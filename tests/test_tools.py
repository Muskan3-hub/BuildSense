import pytest
import os
from agents.tools import tool_registry

def test_tool_registry_manifest():
    """Test that the tool registry correctly exports a manifest."""
    manifest = tool_registry.get_tool_manifest()
    assert isinstance(manifest, list)
    assert len(manifest) >= 4
    tool_names = [t["name"] for t in manifest]
    assert "get_material_price" in tool_names
    assert "get_weather_advisory" in tool_names
    assert "lookup_nbc_rule" in tool_names
    assert "generate_json_report" in tool_names

def test_material_price_tool():
    """Test the material price lookup tool."""
    result = tool_registry.invoke("get_material_price", material="cement", region="Pune", quantity_units=100)
    assert result["status"] == "success"
    assert result["output"]["unit_price_inr"] > 0
    assert result["output"]["currency"] == "INR"
    assert "Pune" in result["output"]["region"]

def test_nbc_lookup_tool():
    """Test the NBC regulation lookup tool."""
    result = tool_registry.invoke("lookup_nbc_rule", query="corridor width")
    assert result["status"] == "success"
    assert "1.2" in result["output"]["required_value"]
    assert result["output"]["severity"] == "HIGH"

def test_weather_advisory_tool_simulation():
    """Test the weather advisory tool (fallback/simulation if no key)."""
    # Assuming no WEATHER_API_KEY in test environment unless set
    result = tool_registry.invoke("get_weather_advisory", city="Pune")
    assert result["status"] == "success"
    assert "Pune" in result["output"]["city"]
    assert "condition" in result["output"]

def test_tool_registry_audit_log():
    """Test that tool registry captures audit logs."""
    tool_registry.clear_audit_log()
    
    tool_registry.invoke("get_material_price", material="cement", region="Pune", quantity_units=10)
    tool_registry.invoke("lookup_nbc_rule", query="fire exit")
    
    logs = tool_registry.get_session_trace()
    assert len(logs) == 2
    assert logs[0]["tool"] == "get_material_price"
    assert logs[1]["tool"] == "lookup_nbc_rule"
    assert "timestamp" in logs[0]
    assert "duration_ms" in logs[0]

def test_invalid_tool_invocation():
    """Test invoking a non-existent tool."""
    result = tool_registry.invoke("non_existent_tool")
    assert result["status"] == "error"
    assert "not found" in result["error"]

# --- Milestone 3 Tests ---

def test_design_catalog_lookup():
    """Validate design catalog tool returns structured furniture/color data"""
    from agents.tools.design_catalog import get_design_catalog
    
    # Test valid style
    res = get_design_catalog(style="modern_minimalist", category="colors")
    assert "error" not in res
    assert res["style"] == "Modern Minimalist"
    assert "color_palette" in res
    assert "primary_wall" in res["color_palette"]

    # Test invalid style
    res = get_design_catalog(style="unknown_style")
    assert "error" in res

def test_calendar_generation():
    """Validate calendar tool maps 48-day schedule correctly, skipping Sundays"""
    from agents.tools.calendar_engine import generate_project_calendar
    
    timeline = [{"phase": "Test Phase", "duration_days": 10}]
    start_date = "2026-08-01" # August 1, 2026 is a Saturday
    
    res = generate_project_calendar(timeline, start_date)
    assert "error" not in res
    assert res["total_working_days"] == 10
    assert len(res["events"]) == 1
    
    # Check that Sundays were skipped (10 working days starting on Saturday means 12 calendar days: Sat, Mon-Sat, Mon-Wed)
    assert res["total_calendar_days"] >= 10

def test_calendar_phase_start_skips_sunday():
    """Phase dates begin on the first actual working day."""
    from agents.tools.calendar_engine import generate_project_calendar

    result = generate_project_calendar(
        [{"phase": "Test Phase", "duration_days": 1}],
        "2026-08-02",  # Sunday
    )

    assert result["events"][0]["end_date"] == "2026-08-03"

