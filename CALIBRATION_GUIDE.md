# IOptimal Calibration Guide
**How to calibrate per-car physics models from your IBT files**

---

## What Is Calibration?

iOptimal's solver uses physics models to predict how each car behaves (spring rates, ride heights, deflections, aero compression). Out of the box, only BMW/Sebring is fully calibrated from real data. Ferrari, Acura, Cadillac, and Porsche use estimates.

**Calibration replaces those estimates with models fitted from YOUR IBT files.** After calibration:
- Ride height predictions are accurate for your car
- Deflection values in saved .sto files will be correct (not BMW defaults)
- Aero compression uses your measured data (not BMW's 15mm assumption)
- LLTD target is calibrated from your corner weight data

The system reads the `CarSetup` block embedded in every IBT file — the same data iRacing uses to display setup values in the garage.

---

## Current Calibration Status (After Running on All IBT Files)

| Car | Sessions | Models | Rear RH R² | Heave Defl R² | Aero | Spring Lookup | LLTD Target |
|-----|---------|--------|------------|---------------|------|---------------|-------------|
| **BMW** | 10 | ✅ Complete | 1.000 | 0.988 | front=15.0mm rear=5.2mm | N/A (direct N/mm) | 0.472 |
| **Ferrari** | 10 | ✅ Complete + Spring Lookup | 1.000 | 0.985 | front=15.2mm rear=6.2mm | ✅ idx 2→115.2 N/mm, idx 1→105.0 N/mm | 0.476 |
| **Acura** | 8 | ⚠️ Partial (3/6) | 0.300 (weak) | 0.744 | front=7.1mm | Not yet | 0.470 |
| **Cadillac** | 0 | ❌ No data | — | — | — | — | — |
| **Porsche** | 0 | ❌ No data | — | — | — | — | — |

**Ferrari** is fully calibrated including the spring lookup table (built from `ferrari.json`). The solver now knows that torsion bar OD index 2 = 115.2 N/mm and index 1 = 105.0 N/mm for the corner springs.

**Acura is partial** because the 8 sessions don't have enough pushrod variation to fit a good rear RH model. Running 2-3 sessions with different rear pushrods (+10mm, baseline, -10mm) will fix this.

---

## Commands Reference

### 1. Run a Setup (Primary Use)
```bash
# Generate a setup from a telemetry session (calibration loads automatically)
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 14 --sto output.sto

# Generate a setup for Acura
python -m ioptimal produce --car acura --ibt session.ibt --wing 8 --sto output.sto
```
**When to use:** Every time you want a setup recommendation. The calibrated models are loaded automatically — you don't need to do anything special.

---

### 2. Check Calibration Status
```bash
python -m ioptimal calibrate --car ferrari --status
python -m ioptimal calibrate --car acura --status
python -m ioptimal calibrate --car bmw --status
```
**When to use:** After adding new IBT sessions. Shows R² scores per model, what's missing, and recommendations.

---

### 3. Add New IBT Sessions to Calibration
```bash
# Add specific IBT files
python -m ioptimal calibrate --car ferrari --ibt session1.ibt session2.ibt

# Scan a directory for all IBT files matching the car
python -m ioptimal calibrate --car ferrari --ibt-dir "C:\Users\YourName\Documents\iRacing\telemetry"

# Scan the project's ibt/ directory
python -m ioptimal calibrate --car acura --ibt-dir ibt
```
**When to use:** After every racing session. The system deduplicates automatically — running it twice on the same file is safe. Once you have 5+ unique-setup sessions, models auto-fit.

---

### 4. Get Step-by-Step Calibration Instructions
```bash
python -m ioptimal calibrate --car acura --protocol
python -m ioptimal calibrate --car ferrari --protocol
python -m ioptimal calibrate --car cadillac --protocol
```
**When to use:** When calibration is incomplete and you want to know exactly what to do in iRacing to fill the gaps. The protocol adapts to what's already calibrated.

---

### 5. Add Spring Rate Lookup Table (Ferrari/Acura — from setupdelta.com JSON)
```bash
# Ferrari: ferrari.json is already in the project root — run this now!
python -m ioptimal calibrate --car ferrari --sto-json ferrari.json

# For new setups at different torsion OD settings, upload .sto to setupdelta.com
# and run again — each run adds one more index→N/mm data point
python -m ioptimal calibrate --car ferrari --sto-json new_setup.json
```
**When to use:** You have `ferrari.json` already — this is done! For additional data points at different spring settings, get more JSONs from setupdelta.com.

**How it works:** The system auto-detects the torsion bar OD index from the `"Torsion bar O.D."` rows in the JSON — no manual input needed. It reads `fSideSpringRateNpm` to get the actual N/mm rate for that index.

**Note:** Spring lookup is optional. The solver works without it — it uses the estimated linear mapping. Each additional index you calibrate improves accuracy across the full spring range.

---

### 6. Ingest a Session (Learning + Calibration)
```bash
# Full ingest: adds to calibration dataset AND knowledge store
python -m ioptimal ingest --car ferrari --ibt session.ibt
```
**When to use:** After important sessions where you want to store learning data (lap times, handling diagnosis, delta detection). This also automatically adds a calibration data point — it does everything `calibrate --ibt` does, plus more.

---

### 7. Re-Fit After Adding Many Sessions
```bash
python -m ioptimal calibrate --car acura --refit
```
**When to use:** If you've added many IBT files and want to force a fresh fit of all models from scratch. Useful after adding 5+ new unique setups.

---

### 8. Clear Calibration Data (Reset)
```bash
python -m ioptimal calibrate --car acura --clear
```
**When to use:** If you want to start calibration from scratch (e.g., after finding out your data was from the wrong car version). This clears `data/calibration/acura/` only — it does NOT affect your IBT files.

---

## When to Run What: Decision Tree

```
After every iRacing session:
  └─ python -m ioptimal ingest --car <car> --ibt <session.ibt>
       ↳ Automatically adds calibration data + stores knowledge

Want a setup recommendation:
  └─ python -m ioptimal produce --car <car> --ibt <session.ibt> --wing <N> --sto output.sto

Want to know calibration status:
  └─ python -m ioptimal calibrate --car <car> --status

Calibration shows gaps ("needs calibration"):
  └─ python -m ioptimal calibrate --car <car> --protocol
       ↳ Follow the iRacing sweep instructions
       ↳ python -m ioptimal calibrate --car <car> --ibt-dir <path to new IBTs>

Have a setupdelta.com JSON:
  └─ python -m ioptimal calibrate --car ferrari --sto-json ferrari.json
       ↳ Instantly adds spring rate data point
```

---

## What Each Model Does

| Model | What It Calibrates | Why It Matters |
|-------|-------------------|----------------|
| **Rear Ride Height** | rear_rh = f(pushrod, springs, perch, fuel) | Accurate rear RH targeting in solver |
| **Front Ride Height** | front_rh = f(heave, perch, camber, pushrod) | Accurate front RH targeting |
| **Heave Spring Defl** | garage display heave deflection values | .sto files load correctly in-game |
| **Shock Deflection** | garage display shock deflection values | .sto files load correctly in-game |
| **Aero Compression** | static_rh - rh_at_speed per wing angle | Correct dynamic RH predictions |
| **LLTD Target** | front weight distribution from corner weights | ARB solver targets correct balance |
| **m_eff (optional)** | effective heave mass from telemetry sigma | More accurate heave spring sizing |
| **Spring Lookup** | index→N/mm for Ferrari/Acura indexed springs | Correct spring rate in heave solver |

---

## How to Calibrate Acura (Currently Partial)

The Acura calibration is incomplete because the existing 8 sessions have similar rear pushrods (all around −35 to −41mm). To fix this:

**In iRacing (15 minutes):**
1. Load your current Acura setup at Hockenheim practice
2. Drive 3 clean laps → IBT saved
3. Change **rear pushrod only** to −25mm (more negative = more compression)
4. Drive 3 clean laps → IBT saved
5. Change **rear pushrod only** to −50mm
6. Drive 3 clean laps → IBT saved

**In terminal:**
```bash
python -m ioptimal calibrate --car acura --ibt-dir "C:\path\to\new\sessions"
```

Expected result: Rear RH model R² improves from 0.30 to >0.85.

---

## How to Calibrate Cadillac (No Data Yet)

```bash
# See what the protocol recommends:
python -m ioptimal calibrate --car cadillac --protocol
```

The protocol will ask for 5 sessions varying torsion bar OD and pushrods. About 30 minutes in iRacing at any track.

---

## Verification: Models Are Active

Run a solver with calibration enabled vs disabled to verify:

```bash
# With calibration (default — uses your calibrated models):
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 14

# Without calibration (uses BMW defaults — for comparison):
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 14 --no-learn

# Check what the solver loaded:
python -m ioptimal calibrate --car ferrari --status
```

The solver will print `[learn] Applied N corrections from M sessions` when calibrated models are active.

---

## Data Storage

Calibration data is stored in `data/calibration/`:
```
data/calibration/
  bmw/
    calibration_points.json   ← raw data from each session
    models.json               ← fitted regression coefficients
  ferrari/
    calibration_points.json
    models.json
  acura/
    calibration_points.json
    models.json
```

These files are updated automatically. You never need to edit them manually.

---

## Calibration Quality Guide

| R² Score | Meaning | Action |
|----------|---------|--------|
| ≥ 0.90 | Excellent — model is accurate | ✅ No action needed |
| 0.50–0.90 | Good — acceptable accuracy | ✅ Works well |
| 0.20–0.50 | Weak — limited accuracy | ⚠️ Add more varied setups |
| < 0.20 | Poor — not enough variation in data | ❌ Need more diverse setup changes |

A low R² doesn't mean the calibration is wrong — it means the sessions were too similar to let the model learn the relationship. Try varying that parameter more across sessions.

---

*See `CLI_GUIDE.md` for full command reference and car-specific notes.*  
*See `ENGINEERING_AUDIT.md` for technical details on calibration models.*
