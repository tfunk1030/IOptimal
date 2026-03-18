"""Wrapper: run full pipeline with legal space search, capture ALL output."""
import sys
import os
import io

# Ensure IOptimal root is on sys.path
ROOT = r"C:\Users\VYRAL\IOptimal"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

# Force UTF-8 on all streams BEFORE anything else
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_FILE = os.path.join(ROOT, "full_pipeline_output_v2.txt")
IBT_FILE = os.path.join(ROOT, "data", "telemetry",
                         "bmwlmdh_sebring international 2026-03-11 17-38-43.ibt")

# Redirect stdout+stderr to file AND console
class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass
    @property
    def encoding(self):
        return "utf-8"
    def reconfigure(self, **kwargs):
        pass

log_file = open(OUTPUT_FILE, "w", encoding="utf-8", errors="replace")
old_stdout = sys.stdout
old_stderr = sys.stderr
sys.stdout = Tee(log_file, old_stdout)
sys.stderr = Tee(log_file, old_stderr)

print(f"=== IOptimal Full Pipeline Run ===")
print(f"IBT: {IBT_FILE}")
print(f"Output: {OUTPUT_FILE}")
print(f"Working dir: {os.getcwd()}")
print()

# Build args namespace
from types import SimpleNamespace
args = SimpleNamespace(
    car="bmw",
    ibt=IBT_FILE,
    wing=17.0,
    lap=None,
    balance=None,
    tolerance=0.1,
    fuel=None,
    free=False,
    sto=None,
    json=None,
    setup_json=None,
    report_only=False,
    no_learn=False,
    legacy_solver=False,
    min_lap_time=None,
    outlier_pct=0.115,
    stint=False,
    stint_threshold=1.5,
    verbose=True,
    explore_legal_space=True,
    search_budget=5000,
    search_mode="standard",
    keep_weird=True,
    objective_profile="balanced",
    learn=False,
    auto_learn=False,
    track=None,
)

try:
    from pipeline.produce import produce
    produce(args)
except Exception as e:
    import traceback
    print(f"\n\n=== CRASH ===\n{e}\n")
    traceback.print_exc()

log_file.close()
sys.stdout = old_stdout
sys.stderr = old_stderr
print(f"\nOutput written to: {OUTPUT_FILE}")
print(f"File size: {os.path.getsize(OUTPUT_FILE)} bytes")
