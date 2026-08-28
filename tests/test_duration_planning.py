"""Duration-constrained construction planning.

The selected Duration must actually CONTROL the generated plan: the
blueprint-derived phases are redistributed (never re-invented) so the real
activities fit the selected calendar window, and infeasible selections are
flagged honestly. Calendar and Construction Plan render from this single
schedule mapping.
"""

import pytest

from agents.tools.calendar_engine import (
    _add_months_clamped,
    generate_project_calendar,
    redistribute_phase_durations,
)


BLUEPRINT_TIMELINE = [
    {
        "phase": "Demolition & Site Preparation",
        "duration_days": 5,
        "tasks": ["Remove partitions", "Clear debris", "Mark layout lines"],
    },
    {
        "phase": "Structural Framing & Wall Partitions",
        "duration_days": 12,
        "dependencies": ["Demolition & Site Preparation"],
        "tasks": ["Erect masonry walls", "Fix door frames", "Build corridors"],
    },
    {
        "phase": "Electrical Conduiting & Plumbing",
        "duration_days": 8,
        "dependencies": ["Structural Framing & Wall Partitions"],
        "tasks": ["Chase conduits", "Pull cables", "Install pipes"],
    },
    {
        "phase": "Plastering, Drywall & False Ceiling",
        "duration_days": 10,
        "dependencies": ["Electrical Conduiting & Plumbing"],
        "tasks": ["Plaster walls", "Erect ceiling grid", "Apply putty"],
    },
    {
        "phase": "Tiling & Flooring Work",
        "duration_days": 7,
        "dependencies": ["Plastering, Drywall & False Ceiling"],
        "tasks": ["Lay floor tiles", "Tile walls", "Grout and clean"],
    },
    {
        "phase": "Painting, Fixtures & Clean-up",
        "duration_days": 6,
        "dependencies": ["Tiling & Flooring Work"],
        "tasks": ["Paint walls", "Fit switches", "Deep clean"],
    },
]

BASELINE_DAYS = sum(p["duration_days"] for p in BLUEPRINT_TIMELINE)  # 48
START = "2026-08-23"


def _work_dates(result):
    return sorted(result["daily_schedules"].keys())


def test_month_window_uses_real_calendar_arithmetic():
    """+1 month from a month-end start clamps to the target month's last day."""
    assert _add_months_clamped(__import__("datetime").date(2028, 1, 31), 1).isoformat() == "2028-02-29"
    assert _add_months_clamped(__import__("datetime").date(2026, 8, 23), 3).isoformat() == "2026-11-23"
    assert _add_months_clamped(__import__("datetime").date(2026, 11, 23), 2).isoformat() == "2027-01-23"


def test_one_month_plan_is_compressed_and_flagged():
    """A 48-working-day blueprint scope cannot silently fit into 1 month."""
    result = generate_project_calendar(BLUEPRINT_TIMELINE, START, duration_months=1)

    window_end = _add_months_clamped(__import__("datetime").date(2026, 8, 23), 1)
    for date_str in _work_dates(result):
        assert date_str <= window_end.isoformat()

    feasibility = result["schedule_feasibility"]
    assert feasibility["status"] in ("highly_compressed", "tight")
    assert "compressed" in feasibility["message"] or "tight" in feasibility["message"]
    # The real activities were compressed, not dropped: every phase survives.
    assert len(result["events"]) == len(BLUEPRINT_TIMELINE)


