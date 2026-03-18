"""Git operations: status, add, commit, push."""
import subprocess
import sys
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_result.txt'

results = []

def run(args, label):
    r = subprocess.run([GIT] + args, capture_output=True, text=True, timeout=60)
    results.append(f"=== {label} ===")
    results.append(r.stdout.strip() if r.stdout else "(no stdout)")
    if r.stderr.strip():
        results.append(f"STDERR: {r.stderr.strip()}")
    results.append(f"Exit: {r.returncode}")
    results.append("")
    return r.returncode

# Status
run(['status', '--short'], 'STATUS')

# Add changed files
run(['add',
     'solver/laptime_sensitivity.py',
     'output/search_report.py',
     'pipeline/report.py',
     'output/report.py',
     'pipeline/produce.py',
     'solver/solve.py',
     'run_full_justified.py',
     'full_justified_output.txt',
], 'ADD')

# Commit
msg = """Expand sensitivity analysis to ALL 45 parameters with full justifications

- Enhanced ParameterSensitivity with justification, telemetry_evidence,
  consequence_plus, consequence_minus fields
- Added sensitivity functions for ALL parameters: springs (heave, third,
  torsion, rear coil), perch offsets, pushrods, ARBs (size + blade),
  geometry (camber, toe), brakes (bias, target, migration, master cyls,
  pad compound), diff (preload, coast/drive ramps, clutch plates),
  TC (gain, slip), tyre pressures (front/rear cold), all 10 damper
  axes, wing angle, ride heights
- Calibrated against actual IBT telemetry: measured shock vel p99,
  aero compression, LLTD proxy, roll gradient, peak lat g, hot
  pressures, rear power slip, body roll, understeer
- Added justification_report() method generating engineering brief
- Updated pipeline/report.py with PARAMETER JUSTIFICATION section
- Updated output/report.py and search_report.py to show ALL params
- Updated callers to pass step6, supporting, measured, wing

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"""

run(['commit', '-m', msg], 'COMMIT')

# Push
run(['push'], 'PUSH')

with open(OUT, 'w') as f:
    f.write('\n'.join(results))
