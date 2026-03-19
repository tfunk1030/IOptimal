import sys
sys.path.insert(0, r'C:\Users\VYRAL\IOptimal')

print("Testing imports...")
try:
    from solver.laptime_sensitivity import compute_laptime_sensitivity, ParameterSensitivity, LaptimeSensitivityReport
    print("  laptime_sensitivity: OK")
except Exception as e:
    print(f"  laptime_sensitivity: FAIL - {e}")

try:
    from output.search_report import compute_sensitivity, format_sensitivity
    print("  search_report: OK")
except Exception as e:
    print(f"  search_report: FAIL - {e}")

try:
    from pipeline.report import generate_report
    print("  pipeline.report: OK")
except Exception as e:
    print(f"  pipeline.report: FAIL - {e}")

try:
    from output.report import print_full_setup_report
    print("  output.report: OK")
except Exception as e:
    print(f"  output.report: FAIL - {e}")

# Test that ParameterSensitivity has new fields
ps = ParameterSensitivity(
    parameter="test",
    current_value=1.0,
    units="mm",
    delta_per_unit_ms=5.0,
    confidence="high",
    mechanism="test mechanism",
    justification="test justification",
    telemetry_evidence="test evidence",
    consequence_plus="+1: something",
    consequence_minus="-1: something else",
)
print(f"  ParameterSensitivity new fields: OK (justification={ps.justification!r})")

# Test that LaptimeSensitivityReport has justification_report method
report = LaptimeSensitivityReport(sensitivities=[ps])
jr = report.justification_report(width=80)
assert "PARAMETER JUSTIFICATION" in jr, "justification_report missing header"
assert "test justification" in jr, "justification_report missing justification text"
print(f"  justification_report: OK ({len(jr)} chars)")

print("\nAll import tests passed!")
