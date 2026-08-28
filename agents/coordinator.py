"""
BuildSense — Coordinator & Decision Agent
Tool Trace Aggregation

The Coordinator:
1. Analyzes the uploaded blueprint.
2. Routes the user query to specialist agents.
3. Stores specialist results in long-term project memory.
4. Generates a project calendar when requested.
5. Aggregates tool execution traces.
6. Detects conflicts.
7. Produces a final explainable recommendation.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from agents.config import get_llm, is_live_mode, invoke_with_retry
from agents.blueprint import analyze_blueprint, analyze_blueprint_for_question
from agents.cost_estimation import estimate_costs
from agents.compliance import check_compliance
from agents.scheduling import generate_schedule
from agents.workforce import (
    match_workforce,
    estimate_minimum_workers,
    analyse_project_profile,
    minimum_workers_for_duration,
)
from agents.interior_design import generate_interior_design
from agents.calendar_service import calendar_service
from agents.calculation_engine import BlueprintCalculationEngine
from agents.memory import (
    SharedMemoryBus,
    conversation_memory,
    knowledge_store,
)


logger = logging.getLogger(__name__)

# Module-level activity store (avoids circular import with app.py)
_agent_activity_store = []
_MAX_ACTIVITIES = 10

# Query tokens that indicate the user is asking about workforce size.
WORKER_QUERY_TOKENS = (
    "worker",
    "workforce",
    "crew",
    "labor",
    "labour",
    "manpower",
    "majdoor",
)


def _record_agent_activity_direct(invoked_agents, user_id=None, conversation_id=None, blueprint_id=None, run_metrics=None):
    """Record agent activities — both in-memory and to DB if user_id provided.

    ``run_metrics`` maps agent name -> {"duration": seconds, "status": str}
    measured during the actual run. When an entry is missing the status is
    recorded as "unknown" rather than fabricated.
    """
    from datetime import datetime, timezone
    agent_action_map = {
        "Blueprint Analysis Agent": "Analyzed blueprint",
        "Cost Estimation Agent": "Generated BOQ",
        "Code Compliance Agent": "Checked NBC requirements",
        "Scheduling Agent": "Generated project timeline",
        "Workforce Agent": "Matched workforce",
        "Interior Design Agent": "Generated design plan",
        "Calendar Engine": "Generated project calendar",
    }
    for agent_name in invoked_agents:
        action = agent_action_map.get(agent_name, "Executed task")
        metrics = (run_metrics or {}).get(agent_name) or {}
        status = metrics.get("status", "unknown")
        duration = metrics.get("duration")
        entry = {
            "agent": agent_name,
            "action": action,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration": round(duration, 3) if isinstance(duration, (int, float)) else None,
        }
        _agent_activity_store.append(entry)
        if len(_agent_activity_store) > _MAX_ACTIVITIES:
            _agent_activity_store.pop(0)
        # Also persist to DB if user_id is available
        if user_id:
            try:
                from agents.database import db
                description = f"{agent_name}: {action}"
                if status not in ("completed", "unknown"):
                    description += f" ({status})"
                db.add_activity(
                    user_id=user_id,
                    activity_type="agent_task",
                    description=description,
                    conversation_id=conversation_id,
                    blueprint_id=blueprint_id,
                )
            except Exception:
                pass  # non-critical


def _run_with_metrics(metrics_store, agent_name, fn, *args, **kwargs):
    """Run a specialist agent, measuring real wall-clock duration and outcome."""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception:
        metrics_store[agent_name] = {
            "duration": round(time.perf_counter() - start, 3),
            "status": "failed",
        }
        raise
    metrics_store[agent_name] = {
        "duration": round(time.perf_counter() - start, 3),
        "status": "completed",
    }
    return result


# Keywords that indicate a follow-up question DEPENDS on the uploaded
# blueprint (measurements, layout, materials, construction details...).
# Such questions MUST be answered from a fresh Gemini analysis of the
# actual blueprint image — never from the text LLM alone.
_BLUEPRINT_DEPENDENT_KEYWORDS = (
    # plan / layout
    "plan", "layout", "floor plan", "blueprint", "drawing", "schematic",
    "arrangement", "adjacent", "next to", "connected",
    # rooms & spaces
    "room", "rooms", "bedroom", "bathroom", "toilet", "kitchen", "hall",
    "living", "dining", "corridor", "passage", "lobby", "balcony",
    "terrace", "garage", "parking", "porch", "store", "pantry",
    # measurements / areas
    "dimension", "size", "area", "sqft", "sq ft", "square feet",
    "square foot", "sqm", "square meter", "metre", "meter", "foot",
    "feet", "measurement", "perimeter", "how big", "how large",
    # construction elements
    "wall", "door", "window", "exit", "entrance", "staircase", "stair",
    "beam", "column", "slab", "roof", "foundation", "structural",
    # estimation / design / materials based on the drawing
    "material", "estimate", "cost", "budget", "quantity", "boq",
    "design", "furnish", "furniture", "renovat", "construct", "build",
    "this house", "this building", "this property", "the uploaded",
    "uploaded blueprint", "my blueprint", "my plan",
)

_QUESTION_CACHE_MAX = 512


def question_requires_blueprint(user_query):
    """
    Return True when the follow-up question depends on the uploaded
    blueprint and therefore requires Gemini blueprint analysis first.

    Questions that clearly don't reference the drawing (e.g. 'What is
    today's date?') keep using the normal chat flow.
    """
    if not user_query or not isinstance(user_query, str):
        return False
    q = user_query.strip().lower()[:_QUESTION_CACHE_MAX]
    if any(k in q for k in _BLUEPRINT_DEPENDENT_KEYWORDS):
        return True
    # Deictic references to an attached/visible document
    return bool(
        __import__("re").search(
            r"\b(this|that|the|my|our|attached|uploaded|given)\b"
            r".*\b(plan|drawing|blueprint|layout|image|file)\b",
            q,
        )
    )


def get_agent_activity():
    """Return recent activities (most recent first)."""
    return list(reversed(_agent_activity_store))


def _resolve_scoped_project_value(user_id, conversation_id, key):
    """Latest structured project value stored for THIS user + conversation.

    Values are stored per user by the pipeline (e.g. `plan_context`,
    `workforce_minimum`) together with the conversation id they belong to.
    A different conversation simply does not match, so nothing carries over
    into new chats; knowledge_store scoping keeps users isolated.
    """
    try:
        entries = knowledge_store.retrieve(
            topic="project", key=key, user_id=user_id,
        )
    except Exception:
        return None
    for entry in reversed(entries):
        value = entry.get("value")
        if not isinstance(value, dict):
            continue
        stored_conv_id = value.get("conversation_id")
        if conversation_id is None:
            # No active conversation context: only a requirement recorded
            # without one may apply.
            if stored_conv_id is None:
                return value
            continue
        if str(stored_conv_id) == str(conversation_id):
            return value
    return None


def run_coordination_pipeline(
    image_path,
    user_query,
    spatial_data=None,
    budget_limit=None,
    site_city="Pune",
    region="Pune",
    style_preset=None,
    start_date=None,
    project_id=None,
    user_id=None,
    conversation_id=None,
    blueprint_id=None,
    plan_duration_months=None,
):
    """
    Execute the BuildSense multi-agent orchestration pipeline.
    """

    from datetime import date as _date
    from agents.tools import tool_registry

    # Default project start to TODAY at run time — never a frozen past date.
    if not start_date or not isinstance(start_date, str):
        start_date = _date.today().isoformat()

    # ── Step 0: Reset per-request state ──────────────────────────
    tool_registry.clear_audit_log()
    # Fresh inter-agent bus for THIS run only: concurrent pipeline runs can
    # never read or overwrite each other's shared context.
    run_memory_bus = SharedMemoryBus()

    # Real per-agent execution metrics (duration/status) for this run
    agent_run_metrics = {}

    # Internal, per-request storage for the question-focused Gemini
    # blueprint analysis. Captured here FIRST and later handed to Groq —
    # never shown to the user directly and never persisted as a duplicate.
    gemini_blueprint_analysis = None

    # ── Step 1: Blueprint Analysis ───────────────────────────────
    if isinstance(spatial_data, dict):
        spatial_data_source = "request"
        logger.info(
            "Coordinator received uploaded spatial_data: rooms=%d, corridors=%d, "
            "total_area_sqft=%r",
            len(spatial_data.get("rooms", [])),
            len(spatial_data.get("corridors", [])),
            spatial_data.get("total_area_sqft"),
        )
    else:
        spatial_data_source = "blueprint_reanalysis"
        logger.info("Coordinator analyzing blueprint for query: %s", image_path)
        if not image_path or not os.path.exists(image_path):
            # Keep the non-image path usable for callers such as
            # CLI/test integrations. This intentionally supplies no rooms,
            # dimensions, or area: it is not a substitute for blueprint
            # evidence and therefore cannot fabricate an estimate.
            spatial_data = {
                "rooms": [],
                "outdoor_spaces": [],
                "corridors": [],
                "exits": [],
                "total_area_sqft": None,
                "area_request": {
                    "required": True,
                    "message": "No blueprint image was supplied for analysis.",
                },
                "raw_analysis": "Blueprint image unavailable.",
                "analysis_engine": "No blueprint image supplied",
            }
            spatial_data_source = "no_image"
            agent_run_metrics["Blueprint Analysis Agent"] = {
                "duration": 0.0,
                "status": "skipped_no_image",
            }
            logger.warning("Coordinator received no readable blueprint image: %s", image_path)
        else:
            try:
                spatial_data = _run_with_metrics(
                    agent_run_metrics,
                    "Blueprint Analysis Agent",
                    analyze_blueprint,
                    image_path,
                )
            except Exception:
                # Re-raise after recording — pipeline surfaces the real error.
                raise
        logger.info(
            "Coordinator received re-analyzed spatial_data: rooms=%d, corridors=%d, "
            "total_area_sqft=%r",
            len(spatial_data.get("rooms", [])),
            len(spatial_data.get("corridors", [])),
            spatial_data.get("total_area_sqft"),
        )

    # ── Step 1a: Gemini-first analysis for blueprint-dependent questions ──
    # Any follow-up question that needs blueprint facts (plan, rooms, areas,
    # dimensions, materials...) MUST be grounded in the ACTUAL uploaded
    # blueprint: Gemini analyses the real image with the question in mind,
    # and that analysis is later passed to Groq for the final answer.
    # The structured spatial_data above (fresh or client-cached) stays as-is
    # for the specialist agents — this pass extracts what THIS question needs.
    if question_requires_blueprint(user_query):
        has_real_image = bool(image_path) and os.path.exists(image_path)
        if has_real_image:
            logger.info(
                "Follow-up requires blueprint context — sending actual blueprint "
                "to Gemini first: %s",
                image_path,
            )
            gemini_blueprint_analysis = _run_with_metrics(
                agent_run_metrics,
                "Gemini Blueprint Analysis",
                analyze_blueprint_for_question,
                image_path,
                user_query,
            )
            logger.info(
                "Gemini blueprint analysis captured (%d chars) — will be passed "
                "to Groq for final answer generation.",
                len(gemini_blueprint_analysis),
            )
            logger.info("=== GEMINI ANALYZED OUTPUT ===\n%s", gemini_blueprint_analysis)
        else:
            agent_run_metrics["Gemini Blueprint Analysis"] = {
                "duration": 0.0,
                "status": "skipped_no_image",
            }
            logger.warning(
                "Question requires blueprint context but no readable blueprint "
                "image is available for this request."
            )

    # ── Step 1b: Deterministic Local Calculations ────────────────
    # Run all room area / unit conversions / perimeter math in Python
    # before any LLM is called. This guarantees 100% accurate metrics
    # are embedded in the final prompt — no LLM arithmetic needed.
    calc_engine = BlueprintCalculationEngine(spatial_data)
    calc_result = calc_engine.compute()

    # Merge back enriched rooms & totals into spatial_data
    spatial_data["rooms"] = calc_result["rooms"]
    spatial_data["outdoor_spaces"] = calc_result["outdoor_spaces"]
    if spatial_data.get("total_area_sqft") is None:
        spatial_data["total_area_sqft"] = calc_result["total_indoor_sqft"]
    spatial_data["_calc_summary"] = calc_result["summary_text"]

    logger.info(
        "Calculation engine: indoor_rooms=%d total_indoor_sqft=%r summary=%s",
        calc_result["room_count"],
        calc_result["total_indoor_sqft"],
        calc_result["summary_text"],
    )

    # ── Step 1c: Structured minimum workforce requirement ─────────
    # When the user asks a worker-related question the requirement is
    # determined from THIS user's blueprint (Gemini-first: Step 1a's
    # analysis of the actual image is authoritative context) BEFORE Groq
    # composes the conversational answer. The structured value — never text
    # parsed out of the final answer — is stored scoped to
    # user + conversation + blueprint so the Project Timeline can enforce
    # workers >= minimum for this project only.
    query_lower = user_query.lower()
    workforce_requirement = None
    if any(token in query_lower for token in WORKER_QUERY_TOKENS):
        try:
            workforce_requirement = estimate_minimum_workers(
                spatial_data,
                blueprint_context=gemini_blueprint_analysis,
                query=user_query,
            )
        except Exception as exc:
            logger.warning("Workforce minimum estimation failed: %s", exc)
            workforce_requirement = None

        min_workers_value = (workforce_requirement or {}).get("minimum_workers")
        if isinstance(min_workers_value, int) and min_workers_value >= 1:
            knowledge_store.store(
                "project",
                "workforce_minimum",
                {
                    "minimum_workers": int(min_workers_value),
                    "conversation_id": conversation_id,
                    "blueprint_id": blueprint_id,
                    "basis": (workforce_requirement or {}).get("basis", ""),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "Gemini Blueprint Analysis",
                user_id=user_id,
            )
            logger.info(
                "Stored structured minimum workforce requirement: %s workers "
                "(conversation=%r, blueprint=%r)",
                min_workers_value,
                conversation_id,
                blueprint_id,
            )
        else:
            workforce_requirement = None

    # ── Step 1c-b: Duration-specific workforce requirement ────────
    # The SAME blueprint needs a different minimum crew depending on how
    # fast the user wants to finish. When the selected duration is known
    # (explicitly from the current Project Timeline selection, or from the
    # plan context stored for THIS conversation), the duration-specific
    # requirement is calculated from the blueprint's own analysed workload
    # and stored as structured data — never parsed out of any answer.
    selected_duration_months = None
    if isinstance(plan_duration_months, int) \
            and not isinstance(plan_duration_months, bool) \
            and 1 <= plan_duration_months <= 12:
        selected_duration_months = int(plan_duration_months)
    if selected_duration_months is None:
        stored_plan_ctx = _resolve_scoped_project_value(
            user_id, conversation_id, "plan_context",
        )
        if isinstance(stored_plan_ctx, dict):
            ctx_months = stored_plan_ctx.get("duration_months")
            if isinstance(ctx_months, int) and not isinstance(ctx_months, bool) \
                    and 1 <= ctx_months <= 12:
                selected_duration_months = ctx_months

    duration_requirement = None
    if selected_duration_months is not None and workforce_requirement:
        try:
            stored_schedule_entries = knowledge_store.retrieve(
                topic="project", key="schedule", user_id=user_id,
            )
            stored_timeline = None
            for entry in reversed(stored_schedule_entries):
                sched_value = entry.get("value") or {}
                if isinstance(sched_value, dict) and sched_value.get("timeline"):
                    stored_timeline = sched_value["timeline"]
                    break

            project_profile = analyse_project_profile(
                spatial_data=spatial_data,
                timeline=stored_timeline,
                reference_minimum=workforce_requirement,
            )
            if project_profile.get("reference_workers") \
                    and project_profile.get("planned_working_days"):
                duration_requirement = minimum_workers_for_duration(
                    project_profile, selected_duration_months,
                )
                knowledge_store.store(
                    "project",
                    "duration_workforce",
                    {
                        "duration_months": selected_duration_months,
                        "feasible": duration_requirement.get("feasible"),
                        "minimum_workers": duration_requirement.get(
                            "minimum_workers",
                        ),
                        "reason": duration_requirement.get("reason"),
                        "basis": duration_requirement.get("basis", {}),
                        "conversation_id": conversation_id,
                        "blueprint_id": blueprint_id,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "Duration Workforce Analysis",
                    user_id=user_id,
                )
                # Give the synthesis model the CURRENT-duration structured
                # answer so follow-up questions reflect the live selection.
                workforce_requirement["duration_months"] = selected_duration_months
                if duration_requirement.get("feasible") is False:
                    workforce_requirement["feasibility_warning"] = \
                        duration_requirement.get("reason")
                elif isinstance(duration_requirement.get("minimum_workers"), int):
                    workforce_requirement["duration_minimum_workers"] = \
                        duration_requirement["minimum_workers"]
        except Exception as exc:
            logger.warning(
                "Duration-specific workforce calculation failed: %s", exc,
            )

    # ── Step 2: Specialist Routing ───────────────────────────────
    invoked_agents = ["Blueprint Analysis Agent"]

    run_cost = any(
        x in query_lower
        for x in [
            "cost",
            "budget",
            "lakh",
            "price",
            "rupee",
            "money",
            "boq",
            "finish",
        ]
    )

    run_compliance = any(
        x in query_lower
        for x in [
            "compliant",
            "compliance",
            "norm",
            "byelaw",
            "code",
            "nbc",
            "safety",
            "fire",
            "exit",
            "width",
            "corridor",
        ]
    )

    run_schedule = any(
        x in query_lower
        for x in [
            "schedule",
            "time",
            "date",
            "duration",
            "phase",
            "timeline",
            "milestone",
            "days",
        ]
    )

    run_workforce = any(
        x in query_lower
        for x in [
            "labor",
            "worker",
            "contractor",
            "thekedar",
            "majdoor",
            "workforce",
            "team",
            "crew",
        ]
    )

    run_design = any(
        x in query_lower
        for x in [
            "design",
            "interior",
            "furniture",
            "decor",
            "color",
            "style",
            "theme",
            "room design",
        ]
    )

    run_calendar = any(
        x in query_lower
        for x in [
            "calendar",
            "schedule view",
            "gantt",
            "dates",
            "holiday",
        ]
    )

    # General query → run all agents.
    if not (
        run_cost
        or run_compliance
        or run_schedule
        or run_workforce
        or run_design
        or run_calendar
    ):
        run_cost = True
        run_compliance = True
        run_schedule = True
        run_workforce = True
        run_design = True
        run_calendar = True

    specialist_outputs = {}
    specialist_status = {}

    resolved_style_preset = style_preset
    if not resolved_style_preset:
        saved_preferences = knowledge_store.retrieve(
            topic="design_preference",
            key="style",
            user_id=user_id,
        )
        if saved_preferences:
            resolved_style_preset = saved_preferences[-1].get("value")
            logger.info(
                "Coordinator restored saved design preference: %s",
                resolved_style_preset,
            )
        else:
            resolved_style_preset = "modern_minimalist"
            logger.info(
                "Coordinator found no saved design preference; using %s",
                resolved_style_preset,
            )
    else:
        logger.info(
            "Coordinator received explicit design preference: %s",
            resolved_style_preset,
        )

    logger.info(
        "Coordinator routing decision: cost=%s compliance=%s scheduling=%s "
        "workforce=%s interior_design=%s calendar=%s",
        run_cost,
        run_compliance,
        run_schedule,
        run_workforce,
        run_design,
        run_calendar,
    )

    # ── Step 3: Execute Specialist Agents ────────────────────────

    if run_cost:
        invoked_agents.append("Cost Estimation Agent")

        specialist_outputs["cost_estimation"] = _run_with_metrics(
            agent_run_metrics,
            "Cost Estimation Agent",
            estimate_costs,
            spatial_data,
            user_query,
            region=region,
        )

    if run_compliance:
        invoked_agents.append("Code Compliance Agent")

        specialist_outputs["code_compliance"] = _run_with_metrics(
            agent_run_metrics,
            "Code Compliance Agent",
            check_compliance,
            spatial_data,
            user_query,
        )
        run_memory_bus.write(
            "compliance_result",
            specialist_outputs["code_compliance"],
        )

    if run_schedule:
        invoked_agents.append("Scheduling Agent")

        specialist_outputs["scheduling"] = _run_with_metrics(
            agent_run_metrics,
            "Scheduling Agent",
            generate_schedule,
            spatial_data,
            user_query,
            city=site_city,
        )

    if run_workforce:
        invoked_agents.append("Workforce Agent")

        required_trades = [
            "Masonry & Brickwork",
            "Electrical & Plumbing",
            "Tiling & Flooring Work",
            "Painting, Fixtures & Clean-up",
        ]

        specialist_outputs["workforce"] = _run_with_metrics(
            agent_run_metrics,
            "Workforce Agent",
            match_workforce,
            required_trades,
            user_query,
        )
        if workforce_requirement and isinstance(specialist_outputs["workforce"], dict):
            # Structured blueprint-specific requirement — reaches the Groq
            # synthesis payload so the natural-language answer can reference it.
            specialist_outputs["workforce"]["workforce_requirement"] = workforce_requirement

    if run_design:
        invoked_agents.append("Interior Design Agent")

        specialist_outputs["interior_design"] = _run_with_metrics(
            agent_run_metrics,
            "Interior Design Agent",
            generate_interior_design,
            spatial_data,
            user_query,
            style_preset=resolved_style_preset,
            memory_bus=run_memory_bus,
        )

        # Store preferred design style.
        knowledge_store.store(
            "design_preference",
            "style",
            resolved_style_preset,
            "Interior Design Agent",
            user_id=user_id,
        )

    # ── Step 3A: Store Specialist Results in Long-Term Memory ────

    for agent_key, output in specialist_outputs.items():
        if isinstance(output, dict):
            specialist_status[agent_key] = {
                "status": "completed",
                "output_keys": sorted(output.keys()),
            }
            logger.info(
                "Coordinator received %s output: keys=%s",
                agent_key,
                ", ".join(sorted(output.keys())),
            )
        else:
            specialist_status[agent_key] = {
                "status": "completed_non_dict",
                "output_type": type(output).__name__,
            }
            logger.warning(
                "Coordinator received non-dict %s output: %s",
                agent_key,
                type(output).__name__,
            )

    logger.info(
        "Coordinator specialist collection complete: %s",
        ", ".join(specialist_outputs.keys()) or "no specialists routed",
    )

    if spatial_data:
        knowledge_store.store(
            "project",
            "blueprint_analysis",
            spatial_data,
            "Blueprint Vision Agent",
            user_id=user_id,
        )

    if "cost_estimation" in specialist_outputs:
        knowledge_store.store(
            "project",
            "cost_estimation",
            specialist_outputs["cost_estimation"],
            "Cost Estimation Agent",
            user_id=user_id,
        )

    if "code_compliance" in specialist_outputs:
        knowledge_store.store(
            "project",
            "compliance",
            specialist_outputs["code_compliance"],
            "Code Compliance Agent",
            user_id=user_id,
        )

    if "scheduling" in specialist_outputs:
        knowledge_store.store(
            "project",
            "schedule",
            specialist_outputs["scheduling"],
            "Scheduling Agent",
            user_id=user_id,
        )

    if "workforce" in specialist_outputs:
        knowledge_store.store(
            "project",
            "workforce",
            specialist_outputs["workforce"],
            "Workforce Agent",
            user_id=user_id,
        )

    if "interior_design" in specialist_outputs:
        knowledge_store.store(
            "project",
            "interior_design",
            specialist_outputs["interior_design"],
            "Interior Design Agent",
            user_id=user_id,
        )

    # ── Step 3B: Calendar Engine ─────────────────────────────────
    #
    # If the user asks for a calendar but did not explicitly ask
    # for a schedule, generate the schedule automatically because
    # the calendar requires timeline information.

    if run_calendar:
        invoked_agents.append("Calendar Engine")

        # Selected duration + crew size act as a hard planning constraint:
        # the Project Timeline stores them per conversation when a plan is
        # generated in the UI, and chat-initiated generation reuses the
        # SAME constraints so both paths produce one consistent schedule.
        plan_context = _resolve_scoped_project_value(
            user_id, conversation_id, "plan_context",
        )
        constraint_duration_months = None
        constraint_workers = None
        if isinstance(plan_context, dict):
            ctx_duration = plan_context.get("duration_months")
            if isinstance(ctx_duration, int) and not isinstance(ctx_duration, bool) \
                    and 1 <= ctx_duration <= 12:
                constraint_duration_months = ctx_duration
            ctx_workers = plan_context.get("workers")
            if isinstance(ctx_workers, int) and not isinstance(ctx_workers, bool) \
                    and ctx_workers >= 1:
                constraint_workers = ctx_workers

        reference_crew = None
        if isinstance(workforce_requirement, dict):
            ref_min = workforce_requirement.get("minimum_workers")
            if isinstance(ref_min, int) and not isinstance(ref_min, bool) and ref_min >= 1:
                reference_crew = ref_min
        if reference_crew is None:
            stored_minimum = _resolve_scoped_project_value(
                user_id, conversation_id, "workforce_minimum",
            )
            if isinstance(stored_minimum, dict):
                ref_min = stored_minimum.get("minimum_workers")
                if isinstance(ref_min, int) and not isinstance(ref_min, bool) and ref_min >= 1:
                    reference_crew = ref_min

        if "scheduling" not in specialist_outputs:
            invoked_agents.append("Scheduling Agent")

            specialist_outputs["scheduling"] = generate_schedule(
                spatial_data,
                user_query,
                city=site_city,
                duration_months=constraint_duration_months,
                workers=constraint_workers,
            )

            # Store the newly generated schedule.
            knowledge_store.store(
                "project",
                "schedule",
                specialist_outputs["scheduling"],
                "Scheduling Agent",
                user_id=user_id,
            )

        timeline = specialist_outputs["scheduling"].get(
            "timeline",
            [],
        )

        workforce_matches = specialist_outputs.get(
            "workforce",
            {},
        ).get(
            "matches",
            [],
        )

        specialist_outputs["calendar"] = tool_registry.invoke(
            "generate_project_calendar",
            timeline=timeline,
            start_date_str=start_date,
            workforce_matches=workforce_matches,
            duration_months=constraint_duration_months,
            workers=constraint_workers,
            reference_workers=reference_crew,
        ).get(
            "output",
            {},
        )

        stored_events = []
        calendar_statuses = []
        daily_schedules = specialist_outputs["calendar"].get(
            "daily_schedules",
            {},
        )
        for phase_event in specialist_outputs["calendar"].get("events", []):
            phase = phase_event.get("phase", "Project phase")
            if not phase_event.get("start_date"):
                logger.warning(
                    "Calendar phase omitted because it has no start date: %s",
                    phase,
                )
                continue
            event_result = calendar_service.create_event(
                {
                    "source_id": (
                        f"project-phase:{phase_event.get('start_date')}:{phase}:"

                        f"{phase_event.get('end_date')}"
                    ),
                    "title": phase_event.get("title", phase),
                    "date": phase_event.get("start_date"),
                    "end_date": phase_event.get("end_date"),
                    "start_time": phase_event.get("start_time", "09:00"),
                    "end_time": phase_event.get("end_time", "17:00"),
                    "duration_minutes": phase_event.get(
                        "duration_minutes",
                        480,
                    ),
                    "description": phase_event.get(
                        "description",
                        "; ".join(phase_event.get("tasks", [])),
                    ),
                    "project_context": user_query,
                    "status": phase_event.get("status", "planned"),
                    "priority": phase_event.get("priority", "medium"),
                    "location": phase_event.get(
                        "location",
                        "Construction Site",
                    ),
                    "dependencies": phase_event.get(
                        "dependencies",
                        [],
                    ),
                    "working_days": phase_event.get(
                        "working_days",
                        phase_event.get("duration_days", 0),
                    ),
                    "tasks": phase_event.get("tasks", []),
                    "assignment": phase_event.get("assignment", {}),
                    "daily_schedule": [
                        schedule
                        for schedules in daily_schedules.values()
                        for schedule in schedules
                        if schedule.get("phase") == phase
                    ],
                }
            )
            stored_events.append(event_result.get("event"))
            calendar_statuses.append(event_result.get("calendar_status"))

        specialist_outputs["calendar"].update(
            {
                "calendar_status": (
                    "external_synced"
                    if calendar_statuses and all(
                        status == "external_synced"
                        for status in calendar_statuses
                    )
                    else "local_fallback"
                ),
                "message": (
                    "External calendar unavailable; event saved locally."
                ),
                "stored_events": stored_events,
            }
        )
        logger.info(
            "Coordinator calendar routing completed: events=%d status=%s",
            len(stored_events),
            specialist_outputs["calendar"]["calendar_status"],
        )

        # Store calendar in long-term memory.
        knowledge_store.store(
            "project",
            "calendar",
            specialist_outputs["calendar"],
            "Calendar Engine",
            user_id=user_id,
        )

    # ── Step 4: Aggregate Tool Execution Trace ──────────────────

    for agent_key, output in specialist_outputs.items():
        if agent_key in specialist_status:
            continue
        if isinstance(output, dict):
            specialist_status[agent_key] = {
                "status": "completed",
                "output_keys": sorted(output.keys()),
            }
            logger.info(
                "Coordinator received %s output: keys=%s",
                agent_key,
                ", ".join(sorted(output.keys())),
            )
        else:
            specialist_status[agent_key] = {
                "status": "completed_non_dict",
                "output_type": type(output).__name__,
            }
            logger.warning(
                "Coordinator received non-dict %s output: %s",
                agent_key,
                type(output).__name__,
            )

    tool_execution_trace = []
    seen_timestamps = set()

    # Collect tool calls returned by individual agents.
    for agent_key, output in specialist_outputs.items():

        if not isinstance(output, dict):
            continue

        for call in output.get("tool_calls", []):

            ts = call.get(
                "timestamp",
                "",
            )

            if ts not in seen_timestamps:
                tool_execution_trace.append(call)
                seen_timestamps.add(ts)

    # Add registry-level session trace.
    for entry in tool_registry.get_session_trace():

        ts = entry.get(
            "timestamp",
            "",
        )

        if ts not in seen_timestamps:
            tool_execution_trace.append(entry)
            seen_timestamps.add(ts)

    # Sort chronologically.
    tool_execution_trace.sort(
        key=lambda e: e.get(
            "timestamp",
            "",
        )
    )

    # ── Step 5: Conflict Detection ───────────────────────────────

    conflicts = []

    if run_cost and budget_limit:

        cost_data = specialist_outputs.get(
            "cost_estimation",
            {},
        )

        est_cost = cost_data.get(
                "total_cost_inr",
        )

        # An absent, evidence-required estimate must never be treated as
        # a numeric zero (or compared against the budget).  Doing so would
        # either crash the coordinator or imply a budget verdict before the
        # blueprint dimensions have been verified.
        try:
            est_cost = float(est_cost)
        except (TypeError, ValueError):
            est_cost = None

        if est_cost is not None and est_cost > budget_limit:
            overrun = est_cost - budget_limit

            conflicts.append(
                f"Budget Overrun: Estimated project cost "
                f"(₹{est_cost / 100000:.2f}L) exceeds limit "
                f"(₹{budget_limit / 100000:.2f}L) by "
                f"₹{overrun / 100000:.2f}L."
            )

    if run_compliance:

        compliance_data = specialist_outputs.get(
            "code_compliance",
            {},
        )

        for check in compliance_data.get(
            "compliance_checks",
            [],
        ):

            if check.get("status") == "FAIL":

                conflicts.append(
                    f"NBC Code Violation: "
                    f"{check.get('rule')} is "
                    f"{check.get('found_value')} "
                    f"(Required: {check.get('required_value')}). "
                    f"Reference: {check.get('nbc_citation')}"
                )

    if run_workforce:

        workforce_data = specialist_outputs.get(
            "workforce",
            {},
        )

        for match in workforce_data.get(
            "matches",
            [],
        ):

            if match.get("status") == "Conflicted":

                conflicts.append(
                    f"Workforce Bottleneck: "
                    f"Matched team "
                    f"'{match.get('matched_contractor')}' "
                    f"is {match.get('status')} "
                    f"({match.get('conflict_details')})"
                )

    # ── Step 6: Synthesised Decision Trail ───────────────────────

    if is_live_mode():

        # Build compact payload — strip large lists that waste tokens
        def _compact(d, max_items=5):
            """Trim list fields to max_items and remove heavy debug keys."""
            if not isinstance(d, dict):
                return d
            out = {}
            for k, v in d.items():
                if k.startswith("_") or k in ("tool_calls", "raw_analysis"):
                    continue
                if isinstance(v, list):
                    out[k] = v[:max_items]
                elif isinstance(v, dict):
                    out[k] = _compact(v, max_items)
                else:
                    out[k] = v
            return out

        budget_str = (
            f"₹{budget_limit / 100000:.2f} Lakh" if budget_limit else "Not specified"
        )
        calc_summary = spatial_data.get("_calc_summary", "")

        # The question-focused Gemini analysis of the ACTUAL uploaded
        # blueprint (internal context — authoritative for drawing facts).
        if gemini_blueprint_analysis:
            gemini_section = f"""
