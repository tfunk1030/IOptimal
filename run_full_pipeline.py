"""Wrapper to run the full pipeline and capture ALL stdout/stderr to a file."""
import subprocess
import sys
import os

OUTPUT_FILE = r"C:\Users\VYRAL\IOptimal\full_pipeline_output.txt"

# The largest IBT file (86MB)
IBT_FILE = r"C:\Users\VYRAL\IOptimal\data\telemetry\bmwlmdh_sebring international 2026-03-11 17-38-43.ibt"

cmd = [
    sys.executable, "-m", "pipeline.produce",
    "--car", "bmw",
    "--ibt", IBT_FILE,
    "--wing", "17",
    "--explore-legal-space",
    "--search-mode", "standard",
    "--search-budget", "5000",
]

print(f"Running: {' '.join(cmd)}")
print(f"Output file: {OUTPUT_FILE}")
print(f"Working dir: {os.path.dirname(os.path.abspath(__file__))}")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    proc = subprocess.run(
        cmd,
        cwd=r"C:\Users\VYRAL\IOptimal",
        stdout=f,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

print(f"Process exited with code: {proc.returncode}")
print(f"Output written to: {OUTPUT_FILE}")

# Print file size
size = os.path.getsize(OUTPUT_FILE)
print(f"Output file size: {size} bytes")
