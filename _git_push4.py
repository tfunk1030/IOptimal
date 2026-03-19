"""Git: diagnose push failure and force push."""
import subprocess
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_push_result4.txt'

results = []

def run(args, label):
    r = subprocess.run([GIT] + args, capture_output=True, text=True, timeout=120)
    results.append(f"=== {label} ===")
    if r.stdout.strip():
        results.append(r.stdout.strip())
    if r.stderr.strip():
        results.append(f"STDERR: {r.stderr.strip()}")
    results.append(f"Exit: {r.returncode}")
    results.append("")
    return r.returncode

# Check remote tracking
run(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], 'UPSTREAM')
run(['rev-list', '--count', 'origin/codextwo..HEAD'], 'AHEAD')
run(['rev-list', '--count', 'HEAD..origin/codextwo'], 'BEHIND')

# Try force push (this is the codextwo branch, not main)
run(['push', '--force', 'origin', 'codextwo'], 'FORCE PUSH')

with open(OUT, 'w') as f:
    f.write('\n'.join(results))
