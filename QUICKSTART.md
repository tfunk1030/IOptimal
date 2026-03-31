# IOptimal Quick Start
**Get a setup recommendation in 3 commands**

---

## Step 1: Install
```bash
pip install -r requirements-dev.txt
```

---

## Step 2: Get a Setup from Your IBT File

```bash
python -m ioptimal produce --car bmw --ibt "path/to/session.ibt" --wing 16 --sto output.sto
```

Replace:
- `bmw` → your car (`ferrari`, `acura`, `cadillac`, `porsche`)
- `path/to/session.ibt` → your iRacing telemetry file
- `16` → your wing angle
- `output.sto` → where to save the setup file

**Load the `.sto` in iRacing:** `Documents\iRacing\setups\<Car>\<Track>\output.sto`

---

## Step 3: Qualify/Race Mode

```bash
# Qualifying — more aggressive
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 14 --scenario quali --sto quali.sto

# Race — maximize stability
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 14 --scenario race --sto race.sto
```

---

## That's It

The solver reads your telemetry, runs physics, and outputs a garage setup. Calibrated models for BMW and Ferrari load automatically.

---

## Common Commands

| What you want | Command |
|--------------|---------|
| Setup from telemetry | `python -m ioptimal produce --car bmw --ibt session.ibt --wing 16 --sto out.sto` |
| Multi-session (best of several) | `python -m ioptimal produce --car bmw --ibt s1.ibt s2.ibt --wing 16` |
| No telemetry (physics only) | `python -m ioptimal solve --car bmw --track sebring --wing 16` |
| Store session for learning | `python -m ioptimal ingest --car bmw --ibt session.ibt` |
| Check calibration status | `python -m ioptimal calibrate --car ferrari --status` |
| What to do in iRacing to calibrate | `python -m ioptimal calibrate --car acura --protocol` |

---

## Car Support

| Car | Quality | Notes |
|-----|---------|-------|
| BMW M Hybrid V8 | ✅ Best | Fully calibrated, optimizer active at Sebring |
| Ferrari 499P | ✅ Good | Calibrated from 10 sessions + spring lookup |
| Acura ARX-06 | ⚠️ OK | Partial calibration — check roll dampers manually |
| Cadillac V-Series.R | ⚠️ OK | Physics-based, needs more sessions |
| Porsche 963 | ❌ Estimates | No data yet — use output as starting point only |

---

## Wing Ranges

| Car | Range |
|-----|-------|
| BMW / Ferrari / Cadillac / Porsche | 12–17° |
| Acura | 6–10° |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Unknown car 'xxx'` | Use: `bmw` `ferrari` `acura` `cadillac` `porsche` |
| `.sto doesn't load in iRacing` | Check `--report` output for warnings |
| `No valid laps found` | Drive a complete lap before ending the session |
| Want a wider setup search | Add `--free` (legal manifold search) |

---

*Full reference: `CLI_GUIDE.md` | Calibration: `CALIBRATION_GUIDE.md`*
