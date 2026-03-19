"""Syntax check for modified files - writes results to file."""
import py_compile
import sys

outfile = r'C:\Users\VYRAL\IOptimal\_syntax_result2.txt'
files = [
    r'C:\Users\VYRAL\IOptimal\solver\laptime_sensitivity.py',
    r'C:\Users\VYRAL\IOptimal\output\search_report.py',
    r'C:\Users\VYRAL\IOptimal\pipeline\report.py',
    r'C:\Users\VYRAL\IOptimal\output\report.py',
    r'C:\Users\VYRAL\IOptimal\pipeline\produce.py',
    r'C:\Users\VYRAL\IOptimal\solver\solve.py',
]

results = []
errors = 0
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        results.append(f"OK: {f.split(chr(92))[-1]}")
    except py_compile.PyCompileError as e:
        results.append(f"FAIL: {f.split(chr(92))[-1]} - {e}")
        errors += 1

summary = f"\n{errors} errors" if errors else f"\nAll {len(files)} files OK!"
results.append(summary)

with open(outfile, 'w') as fh:
    fh.write('\n'.join(results))

sys.exit(errors)