def test_duration_matrix_produces_distinct_plans():
    """1/3/6/12 months each produce their own schedule — not stretched copies."""
    plans = {}
    for months in (1, 3, 6, 12):
        result = generate_project_calendar(
            BLUEPRINT_TIMELINE, START, duration_months=months,
        )
        plans[months] = result

        assert result["requested_duration_months"] == months
        assert result["planned_end_target"] == (
            _add_months_clamped(__import__("datetime").date(2026, 8, 23), months).isoformat()
        )
        # Every scheduled work day stays inside the requested window.
        window_end = _add_months_clamped(__import__("datetime").date(2026, 8, 23), months)
        for date_str in _work_dates(result):
            assert date_str <= window_end.isoformat()
        assert result["total_working_days"] == len(_work_dates(result))

    # Longer durations schedule strictly more working days.
    assert plans[1]["total_working_days"] < plans[3]["total_working_days"]
    assert plans[3]["total_working_days"] < plans[6]["total_working_days"]
    assert plans[6]["total_working_days"] < plans[12]["total_working_days"]

    # Phase boundaries genuinely differ between durations (not one fixed
    # plan re-dated): compare each phase's start/end dates across plans.
    def boundaries(result):
        return [(e["phase"], e["start_date"], e["end_date"]) for e in result["events"]]

    assert boundaries(plans[3]) != boundaries(plans[6])
    assert boundaries(plans[6]) != boundaries(plans[12])
    assert boundaries(plans[1]) != boundaries(plans[3])

    # Long plans fill the selected window instead of leaving it empty:
    # the last scheduled day lands near the planned end (≤ ~1 idle week).
    last_work_12 = max(_work_dates(plans[12]))
    target_12 = __import__("datetime").date.fromisoformat(plans[12]["planned_end_target"])
    gap = (target_12 - __import__("datetime").date.fromisoformat(last_work_12)).days
    assert gap <= 7


def test_expansion_is_weighted_not_uniform_stretch():
    """Task-rich phases absorb relatively more slack than lean phases."""
    timeline = [
        {"phase": "Lean Mobilisation", "duration_days": 5, "tasks": ["One task"]},
        {"phase": "Detailed Finishing", "duration_days": 5, "tasks": ["a", "b", "c", "d", "e", "f"]},
    ]
    adjusted, fits = redistribute_phase_durations(timeline, 20)

    assert fits is True
    lean_extra = adjusted[0] - 5
    rich_extra = adjusted[1] - 5
    assert adjusted[0] + adjusted[1] == 20
    assert rich_extra > lean_extra


def test_compression_keeps_floor_and_chronology():
    """Compressed phases never drop below 1 working day and stay ordered."""
    tiny_timeline = [
        {"phase": f"Phase {i}", "duration_days": 4, "tasks": ["t"]}
        for i in range(6)
    ]  # 24 baseline days
    adjusted, fits = redistribute_phase_durations(tiny_timeline, 10)

    assert all(a >= 1 for a in adjusted)
    assert sum(adjusted) == 10
    assert fits is True

    result = generate_project_calendar(tiny_timeline, START, duration_months=1)
    events = result["events"]
    starts = [e["start_date"] for e in events]
    assert starts == sorted(starts)


def test_impossible_window_is_flagged_as_overrun():
    """Even minimal schedules that cannot fit are reported, never faked."""
    heavy_timeline = [
        {"phase": f"Phase {i:02d}", "duration_days": 3, "tasks": ["t"]}
        for i in range(40)
    ]  # 120 baseline days, 40 indivisible phases
    result = generate_project_calendar(heavy_timeline, START, duration_months=1)

    feasibility = result["schedule_feasibility"]
    assert feasibility["status"] == "overrun"
    assert "too short" in feasibility["message"]
    # Every phase still exists — the overrun is honest, nothing vanished.
    assert len(result["events"]) == 40


def test_worker_count_paces_compression_boundary():
    """A larger crew can turn an over-window requirement into a fitting one."""
    # Baseline 30 days vs a ~26-day window: reference crew compresses;
    # double crew (bounded pacing) fits comfortably.
    timeline = [
        {"phase": "A", "duration_days": 10, "tasks": ["a", "b"]},
        {"phase": "B", "duration_days": 10, "tasks": ["a", "b"]},
        {"phase": "C", "duration_days": 10, "tasks": ["a", "b"]},
    ]
    small_crew = generate_project_calendar(
        timeline, START, duration_months=1, workers=10, reference_workers=10,
    )
    big_crew = generate_project_calendar(
        timeline, START, duration_months=1, workers=20, reference_workers=10,
    )

    assert small_crew["schedule_feasibility"]["status"] != "ok"
    assert big_crew["schedule_feasibility"]["status"] in ("ok", "expanded")
    # Crew-adjusted requirement is reported for both.
    assert small_crew["schedule_feasibility"]["crew_adjusted_working_days"] == 30
    assert big_crew["schedule_feasibility"]["crew_adjusted_working_days"] == round(30 * 0.8)


