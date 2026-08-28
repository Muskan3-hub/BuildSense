import pytest
from agents.coordinator import run_coordination_pipeline

def test_pipeline_generates_tool_execution_trace():
    """Test that the full orchestration pipeline aggregates a tool execution trace."""
    # We pass a query that triggers all agents
    user_query = "Can we build this for 15 lakh while staying compliant with fire safety? Give me a timeline and workforce."
    
    # We pass a dummy image path (Blueprint agent handles simulation fine without real image)
    pipeline_result = run_coordination_pipeline(
        image_path="dummy_path.png",
        user_query=user_query,
        budget_limit=1500000
    )
    
    # Verify the structure returned by coordinator
    assert "tool_execution_trace" in pipeline_result
    trace = pipeline_result["tool_execution_trace"]
    
    # We expect multiple tools to have been called (material_price x6, nbc_rule x3, weather x1)
    assert len(trace) > 5
    
    # Verify trace entries have required schema
    for call in trace:
        assert "tool" in call or "tool_name" in call
        assert "status" in call
        assert "timestamp" in call
        assert "duration_ms" in call
        
    # Verify specific tools were called
    tool_names = [call.get("tool", call.get("tool_name")) for call in trace]
    assert "get_material_price" in tool_names
    assert "lookup_nbc_rule" in tool_names
    assert "get_weather_advisory" in tool_names
    
    # Verify there are no duplicate timestamps (deduplication logic worked)
    timestamps = [call["timestamp"] for call in trace]
    assert len(timestamps) == len(set(timestamps))
