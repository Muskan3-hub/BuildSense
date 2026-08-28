"""
BuildSense — Workforce Matching Agent

Matches required trades against the contractor directory stored in the
SQLite database (``contractors`` table, managed via /api/contractors).

If no contractors are enrolled the agent reports that honestly instead of
inventing crews, ratings, availability, or conflicts.
"""

import json
import math
import logging
from datetime import date as _date, datetime, timezone

from agents.config import get_llm, is_live_mode, extract_text
from agents.database import db

logger = logging.getLogger(__name__)


# ── Duration-specific workforce modelling ────────────────────────────────────
# Estimation-only modelling assumptions — documented and uniform, never
# per-project quotas or hardcoded worker counts:
AVG_WORKING_DAYS_PER_MONTH = 26      # Mon-Sat site week (estimate when the
                                     # exact start date is not known yet)
SPACE_SQFT_PER_CONCURRENT_WORKER = 100   # practical on-site space limit
MIN_VIABLE_CREW_FACTOR = 0.5         # below ~half the reference crew even an
                                     # unlimited schedule stalls: baseline
                                     # trades must be present simultaneously

# Phases whose duration is dominated by physical waiting rather than labour
# (curing/drying/inspection holds). Their days cannot be productively
# compressed by adding crew.
WAIT_PHASE_TOKENS = (
    "curing", "cure", "drying", "dry out", "set", "setting",
    "inspection", "waiting", "maturity", "settling",
)


def _phase_is_waiting(phase):
    text = " ".join([
        str(phase.get("phase", "")),
        str(phase.get("description", "")),
        " ".join(str(t) for t in (phase.get("tasks") or [])),
    ]).lower()
    return any(token in text for token in WAIT_PHASE_TOKENS)


def analyse_project_profile(spatial_data=None, timeline=None,
                            reference_minimum=None):
    """Structured project context used for duration-specific crew sizing.

    All inputs come from the blueprint's own Gemini-driven analysis:
    - ``timeline`` is the stored project schedule (Scheduling Agent phases).
    - ``reference_minimum`` is the blueprint-specific reference crew (the
      existing ``estimate_minimum_workers`` result).

    Returns a dict with:
        reference_workers     C0 — crew that executes the plan at its
                              natural pace
        planned_working_days  R0 — total phase working days at that pace
        waiting_working_days  W0 — R0 subset dominated by curing/waiting
        total_area_sqft       analysed floor area (or None)
        phase_count           number of scheduled phases
    """
    phases = [p for p in (timeline or []) if isinstance(p, dict)]
    planned_days = 0
    waiting_days = 0
    for phase in phases:
        try:
            days = max(0, int(phase.get("duration_days", 0) or 0))
        except (TypeError, ValueError):
            continue
        planned_days += days
        if _phase_is_waiting(phase):
            waiting_days += days

    total_area = None
    raw_area = (spatial_data or {}).get("total_area_sqft")
    if isinstance(raw_area, (int, float)) and raw_area > 0:
        total_area = float(raw_area)
    if total_area is None:
        summed = sum(
            (r.get("area_sqft") or 0)
            for r in ((spatial_data or {}).get("rooms") or [])
            if isinstance(r, dict) and isinstance(r.get("area_sqft"), (int, float))
        )
        if summed > 0:
            total_area = float(summed)

    reference_workers = None
    ref_raw = (reference_minimum or {}).get("minimum_workers") \
        if isinstance(reference_minimum, dict) else None
    if isinstance(ref_raw, int) and not isinstance(ref_raw, bool) and ref_raw >= 1:
        reference_workers = int(ref_raw)

    return {
        "reference_workers": reference_workers,
        "planned_working_days": planned_days,
        "waiting_working_days": min(waiting_days, planned_days),
        "total_area_sqft": total_area,
        "phase_count": len(phases),
    }


