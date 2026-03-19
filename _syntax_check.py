"""Syntax check for modified files."""
import py_compile
import sys

files = [
    r'C:\Users\VYRAL\IOptimal\solver\laptime_sensitivity.py',
    r'C:\Users\VYRAL\IOptimal\output\search_report.py',
    r'C:\Users\VYRAL\IOptimal\pipeline\report.py',
    r'C:\Users\VYRAL\IOptimal\output\report.py',
    r'C:\Users\VYRAL\IOptimal\pipeline\produce.py',
    r'C:\Users\VYRAL\IOptimal\solver\solve.py',
]

errors = 0
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK: {f.split(chr(92))[-1]}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f.split(chr(92))[-1]} - {e}")
        errors += 1

if errors:
    print(f"\n{errors} file(s) have syntax errors!")
    sys.exit(1)
else:
    print(f"\nAll {len(files)} files pass syntax check!")
    sys.exit(0)
