# -*- coding: utf-8 -*-
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from PIL import Image
from agents.coordinator import run_coordination_pipeline

# Create minimal test image
img_path = 'uploads/test_run.png'
os.makedirs('uploads', exist_ok=True)
Image.new('RGB', (800, 600), '#0b0e1a').save(img_path)

result = run_coordination_pipeline(
    img_path,
    "Can we finish Phase 2 within a 15 lakh budget while staying compliant with fire safety norms?",
    budget_limit=1500000
)

print("=== ROUTING PLAN ===")
print(result['routing_plan'])

print("\n=== CONFLICTS DETECTED ===")
for c in result['conflicts_detected']:
    print(" -", c.encode('ascii', 'replace').decode())

cost = result['specialist_outputs'].get('cost_estimation', {})
print("\n=== COST ESTIMATION ===")
print("Total INR:", cost.get('total_cost_inr'))
print("Formatted:", cost.get('formatted_total_cost', '').encode('ascii', 'replace').decode())

comp = result['specialist_outputs'].get('code_compliance', {})
print("\n=== COMPLIANCE ===")
print("Overall Compliant:", comp.get('is_overall_compliant'))
for ch in comp.get('compliance_checks', []):
    print(f"  [{ch['status']}] {ch['rule']}")

sched = result['specialist_outputs'].get('scheduling', {})
print("\n=== SCHEDULING ===")
print("Total Days:", sched.get('total_duration_days'))
print("Critical Path:", sched.get('critical_path', [])[:3], "...")

print("\n=== TOOL EXECUTION TRACE ===")
trace = result.get('tool_execution_trace', [])
print(f"Total Tools Executed: {len(trace)}")
for call in trace[:3]:
    print(f"  - [{call.get('status', '???')}] {call.get('tool', call.get('tool_name', '???'))} ({call.get('duration_ms', 0)}ms)")
if len(trace) > 3:
    print(f"  ... and {len(trace) - 3} more")

print("\n=== SYNTHESIS (first 400 chars) ===")
print(result['synthesized_recommendation'][:400].encode('ascii', 'replace').decode())
print("\n[TEST PASSED]")
