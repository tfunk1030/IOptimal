import subprocess, sys
from pathlib import Path

result = subprocess.run(
    [sys.executable, '-m', 'pytest', 
     'tests/test_search_report.py',
     'tests/test_candidate_search.py',
     'tests/test_brake_solver.py',
     'tests/test_comparison_scoring.py',
     'tests/test_comparison_report.py',
     'tests/test_bmw_sebring_garage_truth.py',
     'tests/test_predictor_directionality.py',
     'tests/test_envelope_clusters.py',
     'tests/test_diff_solver_extended.py',
     'tests/test_learner_sanity.py',
     'tests/test_prediction_feedback.py',
     '-v', '--tb=short'],
    cwd=str(Path(__file__).resolve().parent),
    capture_output=True, text=True, timeout=300
)
print(result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
sys.exit(result.returncode)
