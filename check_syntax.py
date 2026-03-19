import ast, sys

files = ['solver/objective.py', 'solver/laptime_sensitivity.py']
ok = True
for f in files:
    try:
        ast.parse(open(f).read())
        print(f"{f}: syntax OK")
    except SyntaxError as e:
        print(f"{f}: SYNTAX ERROR - {e}")
        ok = False

if ok:
    print("\nAll files pass syntax check")
else:
    print("\nSYNTAX ERRORS FOUND")
    sys.exit(1)
