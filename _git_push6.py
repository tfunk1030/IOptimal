"""Git: push with PATH fix for git-lfs."""
import subprocess
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_push_result6.txt'

# Fix PATH so git-lfs can find git
env = os.environ.copy()
git_paths = r'C:\Program Files\Git\cmd;C:\Program Files\Git\mingw64\bin;C:\Program Files\Git\usr\bin'
env['PATH'] = git_paths + ';' + env.get('PATH', '')

r = subprocess.run(
    [GIT, 'push', 'origin', 'codextwo'],
    capture_output=True, text=True, timeout=120,
    env=env,
)

with open(OUT, 'w') as f:
    f.write(f"STDOUT:\n{r.stdout}\n\nSTDERR:\n{r.stderr}\n\nExit: {r.returncode}")