def available_working_days_for_duration(duration_months, start_date=None):
    """Working days inside the selected window.

    Exact calendar arithmetic (real month boundaries, Sundays and gazetted
    holidays off) when a start date is known; otherwise a documented
    per-month estimate used ONLY for pre-generation sizing hints.
    """
    months = int(duration_months)
    if start_date:
        try:
            start = _date.fromisoformat(str(start_date))
        except ValueError:
            start = None
        if start is not None:
            from agents.tools.calendar_engine import (
                _add_months_clamped, _count_working_days,
            )
            return _count_working_days(start, _add_months_clamped(start, months))
    return AVG_WORKING_DAYS_PER_MONTH * months


def minimum_workers_for_duration(profile, duration_months, start_date=None):
    """Duration-specific minimum crew from work-capacity scheduling math.

    NOT ``workers / months``: the calculation uses the blueprint's own
    analysed workload (person-days of productive work), the working days
    actually available inside the selected calendar window, and the fixed
    calendar cost of curing/waiting periods that crew size cannot compress:

        needed = ceil(productive_person_days / productive_available_days)

    - Shorter windows shrink the productive days available, so the same
      project needs more simultaneous workers; longer windows spread the
      identical workload across more days, lowering the minimum.
    - The result is floored at the smallest viable site crew (dependencies
      mean some trades must always be present) and capped by the maximum
      useful concurrent workforce (site space/equipment limits). When the
      required crew exceeds that cap the duration is reported INFEASIBLE
      instead of inventing an unrealistic number.

    Returns:
        {"feasible": bool, "minimum_workers": int|None, "reason": str|None,
         "basis": {...}}
    """
    reference_workers = (profile or {}).get("reference_workers")
    planned_days = (profile or {}).get("planned_working_days") or 0
    waiting_days = (profile or {}).get("waiting_working_days") or 0
    total_area = (profile or {}).get("total_area_sqft")

    months = int(duration_months)
    if not isinstance(reference_workers, int) or reference_workers < 1 \
            or planned_days < 1:
        return {
            "feasible": True,
            "minimum_workers": None,
            "reason": "Not enough blueprint analysis yet to size the workforce.",
            "basis": {},
        }

    available_days = available_working_days_for_duration(months, start_date)
    productive_days_required = max(1, planned_days - waiting_days)

    # In a sequential dependency chain, curing/drying/inspection periods
    # consume FIXED calendar time regardless of crew size — only the
    # remaining working days in the window can absorb productive work.
    productive_available_days = available_days - waiting_days

    # Smallest viable crew: dependencies require baseline trades on site no
    # matter how long the schedule runs (minimum 3 workers for structural projects).
    floor_crew = max(3, math.ceil(reference_workers * MIN_VIABLE_CREW_FACTOR))

    # Maximum useful crew: limited by analysed site area (when known) and a
    # bounded multiple of the reference crew — adding people beyond this
    # cannot speed real construction up.
    if total_area and total_area > 0:
        area_cap = math.ceil(total_area / SPACE_SQFT_PER_CONCURRENT_WORKER)
    else:
        area_cap = 0
    ceiling_crew = max(area_cap, math.ceil(reference_workers * 2), 8)

    basis = {
        "reference_workers": reference_workers,
        "planned_working_days": planned_days,
        "waiting_working_days": waiting_days,
        "available_working_days": available_days,
        "productive_available_days": productive_available_days,
        "productive_person_days": productive_days_required * reference_workers,
        "floor_crew": floor_crew,
        "ceiling_crew": ceiling_crew,
        "total_area_sqft": total_area,
    }

    if productive_available_days < 1:
        return {
            "feasible": False,
            "minimum_workers": None,
            "reason": (
                f"The selected {months}-month duration may not be realistically "
                f"achievable for this project: curing and inspection holds "
                f"alone (~{waiting_days} working day(s)) fill the entire window."
            ),
            "basis": basis,
        }

    person_days = productive_days_required * reference_workers
    raw_needed = math.ceil(person_days / float(productive_available_days))

    if raw_needed > ceiling_crew:
        return {
            "feasible": False,
            "minimum_workers": None,
            "reason": (
                f"The selected {months}-month duration may not be realistically "
                f"achievable for this project because of task dependencies and "
                f"on-site capacity limits (~{raw_needed} workers would be needed "
                f"but at most ~{ceiling_crew} can work this site effectively)."
            ),
            "basis": basis,
        }

    minimum = max(floor_crew, raw_needed)
    return {
        "feasible": True,
        "minimum_workers": int(minimum),
        "reason": None,
        "basis": basis,
    }