BLUEPRINT ANALYSIS FROM GEMINI (analysis of the actual uploaded blueprint):
This section is AUTHORITATIVE for all blueprint-specific facts (rooms,
dimensions, areas, layout, doors/windows, structural details). Where it and
the conversation disagree, trust this analysis.

{gemini_blueprint_analysis}
"""
        elif question_requires_blueprint(user_query):
            gemini_section = """
BLUEPRINT ANALYSIS FROM GEMINI: UNAVAILABLE — no readable blueprint could be
analysed for this request. Do NOT invent or assume any blueprint-specific
facts; state clearly what cannot be determined from a blueprint.
"""
        else:
            gemini_section = ""

        # Complete specialist payload with full spatial blueprint details
        payload = {
            "spatial_summary": calc_summary,
            "total_area_sqft": spatial_data.get("total_area_sqft"),
            "room_count": len(spatial_data.get("rooms", [])),
            "rooms": spatial_data.get("rooms", []),
            "corridors": spatial_data.get("corridors", []),
            "exits": spatial_data.get("exits", []),
            "structural_notes": spatial_data.get("structural_notes", []),
        }
        if "cost_estimation" in specialist_outputs:
            payload["cost"] = _compact(specialist_outputs["cost_estimation"])
        if "code_compliance" in specialist_outputs:
            payload["compliance"] = _compact(specialist_outputs["code_compliance"])
        if "scheduling" in specialist_outputs:
            payload["schedule"] = _compact(specialist_outputs["scheduling"])
        if "workforce" in specialist_outputs:
            payload["workforce"] = _compact(specialist_outputs["workforce"])
        if "interior_design" in specialist_outputs:
            payload["design"] = _compact(specialist_outputs["interior_design"])

        prompt = f"""You are the BuildSense Coordinator & Decision Agent — the final response generator.

