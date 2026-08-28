"""
BuildSense — Calendar Computation Tool
BuildSense Tool

Tool that takes the scheduling agent's 48-day phase timeline and maps it to
actual real-world dates, avoiding Sundays and injecting Indian public holidays.

Generates a structured calendar output that the Dashboard UI can render.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

WORK_START_TIME = "09:00"
WORK_END_TIME = "17:00"
WORKING_DAY_MINUTES = 8 * 60
DEFAULT_LOCATION = "Construction Site"

# Indian Public Holidays for 2026 (Gazetted)
INDIAN_HOLIDAYS_2026 = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-04-03": "Good Friday",
    "2026-04-20": "Id-ul-Fitr (Tentative)",
    "2026-08-15": "Independence Day",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-19": "Dussehra",
    "2026-11-08": "Diwali",
    "2026-12-25": "Christmas Day"
}

def _add_months_clamped(start_date, months):
    """Calendar-accurate month addition (mirrors the server-side helper).

    Real calendar month arithmetic — never a fixed 30-day month:
    February, leap years, differing month lengths and year changes are
    handled via calendar.monthrange; month-end starts clamp to the target
    month's last day.
    """
    total_month_index = start_date.month - 1 + int(months)
    year = start_date.year + total_month_index // 12
    month = total_month_index % 12 + 1
    day = min(start_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def _is_working_day(day_value):
    """Working day = any day except Sundays and the gazetted holidays."""
    return (
        day_value.weekday() != 6
        and day_value.isoformat() not in INDIAN_HOLIDAYS_2026
    )


def _count_working_days(start_day, end_day):
    """Number of working days in the inclusive date range."""
    count = 0
    cursor = start_day
    while cursor <= end_day:
        if _is_working_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def _largest_remainder(total, weights):
    """Split `total` integer units across weights deterministically.

    Uses the largest-remainder method with a stable index tie-break so the
    same inputs always produce the same distribution.
    """
    clean = [max(0.0, float(w)) for w in weights]
    weight_sum = sum(clean)
    if weight_sum <= 0 or total <= 0:
        return [0] * len(clean)
    raw = [total * w / weight_sum for w in clean]
    parts = [int(r) for r in raw]
    remainder = total - sum(parts)
    order = sorted(
        range(len(raw)),
        key=lambda i: (-(raw[i] - parts[i]), i),
    )
    for i in order[:remainder]:
        parts[i] += 1
    return parts


def redistribute_phase_durations(timeline: List[dict], target_days: int) -> tuple:
    """Constraint-based redistribution of a blueprint-derived timeline.

    This is scheduling logic, NOT a lookup of fixed per-duration plans:
    the phases/tasks/dependencies always come from the blueprint analysis —
    only their working-day allocations are re-derived so the REAL activities
    fit inside the selected project window.

    - Expansion (target >= baseline): slack working days are distributed
      across phases weighted by (base duration + 2 x task count), so
      task-rich and detailed phases absorb relatively more time than short
      mobilisation phases. The result is NOT a uniform proportional stretch.
    - Compression (target < baseline): every phase is scaled down but keeps
      a floor of at least 1 working day; overflow is then shaved from the
      currently largest phases until the plan fits. If even the all-1-day
      floors cannot fit, `fits` is False and the caller reports an honest
      overrun instead of silently dropping phases.

    Returns (adjusted_durations, fits_within_target).
    """
    n = len(timeline)
    base = []
    for phase in timeline:
        try:
            base.append(max(0, int(phase.get("duration_days", 0) or 0)))
        except (TypeError, ValueError):
            base.append(0)

    baseline_total = sum(base)
    target_days = max(0, int(target_days))

    if baseline_total == 0 or target_days == 0:
        return list(base), baseline_total <= target_days

    if baseline_total == target_days:
        return list(base), True

    adjusted = [0] * n
    if target_days > baseline_total:
        # ── Expansion: weighted slack distribution (non-uniform) ────────
        weights = [
            b + 2 * len(phase.get("tasks") or [])
            for b, phase in zip(base, timeline)
        ]
        extras = _largest_remainder(target_days - baseline_total, weights)
        adjusted = [b + e for b, e in zip(base, extras)]
        return adjusted, True

    # ── Compression: scaled with a 1-working-day floor per phase ────────
    scale = target_days / baseline_total
    for i, b in enumerate(base):
        if b == 0:
            adjusted[i] = 0
        else:
            adjusted[i] = min(b, max(1, int(b * scale)))

    # Shave overflow from the currently largest phases (deterministic).
    guard = 0
    while sum(adjusted) > target_days and guard < 10000:
        guard += 1
        candidates = [
            i for i in range(n)
            if base[i] > 0 and adjusted[i] > 1
        ]
        if not candidates:
            break
        largest = max(candidates, key=lambda i: (adjusted[i], -i))
        adjusted[largest] -= 1

    # Rounding floors may undershoot the target — hand the surplus back to
    # the phases with the largest base durations so the compressed plan
    # still uses the working days the window provides.
    surplus = target_days - sum(adjusted)
    if surplus > 0:
        weights = [b for b in base]
        extras = _largest_remainder(surplus, weights)
        adjusted = [a + e for a, e in zip(adjusted, extras)]

    return adjusted, sum(adjusted) <= target_days


def generate_project_calendar(
    timeline: List[dict],
    start_date_str: str,
    workforce_matches: List[dict] = None,
    duration_months: int = None,
    workers: int = None,
    reference_workers: int = None,
) -> dict:
    """
    Maps a linear project timeline to real calendar dates, accounting for
    working days (Mon-Sat) and Indian public holidays. Incorporates workforce assignments.

    When `duration_months` is provided it becomes a HARD project constraint:
    the blueprint-derived phases are re-distributed (never re-invented) so
    the actual construction activities fit the selected window. The crew
    size can pace the plan within bounded limits (±20%) when the blueprint's
    reference crew size is known. Feasibility is reported honestly — a
    compressed or impossible schedule is flagged, never silently produced.

    Args:
        timeline: List of phase dicts from the Scheduling Agent output.
        start_date_str: ISO format date string "YYYY-MM-DD" for project start.
        workforce_matches: List of assigned contractors and their trades.
        duration_months: Selected project duration in calendar months (1-12).
        workers: Crew size selected by the user.
        reference_workers: Blueprint-derived minimum crew size (if known).

    Returns:
        dict with detailed calendar mapping and daily schedules.
    """
    try:
        current_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"error": "Invalid start_date format. Use YYYY-MM-DD."}

    calendar_events = []
    milestones = []
    holidays_encountered = []
    daily_schedules = {}
    total_working_days = 0
    total_calendar_days = 0
    
    # Helper to map phase to an enrolled contractor based on trade.
    # No crew is invented: coworkers stay empty until a real crew-assignment
    # feature exists, and contractor matches come only from the workforce
    # directory results passed in by the caller.
    def get_assigned_contractor_and_workers(phase_name):
        contractor = None
        trade_keyword = ""

        phase_lower = phase_name.lower()
        if "demolition" in phase_lower or "masonry" in phase_lower or "structural" in phase_lower:
            trade_keyword = "Masonry & Brickwork"
        elif "electrical" in phase_lower or "plumbing" in phase_lower:
            trade_keyword = "Electrical & Plumbing"
        elif "plastering" in phase_lower or "drywall" in phase_lower:
            trade_keyword = "Drywall & Ceiling Work"
        elif "tiling" in phase_lower or "flooring" in phase_lower:
            trade_keyword = "Tiling & Flooring Work"
        elif "painting" in phase_lower or "fixtures" in phase_lower:
            trade_keyword = "Painting, Fixtures & Clean-up"

        if workforce_matches:
            for match in workforce_matches:
                if trade_keyword and match.get("trade_category") == trade_keyword:
                    matched = match.get("matched_contractor")
                    if matched:
                        contractor = matched
                    break

        return {
            "contractor_name": contractor,
            "contractor_id": None,
            "coworkers": []
        }
    
    start_date_copy = current_date

    # ── Duration constraint: re-derive phase allocations ──────────────────
    # The selected duration is a hard planning window, not a display range:
    # the blueprint's real phases are redistributed to fit it (or honestly
    # flagged when they cannot). Without a duration the stored timeline is
    # mapped exactly as before.
    schedule_feasibility = None
    planned_durations = None
    project_end_target = None

    if duration_months is not None:
        try:
            duration_months_val = int(duration_months)
        except (TypeError, ValueError):
            return {"error": "duration_months must be an integer (1-12)."}
        if not 1 <= duration_months_val <= 12:
            return {"error": "duration_months must be between 1 and 12."}

        project_end_target = _add_months_clamped(current_date, duration_months_val)
        available_working_days = _count_working_days(
            current_date, project_end_target,
        )

        baseline_working_days = 0
        for phase in timeline:
            try:
                baseline_working_days += max(
                    0, int(phase.get("duration_days", 0) or 0),
                )
            except (TypeError, ValueError):
                continue

        # Crew pacing: more workers than the blueprint reference can compress
        # the plan modestly; fewer stretch it — bounded to ±20% so the plan
        # never assumes unrealistic parallel work.
        pace_factor = 1.0
        if (
            isinstance(workers, int) and not isinstance(workers, bool)
            and isinstance(reference_workers, int) and not isinstance(reference_workers, bool)
            and workers >= 1 and reference_workers >= 1
            and workers != reference_workers
        ):
            pace_factor = min(1.2, max(0.8, reference_workers / workers))
        required_working_days = max(1, round(baseline_working_days * pace_factor))

        if available_working_days <= 0:
            planned_durations = [
                max(1, int(p.get("duration_days", 1) or 1)) for p in timeline
            ]
            schedule_feasibility = {
                "status": "overrun",
                "message": (
                    f"The selected {duration_months_val}-month window contains no "
                    f"working days, so the plan cannot fit inside it."
                ),
                "blueprint_working_days": baseline_working_days,
                "crew_adjusted_working_days": required_working_days,
                "available_working_days": available_working_days,
            }
        elif baseline_working_days == 0:
            planned_durations = None
            schedule_feasibility = {
                "status": "ok",
                "message": "The stored project timeline has no phase durations to distribute.",
                "blueprint_working_days": 0,
                "crew_adjusted_working_days": 0,
                "available_working_days": available_working_days,
            }
        else:
            # The selected window is filled with the REAL blueprint
            # activities (never left mostly empty); when the crew-adjusted
            # requirement exceeds the window, phases compress down to what
            # fits. Crew pacing decides which side of that boundary the
            # project lands on — a larger crew can turn a compressed plan
            # into one that fits comfortably.
            target_days = available_working_days
            planned_durations, fits = redistribute_phase_durations(
                timeline, target_days,
            )

            ratio = required_working_days / available_working_days
            if not fits:
                status = "overrun"
                message = (
                    f"Warning: the selected {duration_months_val}-month duration is too "
                    f"short for this project scope — even a minimal schedule needs "
                    f"{required_working_days} working day(s) but only "
                    f"{available_working_days} fit inside the window. The plan below "
                    f"runs past the requested end date."
                )
            elif ratio > 1.5:
                status = "highly_compressed"
                message = (
                    f"Warning: the selected {duration_months_val}-month duration is highly "
                    f"compressed for the identified scope (~{required_working_days} working "
                    f"day(s) of planned work vs {available_working_days} available at the "
                    f"current crew size)."
                )
            elif ratio > 1.05:
                status = "tight"
                message = (
                    f"The {duration_months_val}-month window is tight: ~{required_working_days} "
                    f"working day(s) of planned work vs {available_working_days} available."
                )
            elif required_working_days < available_working_days * 0.75:
                status = "expanded"
                message = (
                    f"The blueprint activities were distributed across the full "
                    f"{duration_months_val}-month window ({available_working_days} "
                    f"working day(s); ~{required_working_days} needed at the "
                    f"selected crew size)."
                )
            else:
                status = "ok"
                message = (
                    f"The blueprint plan fits the selected {duration_months_val}-month "
                    f"window ({required_working_days} of {available_working_days} working days)."
                )

            schedule_feasibility = {
                "status": status,
                "message": message,
                "blueprint_working_days": baseline_working_days,
                "crew_adjusted_working_days": required_working_days,
                "available_working_days": available_working_days,
            }

    effective_timeline = timeline
    if planned_durations is not None:
        effective_timeline = []
        for phase, adjusted_days in zip(timeline, planned_durations):
            scoped_phase = dict(phase)
            scoped_phase["duration_days"] = adjusted_days
            effective_timeline.append(scoped_phase)

    for phase in effective_timeline:
        duration = phase.get("duration_days", 0)
        phase_start = current_date
        days_worked = 0
        
        assignment = get_assigned_contractor_and_workers(phase.get("phase", ""))
        phase_name = phase.get("phase", "Unknown Phase")
        tasks = phase.get("tasks", [])
        location = phase.get("location", DEFAULT_LOCATION)
        description = phase.get("description", "; ".join(tasks) if tasks else f"Planned work for {phase_name}")
        
        while days_worked < duration:
            date_str = current_date.strftime("%Y-%m-%d")
            
            # Check for holidays and Sundays
            if date_str in INDIAN_HOLIDAYS_2026:
                if {"date": date_str, "name": INDIAN_HOLIDAYS_2026[date_str]} not in holidays_encountered:
                    holidays_encountered.append({"date": date_str, "name": INDIAN_HOLIDAYS_2026[date_str]})
            elif current_date.weekday() == 6:
                # Sunday - skip
                pass
            else:
                if days_worked == 0:
                    phase_start = current_date
                days_worked += 1
                total_working_days += 1
                
                # Add to daily schedules
                if date_str not in daily_schedules:
                    daily_schedules[date_str] = []
                
                daily_schedules[date_str].append({
                    "date": date_str,
                    "phase": phase_name,
                    "start_time": WORK_START_TIME,
                    "end_time": WORK_END_TIME,
                    "duration_minutes": WORKING_DAY_MINUTES,
                    "status": "planned",
                    "contractor": assignment["contractor_name"],
                    "contractor_id": assignment["contractor_id"],
                    "coworkers": assignment["coworkers"],
                    "tasks": tasks,
                    "location": location
                })
            
            current_date += timedelta(days=1)
            total_calendar_days += 1

        phase_end = current_date - timedelta(days=1)
        
        calendar_events.append({
            "title": phase.get("title", phase_name),
            "phase": phase_name,
            "start_date": phase_start.strftime("%Y-%m-%d"),
            "end_date": phase_end.strftime("%Y-%m-%d"),
            "start_time": WORK_START_TIME,
            "end_time": WORK_END_TIME,
            "duration_minutes": duration * WORKING_DAY_MINUTES,
            "duration_days": duration,
            "working_days": duration,
            "tasks": tasks,
            "assignment": assignment,
            "status": "planned",
            "priority": phase.get("priority", "medium"),
            "location": location,
            "dependencies": phase.get("dependencies", []),
            "description": description
        })
        
        milestones.append({
            "date": phase_end.strftime("%Y-%m-%d"),
            "title": phase.get("milestone", f"Completed {phase.get('phase')}")
        })

    end_date = current_date - timedelta(days=1)

    return {
        "project_start": start_date_copy.strftime("%Y-%m-%d"),
        "project_end": end_date.strftime("%Y-%m-%d"),
        "requested_duration_months": duration_months,
        "planned_end_target": (
            project_end_target.isoformat() if project_end_target else None
        ),
        "schedule_feasibility": schedule_feasibility,
        "total_working_days": total_working_days,
        "total_calendar_days": (end_date - start_date_copy).days + 1,
        "events": calendar_events,
        "holidays": holidays_encountered,
        "milestones": milestones,
        "daily_schedules": daily_schedules
    }
