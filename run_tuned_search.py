"""Wrapper: run standalone solver + legal search with tuned objective weights.

Captures ALL output to tuned_output.txt for analysis.
"""
import sys
import os
import io

ROOT = r"C:\Users\VYRAL\IOptimal"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_FILE = os.path.join(ROOT, "tuned_output.txt")

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

print("=== IOptimal Tuned Objective Test ===")
print(f"Output: {OUTPUT_FILE}")
print()

from types import SimpleNamespace
args = SimpleNamespace(
    car="bmw",
    ibt=None,
    track="sebring",
    wing=17.0,
    lap=None,
    fuel=None,
    balance=None,
    tolerance=0.1,
    free=False,
    sto=None,
    space=False,
    no_learn=True,
    legacy_solver=False,
    legal_search=True,
    search_budget=50000,
)

print(f"Car: {args.car}")
print(f"Track: {args.track}")
print(f"Wing: {args.wing}")
print(f"Legal search: {args.legal_search}")
print(f"Search budget: {args.search_budget}")
print()

try:
    from solver.solve import run_solver
    run_solver(args)
except Exception as e:
    import traceback
    traceback.print_exc()

sys.stdout = old_stdout
sys.stderr = old_stderr
log_file.close()

print(f"\n=== Output saved to {OUTPUT_FILE} ===")
