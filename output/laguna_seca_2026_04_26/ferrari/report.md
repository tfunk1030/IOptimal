# Ferrari 499P -- WeatherTech Raceway Laguna Seca

Generated: 2026-04-26
Track: WeatherTech Raceway Laguna Seca (track key `laguna_seca`)
Wing: 17 deg
Scenario profile: `single_lap_safe`
Mode: pure-physics solver (no Ferrari/Laguna Seca IBT available)

---

## 1. Calibration summary

Recalibrated from 29 Ferrari IBT files (24 Hockenheim + 5 Algarve) on 2026-04-26.
Total dataset now: **119 sessions, 18 unique setups**.

### Subsystem provenance (from setup.json:calibration_provenance)

| Subsystem | Status | Confidence | R^2 / data points |
|---|---|---|---|
| track_support | calibrated | unknown | Laguna Seca added to Ferrari supported_track_keys (physics is cross-track) |
| aero_compression | calibrated | high | 18 sessions, IBT AeroCalculator (front=15.6mm, rear=6.6mm @ 230 kph) |
| ride_height_model | calibrated | medium | R^2=0.861 (front), R^2=0.985 (rear) -- front weak after overfitting fix |
| deflection_model | calibrated | high | R^2=0.989 mean across submodels |
| pushrod_geometry | calibrated | unknown | hand-cal from IBT (rear_base=42.5mm, slope=0.45 mm/mm) |
| spring_rates | calibrated | unknown | front torsion C=0.001282 (6-pt fit, 5.2% max err), rear torsion C=0.001282 MR=0.612 (4-pt fit, 3.2% max err) |
| lltd_target | calibrated | unknown | OptimumG/Milliken physics formula (no IBT proxy used per 2026-04-08 fix) |
| damper_zeta | **uncalibrated** | unknown | needs Ferrari per-corner damper sweep + zeta measurement |
| arb_stiffness | **uncalibrated** | unknown | only 1 IBT data point with roll gradient -- need 5+ ARB-varied sessions on same springs |
| roll_gains | **uncalibrated** | unknown | needs IBT-derived steady-state roll gradient calibration |

### All 6 regression models (auto_calibrate output)

| Model | R^2 | RMSE | n |
|---|---|---|---|
| front_ride_height | 0.861 | 0.48 mm | 18 |
| rear_ride_height | 0.985 | 0.26 mm | 18 |
| front_shock_defl_static | 0.999 | 0.08 mm | 18 |
| rear_shock_defl_static | 0.989 | 0.17 mm | 18 |
| heave_spring_defl_static | 0.998 | 0.21 mm | 18 |
| heave_spring_defl_max | 1.000 | 0.01 mm | 18 |

Per-track Hockenheim model: 14 setups, best R^2=0.975. Algarve only 4 setups (need 5) -- skipped per-track fit.

### Cross-car contamination check

Auto-calibrate filtered IBT files by canonical car ID via analyzer/setup_reader.py. All 29 IBTs in this run had filenames matching ferrari499p_* and headers identifying the Ferrari 499P. No BMW/Porsche/Acura data was ingested (the 6 BMW points removed on 2026-04-10 remain excluded).

### Critical Ferrari invariants verified

