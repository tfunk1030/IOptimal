"""Wrapper: run the solver and capture ALL output to full_output.txt."""
import sys
import os

os.chdir(r"C:\Users\VYRAL\IOptimal")
sys.path.insert(0, r"C:\Users\VYRAL\IOptimal")

output_path = r"C:\Users\VYRAL\IOptimal\full_output.txt"
log = open(output_path, "w", encoding="utf-8", errors="replace")

sys.stdout = log
sys.stderr = log

try:
    sys.argv = [
        "ioptimal",
        "--car", "bmw",
        "--track", "sebring",
        "--wing", "17",
        "--verbose",
    ]
    # Use runpy to execute __main__.py as if we ran python -m on the directory
    import runpy
    runpy.run_path(r"C:\Users\VYRAL\IOptimal\__main__.py", run_name="__main__")
except SystemExit as e:
    print(f"\n[SystemExit: {e}]")
except Exception as e:
    import traceback
    print(f"\n[EXCEPTION]: {type(e).__name__}: {e}")
    traceback.print_exc()
finally:
    log.flush()
    log.close()
