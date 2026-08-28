"""Duration-specific minimum workforce requirements.

The SAME blueprint must require a different minimum crew depending on the
selected completion duration: shorter windows need more simultaneous
workers, longer windows spread the identical analysed workload. All values
are calculated from the project's own Gemini-derived analysis — never a
fixed global count, never ``workers / months``.
"""

import pytest

from agents.workforce import (
    analyse_project_profile,
    available_working_days_for_duration,
    minimum_workers_for_duration,
)

# A typical blueprint analysis: reference crew 17, 48 planned working days,
# none of them pure waiting periods.
PROFILE = {
    "reference_workers": 17,
    "planned_working_days": 48,
    "waiting_working_days": 0,
    "total_area_sqft": None,
    "phase_count": 6,
}


def test_same_project_different_durations_change_minimum():
    """1M > 3M > 6M > 12M for the identical blueprint (until the floor)."""
    minimums = {
        months: minimum_workers_for_duration(PROFILE, months)["minimum_workers"]
        for months in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
    }
    values = [minimums[m] for m in sorted(minimums)]
    assert values == sorted(values, reverse=True), minimums
    # Short duration demands substantially more crew than long durations.
    assert minimums[1] > minimums[3] > minimums[12]


def test_long_duration_respects_minimum_viable_crew():
    """Dependencies keep a baseline crew required even with ample time."""
    result = minimum_workers_for_duration(PROFILE, 12)
    assert result["feasible"] is True
    assert result["minimum_workers"] >= 2
    assert result["basis"]["floor_crew"] == max(
        2, -(-17 * 1 // 2),  # ceil(17 * MIN_VIABLE_CREW_FACTOR)
    )
    assert result["minimum_workers"] >= result["basis"]["floor_crew"]


def test_short_duration_beyond_site_capacity_is_infeasible():
    """An extreme compression is flagged honestly — no invented 500-worker plan."""
    huge = dict(PROFILE, planned_working_days=300)
    result = minimum_workers_for_duration(huge, 1)

    assert result["feasible"] is False
    assert result["minimum_workers"] is None
    assert "dependencies" in result["reason"]
    assert result["basis"]["ceiling_crew"] < result["basis"]["productive_person_days"] / result["basis"]["available_working_days"]


def test_waiting_periods_raise_the_requirement():
    """Curing/inspection holds consume fixed calendar time no crew can compress."""
    plain = minimum_workers_for_duration(PROFILE, 3)
    # Same productive work (48 days) PLUS 8 non-compressible curing days.
    curing_profile = dict(
        PROFILE, planned_working_days=56, waiting_working_days=8,
    )
    with_waits = minimum_workers_for_duration(curing_profile, 3)

    assert with_waits["basis"]["productive_available_days"] \
        < plain["basis"]["available_working_days"]
    assert with_waits["minimum_workers"] > plain["minimum_workers"]


def test_window_consumed_by_curing_alone_is_infeasible():
    tiny_window = {"reference_workers": 17, "planned_working_days": 40,
                   "waiting_working_days": 30}
    result = minimum_workers_for_duration(tiny_window, 1)
    assert result["feasible"] is False
    assert result["minimum_workers"] is None


def test_project_scope_changes_the_requirement_at_fixed_duration():
    """Two different blueprints at 3 months get independently sized crews."""
    small = {"reference_workers": 8, "planned_working_days": 20}
    large = {"reference_workers": 25, "planned_working_days": 90}

    small_result = minimum_workers_for_duration(small, 3)
    large_result = minimum_workers_for_duration(large, 3)

    assert large_result["minimum_workers"] != small_result["minimum_workers"]
    assert large_result["minimum_workers"] > small_result["minimum_workers"]


def test_exact_start_date_window_uses_calendar_math():
    """With a start date the window counts REAL working days (Sundays/holidays off)."""
    from datetime import date

    exact = available_working_days_for_duration(3, "2026-08-23")
    estimate = available_working_days_for_duration(3, None)
    assert exact > 0 and estimate > 0
    # The exact count stays close to (but never blindly equals) the estimate.
    assert abs(exact - estimate) <= 10


def test_insufficient_analysis_returns_no_invented_number():
    result = minimum_workers_for_duration({}, 3)
    assert result["minimum_workers"] is None
    assert "Not enough" in result["reason"]

    no_schedule = minimum_workers_for_duration(
        {"reference_workers": 17, "planned_working_days": 0}, 3,
    )
    assert no_schedule["minimum_workers"] is None


def test_site_area_relaxes_the_capacity_cap():
    """A larger analysed site allows a higher useful crew ceiling."""
    base = minimum_workers_for_duration(PROFILE, 1)
    big_site = dict(PROFILE, total_area_sqft=20000)
    relaxed = minimum_workers_for_duration(big_site, 1)

    assert relaxed["basis"]["ceiling_crew"] > base["basis"]["ceiling_crew"]
    assert relaxed["basis"]["ceiling_crew"] == 200  # 20000 / SPACE_SQFT_PER_WORKER


def test_area_bound_does_not_lower_the_floor():
    """The area cap never suppresses the dependency-driven minimum crew."""
    small = {"reference_workers": 6, "planned_working_days": 18,
             "waiting_working_days": 0, "total_area_sqft": 100}
    result = minimum_workers_for_duration(small, 12)

    assert result["feasible"] is True
    assert result["minimum_workers"] >= result["basis"]["floor_crew"]
    assert result["basis"]["floor_crew"] >= 2


# ── API-level coverage ───────────────────────────────────────────────────────


class _FakeKnowledgeStore:
    def __init__(self):
        self._data = {}

    def store(self, topic, key, value, source=None, user_id=None):
        self._data.setdefault((user_id, topic, key), []).append({"value": value})

    def retrieve(self, topic, key, user_id=None):
        return list(self._data.get((user_id, topic, key), []))


@pytest.fixture()
def wf_env(tmp_path, monkeypatch):
    import app as buildsense_app
    import agents.auth as auth_module
    from agents.database import Database

    test_db = Database(str(tmp_path / "wf.db"))
    fake_store = _FakeKnowledgeStore()
    monkeypatch.setattr(buildsense_app, "db", test_db)
    monkeypatch.setattr(buildsense_app, "knowledge_store", fake_store)
    monkeypatch.setattr(auth_module, "db", test_db)

    client = buildsense_app.app.test_client()
    reg = client.post(
        "/api/auth/register",
        json={"username": "wf_user", "password": "secret123",
              "confirm_password": "secret123"},
    )
    assert reg.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "wf_user", "password": "secret123"},
    )
    assert login.status_code == 200
    user_id = login.get_json()["user"]["id"]

    fake_store.store(
        "project", "schedule",
        {"timeline": [
            {"phase": "A", "duration_days": 10, "tasks": ["a", "b"]},
            {"phase": "B", "duration_days": 22, "tasks": ["a"]},
            {"phase": "C", "duration_days": 16, "tasks": ["a", "b", "c"]},
        ]},
        "Scheduling Agent", user_id=user_id,
    )
    fake_store.store(
        "project", "workforce_minimum",
        {"minimum_workers": 17},
        "Gemini Blueprint Analysis", user_id=user_id,
    )
    return {"client": client, "store": fake_store, "user_id": user_id}