# ── Blueprint-specific minimum workforce requirement ─────────────────────────
# Structured data (NOT parsed from any conversational answer): Gemini analyses
# the actual blueprint and returns {"minimum_workers": N}. In offline/mock mode
# a deterministic estimate derived from the analysed blueprint's own floor area
# is used instead of inventing an arbitrary default.
def estimate_minimum_workers(spatial_data=None, blueprint_context=None, query=None):
    """
    Determine the blueprint-specific minimum recommended workforce.

    Order of authority:
      1. Live mode → Gemini receives the blueprint facts (structured spatial
         data plus, when available, its own question-focused image analysis)
         and must return strict JSON: {"minimum_workers": <int>}.
      2. Offline/mock mode (or unusable Gemini response) → deterministic
         estimate computed from the blueprint analysis itself (~1 worker per
         200 sq ft of analysed floor area). No random or fixed default.

    Returns:
        dict: {"minimum_workers": int|None, "basis": str}
              minimum_workers is None only when no blueprint data exists.
    """
    rooms = (spatial_data or {}).get("rooms") or []
    total_area = None
    raw_area = (spatial_data or {}).get("total_area_sqft")
    if isinstance(raw_area, (int, float)) and raw_area > 0:
        total_area = float(raw_area)

    # No analysed blueprint yet -> never invent a requirement (no default).
    if total_area is None and not rooms:
        return {"minimum_workers": None, "basis": "No blueprint analysis available yet."}

    if is_live_mode():
        try:
            llm = get_llm()
            if llm is not None:
                context = {
                    "total_area_sqft": total_area,
                    "room_count": len(rooms),
                    "rooms": [
                        {k: r.get(k) for k in ("type", "label", "width_ft", "height_ft") if isinstance(r, dict) and r.get(k) is not None}
                        for r in rooms[:20]
                    ],
                }
                prompt = f"""You are a senior construction planner. Based ONLY on the supplied
blueprint information, determine the minimum number of workers required on
site to execute this project safely and effectively.

Blueprint facts (authoritative — from the uploaded drawing):
{json.dumps(context, indent=2)}

{("Gemini's detailed blueprint analysis:" + chr(10) + str(blueprint_context)[:4000]) if blueprint_context else ""}

User question: {query or "How many workers do I need?"}

Respond with ONLY valid JSON in exactly this shape:
{{"minimum_workers": <positive integer>, "basis": "<one short sentence explaining the derivation from the blueprint>"}}

Do not invent areas or rooms that are not listed. Return ONLY the JSON."""
                response = llm.invoke(prompt)
                text = extract_text(response.content).strip()
                if text.startswith("```"):
                    first_newline = text.find("\n")
                    if first_newline != -1:
                        text = text[first_newline + 1:]
                    text = text.rstrip().rstrip("`").strip()
                parsed = {}
                stripped = text.strip()
                if stripped.startswith("{"):
                    try:
                        parsed, _ = json.JSONDecoder().raw_decode(stripped)
                    except json.JSONDecodeError:
                        parsed = {}
                value = parsed.get("minimum_workers")
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1:
                    basis = str(parsed.get("basis") or "").strip() or (
                        "Determined by Gemini from the uploaded blueprint."
                    )
                    return {"minimum_workers": int(value), "basis": basis}
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("Workforce minimum estimation via LLM failed: %s. Using blueprint-area estimate.", exc)

    # Deterministic fallback derived from the ACTUAL analysed blueprint.
    area_basis = total_area if total_area else sum(
        (r.get("area_sqft") or 0) for r in rooms if isinstance(r, dict)
    )
    estimated = max(4, math.ceil(float(area_basis) / 200.0))
    return {
        "minimum_workers": int(estimated),
        "basis": (
            f"Estimated from the analysed blueprint floor area "
            f"({float(area_basis):.0f} sq ft at ~1 worker per 200 sq ft)."
        ),
    }