def test_without_duration_behaviour_is_unchanged():
    """No duration selection → legacy sequential mapping of stored phases."""
    result = generate_project_calendar(BLUEPRINT_TIMELINE, START)

    assert result["schedule_feasibility"] is None
    assert result["requested_duration_months"] is None
    assert result["total_working_days"] == BASELINE_DAYS


def test_invalid_duration_values_are_rejected():
    assert "error" in generate_project_calendar(BLUEPRINT_TIMELINE, START, duration_months=13)
    assert "error" in generate_project_calendar(BLUEPRINT_TIMELINE, START, duration_months=0)
    assert "error" in generate_project_calendar(BLUEPRINT_TIMELINE, START, duration_months="abc")


# ── Route-level coverage ─────────────────────────────────────────────────────


class _FakeKnowledgeStore:
    """Per-user knowledge store double scoped exactly like the real one."""

    def __init__(self):
        self._data = {}

    def store(self, topic, key, value, source=None, user_id=None):
        self._data.setdefault((user_id, topic, key), []).append({"value": value})

    def retrieve(self, topic, key, user_id=None):
        return list(self._data.get((user_id, topic, key), []))


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    """Isolated app: temp database, fake knowledge store, logged-in user."""
    import app as buildsense_app
    import agents.auth as auth_module
    from agents.database import Database

    test_db = Database(str(tmp_path / "plan.db"))
    fake_store = _FakeKnowledgeStore()
    monkeypatch.setattr(buildsense_app, "db", test_db)
    monkeypatch.setattr(buildsense_app, "knowledge_store", fake_store)
    monkeypatch.setattr(auth_module, "db", test_db)

    client = buildsense_app.app.test_client()
    reg = client.post(
        "/api/auth/register",
        json={
            "username": "dur_user",
            "password": "secret123",
            "confirm_password": "secret123",
        },
    )
    assert reg.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "dur_user", "password": "secret123"},
    )
    assert login.status_code == 200
    user_id = login.get_json()["user"]["id"]

    yield {
        "client": client,
        "db": test_db,
        "store": fake_store,
        "user_id": user_id,
    }


def _store_schedule(store, user_id):
    store.store(
        "project", "schedule", {"timeline": BLUEPRINT_TIMELINE},
        "Scheduling Agent", user_id=user_id,
    )


def _generate(client, **overrides):
    payload = {
        "start_date": "2026-08-23",
        "duration_months": 3,
        "workers": 17,
    }
    payload.update(overrides)
    return client.post("/api/calendar/generate", json=payload)


def test_generate_route_duration_controls_plan(plan_env):
    env = plan_env
    _store_schedule(env["store"], env["user_id"])

    one = _generate(env["client"], duration_months=1)
    six = _generate(env["client"], duration_months=6)
    assert one.status_code == 200 and six.status_code == 200

    one_body, six_body = one.get_json(), six.get_json()
    assert one_body["working_days"] < six_body["working_days"]
    assert one_body["plan_end"] == "2026-09-23"
    assert six_body["plan_end"] == "2027-02-23"
    assert one_body["feasibility"]["status"] in ("highly_compressed", "tight")
    assert six_body["feasibility"]["status"] in ("ok", "expanded")

    # Persisted calendar events mirror the SAME schedule (single source of truth).
    events = env["db"].get_calendar_events(env["user_id"])
    plan_events = [e for e in events if e["category"] == "construction_plan"]
    assert len(plan_events) == six_body["working_days"]
    event_dates = {e["date"] for e in plan_events}
    assert event_dates == set(
        d["date"] for d in six_body["days"] if d["type"] == "work"
    )


