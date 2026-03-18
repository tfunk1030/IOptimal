"""Git: add learning data, commit, then force push."""
import subprocess
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_push_result3.txt'

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

# Add the modified learning data
run(['add', 'data/learnings/'], 'ADD LEARNINGS')

# Commit
run(['commit', '-m', 'Update learning data from justified pipeline run'], 'COMMIT LEARNINGS')

# Try force push
run(['push', '--force-with-lease', 'origin', 'codextwo'], 'PUSH')
run(['log', '--oneline', '-5'], 'LOG')

with open(OUT, 'w') as f:
    f.write('\n'.join(results))