def match_workforce(required_trades, query=None):
    """
    Matches required trades to enrolled contractors from the database directory.
    """
    directory = db.list_contractors()

    if is_live_mode() and directory:
        llm = get_llm()
        prompt = f"""
You are an expert Construction Labor Coordinator and Resource Manager.
Analyze the following required trades and match them against the enrolled
regional contractor directory.

Required Trades:
{json.dumps(required_trades, indent=2)}

Enrolled Contractor Directory:
{json.dumps(directory, indent=2)}

Match each required trade category to the best candidate from the directory, ranking them.
Flag any scheduling availability conflicts based ONLY on the status/notes fields present.

Provide your response as a JSON object matching this schema:
{{
  "matches": [
    {{
      "trade_category": "Trade Name",
      "matched_contractor": "Contractor Name",
      "rating": rating_float_or_null,
      "daily_rate_inr": rate_number_or_null,
      "status": "Available or Conflicted",
      "conflict_details": "Explanation of conflict if any, else empty",
      "match_justification": "Why this candidate fits this task"
    }}
  ],
  "workforce_summary": "Summary assessment of labor availability, costs, and key risks."
}}

CRITICAL RULES:
- Only use contractors from the provided directory. Never invent names, ratings, rates, or conflicts.
- Return ONLY valid JSON. No markdown code blocks, no backticks.
"""
        try:
            response = llm.invoke(prompt)
            from agents.config import extract_text
            text = extract_text(response.content).strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except Exception as e:
            logger.warning("Error in Workforce LLM matching: %s. Falling back to rule-based matching.", e)

    # ── Rule-based matching against the real directory ────────────────────────
    matches = []
    for trade in required_trades:
        matched_crew = None
        trade_lower = trade.lower()
        for crew in directory:
            crew_trade = str(crew.get("trade", "")).lower()
            if trade_lower in crew_trade or crew_trade in trade_lower:
                matched_crew = crew
                break

        if matched_crew:
            status = "Available"
            conflict_details = ""
            notes = str(matched_crew.get("notes") or "").strip()
            if str(matched_crew.get("status", "")).lower() not in ("available", ""):
                status = "Conflicted"
                conflict_details = (
                    f"{matched_crew.get('name')} is currently marked "
                    f"'{matched_crew.get('status')}'."
                    + (f" {notes}" if notes else "")
                )

            matches.append({
                "trade_category": trade,
                "matched_contractor": matched_crew.get("name"),
                "rating": matched_crew.get("rating"),
                "daily_rate_inr": matched_crew.get("daily_rate_inr"),
                "status": status,
                "conflict_details": conflict_details,
                "match_justification": (
                    f"Trade match with enrolled contractor "
                    f"'{matched_crew.get('name')}' "
                    f"(capacity: {matched_crew.get('capacity_workers', 0)} workers"
                    f"{f', located in {matched_crew.get('location')}' if matched_crew.get('location') else ''})."
                )
            })
        else:
            matches.append({
                "trade_category": trade,
                "matched_contractor": None,
                "rating": None,
                "daily_rate_inr": None,
                "status": "Unmatched",
                "conflict_details": "",
                "match_justification": (
                    "No enrolled contractor covers this trade yet. Enroll one via "
                    "the contractor directory."
                )
            })

    matched_count = sum(1 for m in matches if m["matched_contractor"])
    conflicted_count = sum(1 for m in matches if m["status"] == "Conflicted")
    unmatched_count = sum(1 for m in matches if m["status"] == "Unmatched")

    if not directory:
        summary = (
            "No contractors are enrolled in the directory yet, so no workforce "
            "matches can be made. Enroll real contractors via POST /api/contractors "
            "to enable workforce matching."
        )
    else:
        parts = [f"Matched {matched_count} of {len(matches)} required trades from the enrolled directory."]
        if conflicted_count:
            parts.append(f"{conflicted_count} match(es) have an availability conflict recorded in the directory.")
        if unmatched_count:
            parts.append(f"{unmatched_count} trade(s) have no enrolled contractor.")
        summary = " ".join(parts)

    return {
        "matches": matches,
        "workforce_summary": summary,
        "directory_size": len(directory),
    }