def test_duration_endpoint_calculates_then_caches(wf_env):
    env = wf_env
    first = env["client"].get(
        "/api/calendar/workforce-minimum?duration_months=3"
    )
    assert first.status_code == 200
    body = first.get_json()
    assert body["duration_months"] == 3
    assert body["source"] == "calculated"
    assert body["feasible"] is True
    assert isinstance(body["minimum_workers"], int) and body["minimum_workers"] >= 1

    second = env["client"].get("/api/calendar/workforce-minimum?duration_months=3")
    cached_body = second.get_json()
    assert cached_body["source"] == "cached"
    assert cached_body["minimum_workers"] == body["minimum_workers"]
    # Cache hit — no duplicate entry was stored for this duration.
    entries = env["store"].retrieve("project", "duration_workforce", user_id=env["user_id"])
    assert len(entries) == 1


def test_duration_endpoint_durations_are_isolated(wf_env):
    env = wf_env
    one = env["client"].get("/api/calendar/workforce-minimum?duration_months=1").get_json()
    twelve = env["client"].get("/api/calendar/workforce-minimum?duration_months=12").get_json()

    assert one["minimum_workers"] > twelve["minimum_workers"]
    assert one["duration_months"] == 1 and twelve["duration_months"] == 12


def test_duration_endpoint_without_param_keeps_reference_shape(wf_env):
    env = wf_env
    response = env["client"].get("/api/calendar/workforce-minimum")
    body = response.get_json()
    assert response.status_code == 200
    assert body == {"minimum_workers": 17, "basis": ""}