IMPORTANT: All mathematical calculations (room areas, unit conversions, cost totals, \
and area measurements) have already been verified and provided in the payload below. \
Do NOT recalculate, re-derive, or hallucinate any numeric values. Use the provided \
metrics exactly as given to generate your response.

User Query: "{user_query}"
Budget: {budget_str}
{gemini_section}
Pre-Calculated Metrics:
{json.dumps(payload, indent=2)}

Detected Conflicts:
{json.dumps(conflicts)}

Conversation Context:
{conversation_memory.get_context_summary(user_id=user_id)}

ANSWERING RULES:
- Base the answer on the provided blueprint analysis (when present) and the
  verified metrics. Do not invent blueprint information.
- If the blueprint does not contain enough information to answer something,
  clearly say so instead of guessing.
- Do not claim information exists in the blueprint when it does not.
- Provide a useful, direct answer to the user's question.

Write a concise markdown response (max 600 words) structured as:
1. Executive Summary & Verdict
2. Specialist Insights (key numbers only)
3. Conflict Resolution & Trade-offs
4. Actionable Recommendations
"""

        # Dev tracing: prove the Groq request actually carries the Gemini
        # blueprint analysis (requirement: verify, don't assume).
        logger.info(
            "Synthesis prompt prepared for Groq: contains_gemini_analysis=%s "
            "(%d chars), question_requires_blueprint=%s",
            bool(gemini_blueprint_analysis),
            len(gemini_blueprint_analysis) if gemini_blueprint_analysis else 0,
            question_requires_blueprint(user_query),
        )

        try:
            from agents.config import is_groq_available, invoke_groq_with_retry, extract_text
            from langchain_core.messages import HumanMessage as _HM

            if is_groq_available():
                logger.info("Routing text synthesis downstream to Groq...")
                try:
                    synthesis = invoke_groq_with_retry(
                        prompt, temperature=0.3, max_tokens=1500,
                    ).strip()
                except Exception as groq_exc:
                    # Groq failed (missing pkg, rate limit, API error) —
                    # fall back to Gemini for the SAME final answer rather
                    # than dropping to the offline summary. The prompt (with
                    # the embedded Gemini blueprint analysis) is unchanged.
                    logger.error(
                        "Groq synthesis failed (%s); falling back to Gemini.",
                        groq_exc,
                    )
                    response = invoke_with_retry(
                        [_HM(content=prompt)],
                        temperature=0.3,
                        max_tokens=1500,
                    )
                    synthesis = extract_text(response.content).strip()
            else:
                logger.info("GROQ_API_KEY not configured; routing synthesis to Gemini...")
                response = invoke_with_retry(
                    [_HM(content=prompt)],
                    temperature=0.3,
                    max_tokens=1500,
                )
                synthesis = extract_text(response.content).strip()

        except Exception as e:
            logger.error("Coordinator synthesis failed: %s", e)
            synthesis = build_offline_synthesis(
                user_query,
                budget_limit,
                spatial_data,
                specialist_outputs,
                conflicts,
                gemini_blueprint_analysis=gemini_blueprint_analysis,
            )

    else:

        synthesis = build_offline_synthesis(
            user_query,
            budget_limit,
            spatial_data,
            specialist_outputs,
            conflicts,
            gemini_blueprint_analysis=gemini_blueprint_analysis,
        )

    # ── Step 7: Conversation Memory ──────────────────────────────

    conversation_memory.add_turn(
        "user",
        user_query,
        user_id=user_id,
    )

    conversation_memory.add_turn(
        "assistant",
        synthesis,
        metadata={
            "agents": invoked_agents,
        },
        user_id=user_id,
    )

    # ── Step 7B: Record Agent Activity ─────────────────────────
    # Use shared-memory-bus-compatible approach to avoid circular imports.
    # Store activity records so they can be picked up by the Flask app.
    _record_agent_activity_direct(
        invoked_agents,
        user_id=user_id,
        conversation_id=conversation_id,
        blueprint_id=blueprint_id,
        run_metrics=agent_run_metrics,
    )

    # ── Final Pipeline Result ────────────────────────────────────

    return {
        "routing_plan": invoked_agents,
        "spatial_data_source": spatial_data_source,
        "resolved_style_preset": resolved_style_preset,
        "spatial_data": spatial_data,
        "specialist_outputs": specialist_outputs,
        "specialist_status": specialist_status,
        "conflicts_detected": conflicts,
        "tool_execution_trace": tool_execution_trace,
        "synthesized_recommendation": synthesis,
        # Structured blueprint-specific minimum workforce (if this run
        # determined one) — the Project Timeline reads this to enforce the
        # worker minimum without parsing Groq's conversational text.
        "workforce_minimum": (
            {"minimum_workers": workforce_requirement["minimum_workers"]}
            if workforce_requirement else None
        ),
        # Internal trace of the Gemini-first step (for dev verification).
        "blueprint_question_analysis_present": bool(gemini_blueprint_analysis),
    }


def build_offline_synthesis(
    query,
    budget,
    spatial_data,
    specialist_outputs,
    conflicts,
    gemini_blueprint_analysis=None,
):
    """
    Deterministic offline summary used ONLY when no LLM is available or the
    LLM call failed. It aggregates the pipeline's REAL specialist outputs and
    is clearly labeled as a non-AI summary. It never issues verdicts that are
    not directly derived from specialist results, and it never claims a
    blueprint was evaluated when none was supplied.
    """

    conflicts_str = (
        "\n".join(
            f"- {c}"
            for c in conflicts
        )
        if conflicts
        else "- None detected by the agents that ran."
    )

    cost = specialist_outputs.get(
        "cost_estimation",
        {},
    )

    schedule = specialist_outputs.get(
        "scheduling",
        {},
    )

    compliance = specialist_outputs.get(
        "code_compliance",
        {},
    )

    has_blueprint = bool(spatial_data) and any([
        spatial_data.get("rooms"),
        spatial_data.get("corridors"),
        spatial_data.get("exits"),
        spatial_data.get("total_area_sqft"),
    ])

    lines = [
        "### 📋 Summary (offline mode)",
        "",
        "_AI synthesis is unavailable (no Gemini/Groq API key configured, or the "
        "request failed). Below is a deterministic roll-up of the specialist "
        "agents that actually ran — not an AI-generated recommendation._",
        "",
    ]

    if not has_blueprint:
        lines.append(
            "**Note:** No blueprint was analyzed in this run, so room-level "
            "results below may be limited."
        )
        lines.append("")

    if gemini_blueprint_analysis:
        lines.append(
            "### 📐 Blueprint facts (from Gemini analysis of the uploaded blueprint)\n"
        )
        lines.append(str(gemini_blueprint_analysis))
        lines.append("")
    elif question_requires_blueprint(query):
        lines.append(
            "**Note:** This question depends on the blueprint, but no Gemini "
            "blueprint analysis is available for this request — blueprint-specific "
            "details cannot be determined and are not invented here."
        )
        lines.append("")

    if cost:
        lines.append(
            f"- **Estimated cost:** {cost.get('formatted_total_cost', 'N/A')}"
            + (
                " _(indicative baseline-rate estimate)_"
                if cost.get("estimation_mode") == "baseline_rate_card_offline"
                else ""
            )
        )
    else:
        lines.append("- **Estimated cost:** Not computed (cost agent did not run).")

    if schedule:
        lines.append(
            f"- **Timeline:** {schedule.get('total_duration_days', '?')} working days "
            f"across {len(schedule.get('timeline', []))} phases."
        )
    else:
        lines.append("- **Timeline:** Not computed (scheduling agent did not run).")

    # Only report a compliance status when the compliance agent actually ran.
    if compliance:
        if compliance.get("is_overall_compliant") is False:
            lines.append("- **Compliance:** ⚠️ Issues found — see the Code Compliance card.")
        elif compliance.get("is_overall_compliant") is True:
            lines.append("- **Compliance:** ✅ No violations detected by the checks that ran.")
        else:
            lines.append("- **Compliance:** Reviewed with warnings — see the Code Compliance card.")
    else:
        lines.append("- **Compliance:** Not assessed (compliance agent did not run).")

    lines.append("")
    lines.append("### 🤝 Conflicts")
    lines.append(conflicts_str)
    lines.append("")

    from agents.config import is_groq_available

    if is_live_mode() or is_groq_available():
        # Keys ARE configured — the failure was the request itself.
        lines.append(
            "_AI synthesis failed for this request (API error or rate limit), "
            "so this is a deterministic roll-up instead of an AI-generated "
            "recommendation. Please retry the question._"
        )
    else:
        lines.append(
            "Configure `GEMINI_API_KEY` (or `GROQ_API_KEY`) to enable full AI "
            "synthesis of these results."
        )

    return "\n".join(lines)

