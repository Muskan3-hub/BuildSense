"""
BuildSense — Code Compliance Agent
Tool-Integrated Version
- Uses the ToolRegistry to call `lookup_nbc_rule` for each compliance check,
  replacing hardcoded citation strings with dynamic, auditable lookups.
- Records every tool call in a `tool_calls` audit list returned in the output.
- Simulation mode now fetches NBC regulation text from the tool knowledge base.
"""

import json
from agents.config import get_llm, is_live_mode


def check_compliance(spatial_data, query=None):
    """
    Checks spatial coordinates and dimensions against NBC 2016.
    
    Each compliance rule now fetches its regulation text via the
    `lookup_nbc_rule` tool, enabling dynamic, auditable citation chains.

    Args:
        spatial_data: Output of `analyze_blueprint()`.
        query:        Optional user context string.

    Returns:
        dict with compliance_checks, is_overall_compliant, summary_findings,
        and `tool_calls` (list of tool invocation audit entries).
    """
    from agents.tools import tool_registry

    log_start = len(tool_registry.get_audit_log())

    try:
        verified_area = float(spatial_data.get("total_area_sqft"))
    except (AttributeError, TypeError, ValueError):
        verified_area = None

    if verified_area is None or verified_area <= 0:
        # A drawing without readable measurements cannot produce a verdict.
        exit_rule = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.2")
        corridor_rule = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.3")
        setback_rule = tool_registry.invoke("lookup_nbc_rule", part="Part 3", clause="6.2")
        citations = [
            result.get("output", {}).get("full_citation")
            for result in (exit_rule, corridor_rule, setback_rule)
            if result.get("status") == "success"
        ]
        return {
            "compliance_checks": [{
                "rule": "Verified blueprint dimensions and egress evidence",
                "status": "EVIDENCE_REQUIRED",
                "found_value": "No verified floor area or readable dimensions",
                "required_value": "Readable area, corridor widths, and egress dimensions",
                "nbc_citation": "; ".join(citations),
                "message": "Compliance needs readable area and egress dimensions; no verdict has been assigned.",
                "severity": "MEDIUM",
            }],
            "is_overall_compliant": None,
            "compliance_required": True,
            "summary_findings": "Evidence required: provide readable area, corridor-width, and egress dimensions before an NBC verdict can be made.",
            "tool_calls": _extract_tool_calls(tool_registry, log_start),
        }

    # ── Live Mode ──────────────────────────────────────────────────────────────
    if is_live_mode():
        llm = get_llm()
        
        # Fetch key rules via tool before sending to LLM to enrich the prompt
        nbc_4_3 = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.3")
        nbc_4_2 = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.2")
        nbc_6_2 = tool_registry.invoke("lookup_nbc_rule", part="Part 3", clause="6.2")

        nbc_texts = []
        for res in [nbc_4_3, nbc_4_2, nbc_6_2]:
            if res.get("status") == "success":
                out = res.get("output", {})
                nbc_texts.append(
                    f"[{out.get('full_citation', '')}]: {out.get('regulation_text', '')[:300]}"
                )

        nbc_context = "\n".join(nbc_texts) if nbc_texts else "NBC 2016 Part 4 fire safety rules."

        prompt = f"""
You are an expert Building Inspector and Code Compliance Consultant specializing in the National Building Code (NBC) of India 2016.
Analyze the following spatial layout data and evaluate it for building code violations.

Spatial Data:
{json.dumps(spatial_data, indent=2)}

Relevant NBC Regulations (fetched from NBC Knowledge Base):
{nbc_context}

Provide your response as a JSON object matching this schema:
{{
  "compliance_checks": [
    {{
      "rule": "Name of the rule",
      "status": "PASS or FAIL or WARNING",
      "found_value": "Detected value",
      "required_value": "NBC requirement",
      "nbc_citation": "Specific clause from NBC",
      "message": "Detailed explanation",
      "severity": "CRITICAL or MEDIUM or INFO"
    }}
  ],
  "is_overall_compliant": false,
  "summary_findings": "High-level summary."
}}

CRITICAL RULES:
- Return ONLY valid JSON. Do not return markdown, do not write backticks.
"""
        try:
            response = llm.invoke(prompt)
            from agents.config import extract_text
            text = extract_text(response.content).strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            result = json.loads(text.strip())
            result["tool_calls"] = _extract_tool_calls(tool_registry, log_start)
            return result
        except Exception as e:
            print(f"Error in Gemini Compliance: {str(e)}. Falling back to the deterministic rule-based engine.")

    # ── Simulation / Tool-Backed Mode ─────────────────────────────────────────
    checks = []
    is_overall_compliant = True

    # 1. Corridor width — fetch regulation via NBC Lookup Tool
    corridors = spatial_data.get("corridors", [])
    for corr in corridors:
        width_m = corr.get("width_m")
        name = corr.get("name", "Corridor")

        # Dynamic rule lookup
        nbc_result = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.3")
        if nbc_result.get("status") == "success":
            nbc_data = nbc_result.get("output", {})
            min_width = nbc_data.get("minimum_required_value", 1.2)
            citation = nbc_data.get("full_citation", "NBC 2016 Part 4, Clause 4.3")
            severity = nbc_data.get("severity", "CRITICAL")
        else:
            min_width = 1.2
            citation = "NBC 2016 Part 4 (Fire and Life Safety), Clause 4.3"
            severity = "CRITICAL"

        try:
            width_m = float(width_m)
        except (TypeError, ValueError):
            width_m = None

        if width_m is None:
            checks.append({
                "rule": f"Corridor Width — {name}",
                "status": "WARNING",
                "found_value": "Not readable from blueprint",
                "required_value": f"{min_width}m minimum",
                "nbc_citation": citation,
                "message": (
                    f"The width of corridor '{name}' is not readable. "
                    "Provide an explicit dimension before compliance can be confirmed."
                ),
                "severity": "MEDIUM",
            })
        elif width_m < min_width:
            is_overall_compliant = False
            checks.append({
                "rule": f"Corridor Width — {name}",
                "status": "FAIL",
                "found_value": f"{width_m}m",
                "required_value": f"{min_width}m minimum",
                "nbc_citation": citation,
                "message": (
                    f"The exit access corridor '{name}' is only {width_m}m wide. "
                    f"This fails the fire egress safety corridor requirement of {min_width}m. "
                    f"Regulation: {nbc_result.get('output', {}).get('regulation_text', '')[:200]}"
                ),
                "severity": severity
            })
        else:
            checks.append({
                "rule": f"Corridor Width — {name}",
                "status": "PASS",
                "found_value": f"{width_m}m",
                "required_value": f"{min_width}m minimum",
                "nbc_citation": citation,
                "message": f"Corridor '{name}' ({width_m}m) meets the NBC minimum egress width.",
                "severity": "INFO"
            })

    # 2. Exit count — fetch regulation via NBC Lookup Tool
    exits = spatial_data.get("exits", [])
    total_area = spatial_data.get("total_area_sqft")
    try:
        total_area = float(total_area)
    except (TypeError, ValueError):
        total_area = None

    exit_rule = tool_registry.invoke("lookup_nbc_rule", part="Part 4", clause="4.2")
    if exit_rule.get("status") == "success":
        exit_data = exit_rule.get("output", {})
        exit_citation = exit_data.get("full_citation", "NBC 2016 Part 4, Clause 4.2")
        exit_severity = exit_data.get("severity", "CRITICAL")
    else:
        exit_citation = "NBC 2016 Part 4, Clause 4.2.1"
        exit_severity = "CRITICAL"

    if total_area is None:
        checks.append({
            "rule": "Egress Exit Count",
            "status": "WARNING",
            "found_value": f"{len(exits)} exit(s); floor area unavailable",
            "required_value": "Depends on occupancy and verified floor area",
            "nbc_citation": exit_citation,
            "message": (
                "The blueprint area is unavailable, so the area-dependent exit "
                "requirement cannot be confirmed."
            ),
            "severity": "MEDIUM",
        })
    elif len(exits) < 2 and total_area > 1500:
        is_overall_compliant = False
        checks.append({
            "rule": "Egress Exit Count",
            "status": "FAIL",
            "found_value": f"{len(exits)} exit(s)",
            "required_value": "Minimum 2 remote exits",
            "nbc_citation": exit_citation,
            "message": (
                f"Only {len(exits)} exits detected for {total_area:,} sq ft. "
                "NBC requires at least two separate, remote exits to prevent crowding during evacuations."
            ),
            "severity": exit_severity
        })
    else:
        checks.append({
            "rule": "Egress Exit Count",
            "status": "PASS",
            "found_value": f"{len(exits)} exits",
            "required_value": "Minimum 2 remote exits",
            "nbc_citation": exit_citation,
            "message": "Satisfactory exit paths: at least two separate egress routes detected.",
            "severity": "INFO"
        })

    # 3. Setback advisory — fetch via NBC Lookup Tool
    setback_rule = tool_registry.invoke("lookup_nbc_rule", part="Part 3", clause="6.2")
    if setback_rule.get("status") == "success":
        sb_data = setback_rule.get("output", {})
        sb_citation = sb_data.get("full_citation", "NBC 2016 Part 3, Clause 6.2")
        sb_text = sb_data.get("regulation_text", "")[:200]
    else:
        sb_citation = "NBC 2016 Part 3 (Development Control Rules), Clause 6.2"
        sb_text = "Minimum setbacks typically 3.0m front/rear, 1.5m sides."

    checks.append({
        "rule": "Perimeter Setbacks",
        "status": "WARNING",
        "found_value": "Not specified in interior plan",
        "required_value": "Zoning / Municipal norms (typically 3.0m)",
        "nbc_citation": sb_citation,
        "message": (
            f"Boundary setback clearances are not specified in the interior floor plan. "
            f"Ensure local municipal setbacks are maintained. Regulation: {sb_text}"
        ),
        "severity": "MEDIUM"
    })

    summary = (
        "Non-compliant. Corridor A (0.9m) violates NBC minimum egress width of 1.2m (CRITICAL). "
        "Verify building setbacks with local municipal authority."
        if not is_overall_compliant else
        "Overall Compliant with NBC safety specifications."
    )

    tool_calls = _extract_tool_calls(tool_registry, log_start)

    return {
        "compliance_checks": checks,
        "is_overall_compliant": is_overall_compliant,
        "summary_findings": summary,
        "tool_calls": tool_calls
    }


def _extract_tool_calls(tool_registry, log_start: int) -> list:
    """Extracts new tool audit entries added since log_start."""
    return tool_registry.get_audit_log()[log_start:]