def test_generate_validates_against_current_duration_only(wf_env):
    """TEST C/D: below the 1-month minimum fails, above it passes; the same
    worker count that fails at 1 month may be valid at 12 months."""
    env = wf_env
    # The UI always sends the current start date so hint and backend gate
    # use identical calendar math for the same selection.
    common_start = "&start_date=2026-08-23"
    min_one = env["client"].get(
        "/api/calendar/workforce-minimum?duration_months=1" + common_start
    ).get_json()["minimum_workers"]
    min_twelve = env["client"].get(
        "/api/calendar/workforce-minimum?duration_months=12" + common_start
    ).get_json()["minimum_workers"]

    assert min_one > min_twelve

    rejected = env["client"].post(
        "/api/calendar/generate",
        json={"start_date": "2026-08-23", "duration_months": 1,
              "workers": min_one - 1},
    )
    assert rejected.status_code == 400
    body = rejected.get_json()
    assert f"{min_one} workers" in body["error"]
    assert "1-month" in body["error"]

    accepted = env["client"].post(
        "/api/calendar/generate",
        json={"start_date": "2026-08-23", "duration_months": 1,
              "workers": min_one},
    )
    assert accepted.status_code == 200

    # The same mid-range crew: invalid for 1 month, valid for 12 months.
    mid = (min_one + min_twelve) // 2
    at_twelve = env["client"].post(
        "/api/calendar/generate",
        json={"start_date": "2026-08-23", "duration_months": 12, "workers": mid},
    )
    assert at_twelve.status_code == 200
    at_one = env["client"].post(
        "/api/calendar/generate",
        json={"start_date": "2026-08-23", "duration_months": 1, "workers": mid},
    )
    assert at_one.status_code == 400


def test_generate_rejects_infeasible_duration_honestly(wf_env):
    """TEST B variant: impossible windows are refused, never scheduled."""
    env = wf_env
    env["store"].store(
        "project", "schedule",
        {"timeline": [
            {"phase": f"Phase {i}", "duration_days": 30, "tasks": ["work"]}
            for i in range(10)
        ]},
        "Scheduling Agent", user_id=env["user_id"],
    )
    response = env["client"].post(
        "/api/calendar/generate",
        json={"start_date": "2026-08-23", "duration_months": 1, "workers": 40},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body.get("feasible") is False
    assert "realistically" in body["error"] or "not realistically" in body["error"]


def test_scoped_project_value_filters_by_conversation(monkeypatch):
    """Cached requirements match conversation AND duration exactly."""
    import agents.coordinator as coordinator

    store = _FakeKnowledgeStore()
    monkeypatch.setattr(coordinator, "knowledge_store", store)
    store.store(
        "project", "duration_workforce",
        {"duration_months": 3, "minimum_workers": 22, "conversation_id": 7},
        "t", user_id=1,
    )
    hit = coordinator._resolve_scoped_project_value(1, 7, "duration_workforce")
    assert hit["minimum_workers"] == 22
    assert coordinator._resolve_scoped_project_value(1, 9, "duration_workforce") is None
