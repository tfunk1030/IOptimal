"""Git: verbose push to see exact error."""
import subprocess
import os

os.chdir(r'C:\Users\VYRAL\IOptimal')
GIT = r'C:\Program Files\Git\cmd\git.exe'
OUT = r'C:\Users\VYRAL\IOptimal\_git_push_result5.txt'

env = os.environ.copy()
env['GIT_TRACE'] = '1'
env['GIT_CURL_VERBOSE'] = '1'

r = subprocess.run(
    [GIT, 'push', 'origin', 'codextwo', '--verbose'],
    capture_output=True, text=True, timeout=120,
    env=env,
)

with open(OUT, 'w') as f:
    f.write(f"STDOUT:\n{r.stdout}\n\nSTDERR:\n{r.stderr}\n\nExit: {r.returncode}")
