"""End-to-end durable-calendar coverage without external AI/calendar access."""

from agents import coordinator, scheduling
from agents.calendar_service import CalendarService, LocalCalendarStore


class _MemorySink:
    """Keep the integration test from mutating the user's durable knowledge."""

    def store(self, *args, **kwargs):
        return {}

    def retrieve(self, *args, **kwargs):
        return []


def test_calendar_pipeline_persists_events_and_api_returns_them(tmp_path, monkeypatch):
    """A calendar query must reach the durable API through the coordinator."""
    import app as buildsense_app

    service = CalendarService(LocalCalendarStore(str(tmp_path / "events.json")))
    monkeypatch.setattr(coordinator, "calendar_service", service)
    monkeypatch.setattr(buildsense_app, "calendar_service", service)
    monkeypatch.setattr(coordinator, "knowledge_store", _MemorySink())
    monkeypatch.setattr(scheduling, "is_live_mode", lambda: False)

    spatial_data = {
        "rooms": [{"name": "Office", "room_type": "office", "coords": [0, 0, 50, 50]}],
        "corridors": [],
        "exits": [],
        "total_area_sqft": None,
    }
    client = buildsense_app.app.test_client()
    response = client.post(
        "/api/query",
        json={
            "query": "Generate the project calendar",
            "image_path": "unused.png",
            "spatial_data": spatial_data,
            "start_date": "2026-08-03",
        },
    )

    assert response.status_code == 200
    result = response.get_json()
    calendar = result["specialist_outputs"]["calendar"]
    assert calendar["calendar_status"] == "local_fallback"
    assert calendar["stored_events"]
    assert calendar["stored_events"][0]["daily_schedule"]

    events_response = client.get("/api/calendar/events")
    assert events_response.status_code == 200
    events = events_response.get_json()["events"]
    assert len(events) == len(calendar["events"])
    assert events[0]["title"]
    assert events[0]["date"]
    assert events[0]["start_time"] == "09:00"

    # A repeated request uses the same deterministic source IDs, not duplicates.
    repeated = client.post(
        "/api/query",
        json={
            "query": "Generate the project calendar",
            "image_path": "unused.png",
            "spatial_data": spatial_data,
            "start_date": "2026-08-03",
        },
    )
    assert repeated.status_code == 200
    assert len(client.get("/api/calendar/events").get_json()["events"]) == len(events)
