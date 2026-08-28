"""
BuildSense Tool: JSON Report Generator

Generates a structured, timestamped JSON report from the full coordination
pipeline result and saves it to the reports/ directory.
Returns a Flask-servable download URL.
"""

import os
import json
from datetime import datetime, timezone


REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))), "reports")


def generate_json_report(pipeline_result: dict, report_title: str = "BuildSense Construction Analysis Report") -> dict:
    """
    Serialize the full coordination pipeline result into a structured JSON report.
    
    Args:
        pipeline_result: The dict returned by run_coordination_pipeline()
        report_title:    Human-readable title for the report
    
    Returns:
        dict with filename, download_url, report_size_kb, and summary stats
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc)
    ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
    filename = f"buildsense_report_{ts_str}.json"
    file_path = os.path.join(REPORTS_DIR, filename)
    
    # Build structured report envelope
    report = {
        "report_metadata": {
            "title": report_title,
            "generated_at": timestamp.isoformat(),
            "generated_by": "BuildSense Multi-Agent Decision Engine",
            "version": "2.0"
        },
        "routing_plan": pipeline_result.get("routing_plan", []),
        "spatial_analysis": pipeline_result.get("spatial_data", {}),
        "cost_estimation": pipeline_result.get("specialist_outputs", {}).get("cost_estimation", {}),
        "compliance_audit": pipeline_result.get("specialist_outputs", {}).get("code_compliance", {}),
        "construction_schedule": pipeline_result.get("specialist_outputs", {}).get("scheduling", {}),
        "workforce_matches": pipeline_result.get("specialist_outputs", {}).get("workforce", {}),
        "conflicts_detected": pipeline_result.get("conflicts_detected", []),
        "tool_execution_trace": pipeline_result.get("tool_execution_trace", []),
        "synthesized_recommendation": pipeline_result.get("synthesized_recommendation", ""),
        "summary_stats": _build_summary_stats(pipeline_result)
    }
    
    # Write to disk
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    
    file_size_kb = round(os.path.getsize(file_path) / 1024, 1)
    
    return {
        "success": True,
        "filename": filename,
        "download_url": f"/reports/{filename}",
        "file_path": file_path,
        "file_size_kb": file_size_kb,
        "generated_at": timestamp.isoformat(),
        "report_title": report_title,
        "sections_included": [
            "Spatial Analysis", "Cost Estimation", "Compliance Audit",
            "Construction Schedule", "Workforce Matches",
            "Conflicts Detected", "Tool Execution Trace",
            "Synthesized Recommendation"
        ]
    }


def _build_summary_stats(result: dict) -> dict:
    """Extract key figures from the pipeline result for the report header."""
    cost_data = result.get("specialist_outputs", {}).get("cost_estimation", {})
    sched_data = result.get("specialist_outputs", {}).get("scheduling", {})
    comp_data = result.get("specialist_outputs", {}).get("code_compliance", {})
    tool_trace = result.get("tool_execution_trace", [])
    
    return {
        "total_cost_inr": cost_data.get("total_cost_inr"),
        "formatted_cost": cost_data.get("formatted_total_cost"),
        "total_duration_days": sched_data.get("total_duration_days"),
        "is_compliant": comp_data.get("is_overall_compliant"),
        "conflicts_count": len(result.get("conflicts_detected", [])),
        "agents_invoked": len(result.get("routing_plan", [])),
        "tool_calls_count": len(tool_trace),
        "tool_calls_successful": sum(1 for t in tool_trace if t.get("status") == "success"),
        "tool_calls_failed": sum(1 for t in tool_trace if t.get("status") == "error"),
    }
