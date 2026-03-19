"""Wrapper: run IOptimal solver via subprocess, capture ALL output."""
import subprocess
import os

OUTPUT = r"C:\Users\VYRAL\IOptimal\setup_output.txt"
IBT = r"C:\Users\VYRAL\IOptimal\ibtfiles\bmwlmdh_sebring international 2026-03-18 20-15-08.ibt"
PYTHON = r"C:\Users\VYRAL\AppData\Local\Programs\Python\Python313\python.exe"
MAIN = r"C:\Users\VYRAL\IOptimal\__main__.py"

cmd = [
    PYTHON, MAIN,
    "--car", "bmw",
    "--ibt", IBT,
    "--wing", "17",
    "--verbose",
    "--space",
]

print(f"Running solver on: {os.path.basename(IBT)}")
print(f"Output -> {OUTPUT}")

with open(OUTPUT, "w", encoding="utf-8", errors="replace") as f:
    proc = subprocess.run(
        cmd,
        cwd=r"C:\Users\VYRAL\IOptimal",
        stdout=f,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env={**os.environ, "PYTHONPATH": r"C:\Users\VYRAL\IOptimal"},
    )

size = os.path.getsize(OUTPUT)
print(f"Exit code: {proc.returncode}")
print(f"Output size: {size} bytes")
