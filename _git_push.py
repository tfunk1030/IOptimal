"""Git push with verbose error output."""
import subprocess
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_push_result.txt'

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

run(['branch', '-v'], 'BRANCH')
run(['log', '--oneline', '-3'], 'LOG')
run(['remote', '-v'], 'REMOTE')
run(['pull', '--rebase', 'origin', 'codextwo'], 'PULL REBASE')
run(['push', 'origin', 'codextwo'], 'PUSH')

with open(OUT, 'w') as f:
    f.write('\n'.join(results))
