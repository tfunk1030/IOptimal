## IOptimal quickstart

This is the shortest path to get useful output from the solver.

If you want the full guide, see:

- `docs/usage_and_calibration_guide.md`
- `docs/calibration_workflow.md`

---

## 1. Install requirements

You need Python 3.11+ plus the core Python dependencies.

```bash
python3 -m pip install numpy scipy openpyxl
```

For local development and test runs:

```bash
python3 -m pip install -r requirements-dev.txt
```

---

## 2. Pick the right command

If you are on **PowerShell**, prefer the commands exactly as one-line examples
below. The Bash line continuation character `\` will not work in PowerShell.

### Best normal workflow: one IBT -> one recommended setup

```bash
python3 -m pipeline.produce --car bmw --ibt "path/to/session.ibt" --wing 17 --scenario-profile single_lap_safe --sto output.sto
```

Use this when you have:
- a real IBT file
- the current setup embedded in session info
- a target wing angle

This is the **main telemetry-backed workflow**.

---

### Analyze only, no setup export

```bash
python3 -m analyzer --car ferrari --ibt "path/to/session.ibt"
```

Use this when you want:
- diagnosis
- engineering report
- no `.sto` yet

---

### Track-only solve when no telemetry exists

```bash
python3 -m solver.solve --car cadillac --track silverstone --wing 15 --scenario-profile single_lap_safe
```

Use this only when:
- you do **not** have an IBT yet
- you want a physics-only starting point

This is weaker than the telemetry-backed path.

---

## 3. Choose the right car expectation

Do not expect equal accuracy for every car.

### Strongest path right now
- **BMW at Sebring**

### Improving but still less certain
- Ferrari
- Acura
- Cadillac
- Porsche

If a car/track path is only partial or exploratory:
- treat output as engineering guidance
- verify in the sim
- do not assume it is truly optimal

---

## 4. Most useful flags

### Choose a specific lap

```bash
--lap 12
```

### Save JSON output too

```bash
--json output.json
```

### Run a more aggressive or race-focused profile

```bash
--scenario-profile quali
--scenario-profile sprint
--scenario-profile race
```

### Search the legal setup manifold

```bash
--free
```

This asks the solver to search for a better accepted candidate starting from
the pinned baseline.

### Disable learner corrections

```bash
--no-learn
```

Useful when you want a cleaner physics-only comparison.

---

## 5. What files you should keep

For any useful run, save:

- the `.ibt`
- the generated `.sto`
- the JSON output if you requested one
- the terminal/report output

If you later want to calibrate the car more accurately, also keep:

- garage screenshots
- setup row dump JSON
- `.sto` from the original session if available

---

## 6. If the output looks wrong

Check these first:

1. Did you use a clean push lap?
2. Is the wing correct?
3. Is the car/track path actually calibrated?
4. Are you using a realistic fuel load?
5. Is the current setup embedded correctly in the IBT?

If the path is not well calibrated yet, the solver may still:
- parse the setup correctly
- diagnose handling reasonably
- but output a setup that is only partially correlated with the real garage truth

---

## 7. If you want to help improve the car model

Use the calibration scaffold:

```bash
python3 -m calibration.cli create-sample-pack --root-dir data/calibration/raw --car ferrari --track sebring --sample-id ferrari_sebring_001 --sample-type garage_static
```

Then see:

- `docs/calibration_workflow.md`
- `docs/usage_and_calibration_guide.md`

---

## 8. Good starter commands

### BMW / Sebring / one telemetry session

```bash
python3 -m pipeline.produce --car bmw --ibt "bmw_sebring.ibt" --wing 17 --scenario-profile single_lap_safe --json bmw_sebring.json --sto bmw_sebring.sto
```

### Ferrari / analyze only

```bash
python3 -m analyzer --car ferrari --ibt "ferrari_session.ibt"
```

### Acura / exploratory telemetry-backed solve

```bash
python3 -m pipeline.produce --car acura --ibt "acura_session.ibt" --wing 8 --scenario-profile single_lap_safe --json acura.json
```

---

## 9. Rule of thumb

- If you have an IBT: use `pipeline.produce`
- If you only want a report: use `analyzer`
- If you do not have telemetry yet: use `solver.solve`
- If you want to improve the model itself: use `calibration.cli`