- torsion_arb_coupling = 0.0 (per CLAUDE.md 2026-04-11 fix; was 0.15) -- preserved.
- Rear torsion bar rear_torsion_unvalidated = False (PR #57 confirmed model is within ~10-22% of IBT-derived wheel rates).
- Indexed heave decoder: front idx 4 -> ~110 N/mm anchor (idx 1=50, slope 20/idx); written to .sto as Value="4" (correct garage index).
- validate_and_fix_garage_correlation runs **before** Ferrari index conversion in output/setup_writer.py (PR #57).

---

## 2. Pipeline result -- which steps ran, which blocked

| Step | Status | Notes |
|---|---|---|
| **Step 1 -- Rake / Ride Heights** | ran cleanly | DF balance 46.21% vs target 48.30% (-2.09 pp gap, vortex margin 0.0 mm) |
| **Step 2 -- Heave / Third Springs** | ran cleanly | front idx 4, rear idx 0; bottoming margins 8.3 / 27.4 mm |
| **Step 3 -- Corner Springs / Torsion Bars** | ran cleanly | front TB idx 18 (max stiffness, 204.7 N/mm wheel rate), rear TB idx 0 |
| **Step 4 -- ARBs / LLTD** | **BLOCKED** | arb_stiffness + roll_gains uncalibrated |
| **Step 5 -- Wheel Geometry** | **BLOCKED** | cascades from Step 4 (geometry uses step4.k_roll_total) |
| **Step 6 -- Dampers** | **BLOCKED** | damper_zeta uncalibrated for Ferrari (no per-corner shock sweep yet) |

Per CLAUDE.md key principle 7 ("Calibrated or instruct, never guess"), blocked steps left at garage defaults -- iRacing will use its own defaults for Step 4-6 parameters. The .sto file contains only the calibrated Steps 1-3 plus supporting parameters (brake bias, diff, tyres).

---

## 3. Setup highlights

### Platform / Aero (Step 1)

- Wing: **17 deg**
- Front static RH target: **34.2 mm**
- Rear static RH target: **30.0 mm**
- Front pushrod offset: **+40.0 mm**
- Rear pushrod offset: **+40.0 mm**
- DF balance achieved: **46.21%** (target 48.30%, 2.09 pp short)
- L/D ratio: **4.12** (free optimum 4.22; cost of pinning -0.002)
- Aero state: nominal (stall margin 0.0 mm, no bottoming risk)

### Springs (Step 2 + Step 3)

- Front heave: **index 4** (~110 N/mm physical) | front perch -36 mm
- Rear heave (third): **index 0** (~30 N/mm physical) | rear perch -104 mm (validated from best-lap session 87.575 s)
- Front torsion bar: **index 18** (max, ~204.7 N/mm wheel rate; 4.31 Hz natural freq)
- Rear torsion bar: **index 0** (~150.6 N/mm wheel rate; 5.48 Hz natural freq)

### Garage-reconciled ride heights

- LF/RF ride height: **45.9 mm** (reconciled from 34.2 by garage model)
- LR/RR ride height: **40.0 mm** (reconciled from 30.0 by garage model)

### ARBs / Geometry / Dampers

Defaulted (blocked -- see blockers below).

### Supporting parameters

- Brake bias: **48.9%** (physics-computed, calibrated base 49.0% with fuel correction)
- Diff preload: **30 Nm**
- Diff ramps: **45 / 70** (coast / drive)
- Diff plates: **6**
- Tyre pressures (cold): **152 / 152 / 152 / 152 kPa**

---

## 4. Predicted competitiveness for Laguna Seca

Ferrari 499P has strong general aero performance (LMH chassis). However, with Steps 4-6 blocked, this setup's competitiveness is **mixed**:

- **Strengths:** Step 1-3 are physics-validated (high R^2 across calibrated models). Aero at the pinned-front strategy gives L/D=4.12 (close to free optimum 4.22), so straight-line pace should be acceptable.
- **Weaknesses:**
  - DF balance is 2.09 pp short of target -- **front-limited mid-corner** is likely.
  - LLTD and ARBs are at iRacing garage defaults -- **transient balance is unverified**.
  - Camber/toe and dampers at iRacing defaults -- **tyre wear and bump compliance not tuned**.
  - Stall margin is exactly 0.0 mm at vortex burst threshold -- **front floor will be sensitive to kerbs/bumps**, especially at Laguna's corkscrew (turn 8-8a).

**Honest expectation:** This is a starting-point setup, not a competitive race setup. The driver should expect to hand-tune ARBs, geometry, and dampers based on track feel. Supporting parameters (brake bias, diff, tyres) are physics-derived and should be close to optimal for fuel load 89 L.

---

## 5. Blockers -- what data Ferrari needs

To unblock the remaining steps, Ferrari needs (min_sessions=5 per CLAUDE.md):

### To unblock Step 4 (ARBs / LLTD)
- **5+ Ferrari IBT sessions** with **same springs and pushrods**, **different ARB configurations**, on a track that produces sustained lateral g (Hockenheim Stadium section, Algarve T1).
- Each session needs reliable steady-state roll gradient (constant lat_g for >1 s).
- Currently: only 1 session has usable roll-gradient data.

### To unblock Step 5 (Wheel Geometry)
- Cascades from Step 4 (geometry uses step4.k_roll_total).
- Once Step 4 unblocks, Step 5 should run from existing camber / toe baselines.
- Optional: **5+ sessions varying camber +/-0.5 deg** for tyre temperature spread calibration.

### To unblock Step 6 (Dampers)
- Ferrari needs damper_zeta calibration. This requires **5+ controlled per-corner damper sweep** sessions with explicit changes to LS/HS comp/rbd at known shock velocity ranges.
- Currently no Ferrari damper-sweep IBT exists.

### Lower-priority
- Ferrari rear ride-height model is **calibrated** (R^2=0.985); front model is **medium** (R^2=0.861). 10+ more diverse setups (varying heave perch + heave spring index) would push front R^2 above 0.95.
- ARB stiffness coefficients in cars.py are **ESTIMATE** (3000 / 1500 N/mm-deg per step). Once Step 4 unblocks via roll-gradient sessions, these can be validated and tightened.

---

## 6. Files generated

- setup.sto -- iRacing-compatible XML (3.8 KB; Steps 1-3 + supporting params)
- setup.json -- full solver output incl. calibration_provenance, calibration_blocked: [4, 5, 6], all step details
- report.md -- this file