def test_generate_route_regenerates_on_duration_change(plan_env):
    """Changing duration and regenerating replaces the previous plan."""
    env = plan_env
    _store_schedule(env["store"], env["user_id"])

    first = _generate(env["client"], duration_months=3).get_json()
    second = _generate(env["client"], duration_months=12).get_json()

    assert first["working_days"] < second["working_days"]
    assert second["plan_start"] == "2026-08-23"
    assert second["plan_end"] == "2027-08-23"

    events = [e for e in env["db"].get_calendar_events(env["user_id"])
              if e["category"] == "construction_plan"]
    # Old 3-month plan fully replaced by the 12-month plan.
    assert len(events) == second["working_days"]
    assert all(e["date"] >= "2026-08-23" for e in events)


def test_generate_route_minimum_workers_still_enforced(plan_env):
    """Backend enforces the DURATION-SPECIFIC minimum for the current plan."""
    env = plan_env
    _store_schedule(env["store"], env["user_id"])
    env["store"].store(
        "project", "workforce_minimum",
        {"minimum_workers": 17},
        "Workforce Analysis", user_id=env["user_id"],
    )

    # Derive the authoritative expectation from the same structured model.
    from agents.workforce import (
        analyse_project_profile,
        minimum_workers_for_duration,
    )
    profile = analyse_project_profile(
        spatial_data=None,
        timeline=BLUEPRINT_TIMELINE,
        reference_minimum={"minimum_workers": 17},
    )
    requirement = minimum_workers_for_duration(profile, 3, "2026-08-23")
    expected_min = requirement["minimum_workers"]

    blocked = _generate(env["client"], workers=expected_min - 1)
    assert blocked.status_code == 400
    body = blocked.get_json()
    assert body["minimum_workers"] == expected_min
    assert f"{expected_min} workers" in body["error"]
    assert "3-month" in body["error"]

    allowed = _generate(env["client"], workers=expected_min)
    assert allowed.status_code == 200


def test_generate_route_scopes_plan_to_conversation(plan_env):
    """Plans live per conversation: another chat's plan is never destroyed."""
    env = plan_env
    _store_schedule(env["store"], env["user_id"])

    conv_a = env["db"].create_conversation(env["user_id"], title="Chat A")
    conv_b = env["db"].create_conversation(env["user_id"], title="Chat B")
    env["db"].create_calendar_event(
        env["user_id"],
        title="Old chat A plan",
        description="",
        date="2026-08-24",
        category="construction_plan",
        conversation_id=conv_a["id"],
    )

    response = _generate(env["client"], conversation_id=conv_b["id"])
    assert response.status_code == 200

    titles = [(e["title"], e["conversation_id"])
              for e in env["db"].get_calendar_events(env["user_id"])
              if e["category"] == "construction_plan"]
    assert ("Old chat A plan", conv_a["id"]) in titles
    new_events = [cid for _, cid in titles if cid == conv_b["id"]]
    assert new_events, "new plan should be tagged with chat B"

    # Plan context persisted for THIS conversation so follow-up chat queries
    # reuse the same duration/workers constraints.
    ctx_entries = env["store"].retrieve("project", "plan_context", user_id=env["user_id"])
    latest = ctx_entries[-1]["value"]
    assert latest["duration_months"] == 3
    assert latest["workers"] == 17
    assert latest["conversation_id"] == conv_b["id"]


def test_generate_route_requires_schedule_first(plan_env):
    env = plan_env
    response = _generate(env["client"])
    assert response.status_code == 400
    assert "blueprint" in response.get_json()["error"].lower()


def test_generate_route_validates_inputs(plan_env):
    env = plan_env
    _store_schedule(env["store"], env["user_id"])

    assert _generate(env["client"], start_date="").status_code == 400
    assert _generate(env["client"], start_date="23-08-2026").status_code == 400
    assert _generate(env["client"], duration_months=13).status_code == 400
    assert _generate(env["client"], workers="17.5").status_code == 400
    assert _generate(env["client"], workers="-5").status_code == 400