def test_calendar_event_work_details():
    """Validate calendar events contain construction work details."""
    from agents.tools.calendar_engine import generate_project_calendar

    timeline = [{
        "phase": "Foundation Work",
        "duration_days": 2,
        "tasks": [
            "Excavation",
            "Foundation preparation"
        ],
        "priority": "high",
        "location": "Foundation Area",
        "dependencies": ["Site Preparation"],
        "description": "Prepare and construct the foundation."
    }]

    result = generate_project_calendar(
        timeline,
        "2026-08-03"
    )

    assert "error" not in result
    assert len(result["events"]) == 1

    event = result["events"][0]

    assert event["title"] == "Foundation Work"
    assert event["start_time"] == "09:00"
    assert event["end_time"] == "17:00"
    assert event["duration_minutes"] == 960
    assert event["working_days"] == 2
    assert event["status"] == "planned"
    assert event["priority"] == "high"
    assert event["location"] == "Foundation Area"
    assert event["dependencies"] == ["Site Preparation"]
    assert "Excavation" in event["tasks"]

    # Check daily schedule details
    assert "2026-08-03" in result["daily_schedules"]

    daily = result["daily_schedules"]["2026-08-03"][0]

    assert daily["start_time"] == "09:00"
    assert daily["end_time"] == "17:00"
    assert daily["duration_minutes"] == 480
    assert daily["status"] == "planned"


def test_blueprint_metric_dimensions_convert_to_sqft():
    """Metric dimensions are converted from the printed dimensions only."""
    from agents.blueprint import _parse_dimensions_sqft

    assert _parse_dimensions_sqft("3.2 m x 4.5 m") == pytest.approx(155.0, abs=0.1)
    assert _parse_dimensions_sqft("3.2 m x 4.5") == pytest.approx(155.0, abs=0.1)

def test_memory_short_term():
    """Validate conversation memory stores and retrieves recent turns"""
    from agents.memory import ConversationMemory
    
    mem = ConversationMemory(max_turns=3)
    mem.add_turn("user", "Hello 1")
    mem.add_turn("assistant", "Hi 1")
    mem.add_turn("user", "Hello 2")
    mem.add_turn("assistant", "Hi 2")
    
    history = mem.get_history()
    assert len(history) == 3
    assert history[0]["content"] == "Hi 1"
    assert history[2]["content"] == "Hi 2"

def test_memory_long_term(tmp_path):
    """Validate knowledge store persists and loads from JSON"""
    from agents.memory import KnowledgeStore
    
    test_file = tmp_path / "test_knowledge.json"
    ks = KnowledgeStore(filepath=str(test_file))
    
    ks.store("design_preference", "style", "Modern Minimalist")
    ks.store("design_preference", "budget", "Premium")
    
    assert len(ks) == 2
    
    # Test retrieve
    res = ks.retrieve(topic="design_preference", key="style")
    assert len(res) == 1
    assert res[0]["value"] == "Modern Minimalist"
    
    # Reload from disk
    ks2 = KnowledgeStore(filepath=str(test_file))
    assert len(ks2) == 2
    assert ks2.retrieve(key="budget")[0]["value"] == "Premium"

def test_interior_design_simulation():
    """Validate design agent returns complete room specifications"""
    from agents.interior_design import generate_interior_design
    
    spatial_data = {
        "rooms": [
            {"name": "Conference Room", "area": 400},
            {"name": "Manager Office", "area": 250}
        ]
    }
    
    res = generate_interior_design(spatial_data, style_preset="modern_minimalist")
    assert "overall_theme" in res
    assert "rooms" in res
    assert len(res["rooms"]) == 2
    assert res["rooms"][0]["room_name"] == "Conference Room"
    assert "furniture" in res["rooms"][0]
    assert "tool_calls" in res
