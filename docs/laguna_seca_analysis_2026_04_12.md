# Laguna Seca Deep Analysis — Porsche 963 Pipeline Issues

## Context

First Porsche 963 sessions at Laguna Seca (2 IBTs, April 12, 2026). The pipeline produced a setup from the second (stiffer) session but has **critical calibration and solver issues** that make parts of the output unreliable. This plan covers root causes, engineering assessment, and code fixes.

---

## IBT Session Data (Parsed from IBT)

### Session 1 (00-36-46) — FIRST, Exploratory Soft Setup
- **Best lap**: 75.778s (Lap 5 of 5 valid laps)
- **Springs**: Front heave **230 N/mm**, Rear third **220 N/mm**, Rear coil 180
- **Dampers**: Front HS comp **2**, Rear HS comp **4** (very soft HS)
- **Rear 3rd dampers**: LS 4/2, HS 4/2 (soft)
- **Perches**: Front heave 61mm, Rear pushrod +18.5mm
- **Geometry**: Camber -2.8/-1.8, Toe -1.0/-1.1
- **Static RH**: Front 30.1mm, Rear 49.2mm

**Telemetry (Best Lap 5)**:
- Speed: 76–271 kph, median 170 kph
- **Front RH at >200kph**: LF mean=18.3 std=5.3 **min=-2.3mm**, RF mean=17.0 std=6.4 **min=-3.6mm**
- **Front RH all speeds**: LF **min=-11.5mm**, RF **min=-16.0mm** (severe bottoming on kerbs/braking)
- **Bottoming at >150kph**: LF=20, **RF=47 samples**, RR=2
- Peak lateral G: **3.56g** (low — cold tyres, less confidence)
- Shock vel p99: LF=183, RF=188, LR=185, RR=204 mm/s
- **Tyre surface temps**: 43–57°C (very cold, well below 85°C window)
- Tyre pressures: F 168-171 kPa (24.4-24.9 PSI), R 165-167 kPa (24.0-24.2 PSI)
- CFSR >200kph mean=89.2mm min=70.6mm (no splitter bottoming)

### Session 2 (00-51-10) — SECOND, Stiffened After Bottoming
- **Best lap**: 75.085s (Lap 11 of 5 valid laps) — **0.7s FASTER**
- **Springs**: Front heave **290 N/mm** (+60), Rear third **250 N/mm** (+30), Rear coil 180
- **Dampers**: Front HS comp **7** (+5), Rear HS comp **7** (+3) (stiff HS)
- **Rear 3rd dampers**: LS 5/5, HS 5/5 (stiffened across the board)
- **Perches**: Front heave 63mm (+2), Rear pushrod +16.0mm (-2.5)
- **Geometry**: Camber -2.8/-1.9, Toe -1.2/-1.6 (more toe both ends)
- **Static RH**: Front 30.5mm (+0.4), Rear 48.7mm (-0.5)

**Telemetry (Best Lap 11)**:
- Speed: 73–271 kph, median 174 kph
- **Front RH at >200kph**: LF mean=20.4 std=4.8 **min=-4.7mm**, RF mean=18.9 std=6.3 **min=-0.8mm**
- **Front RH all speeds**: LF **min=-4.7mm**, RF **min=-16.3mm** (still bottoming but LF improved)
- **Bottoming at >150kph**: LF=16 (↓from 20), **RF=29** (↓from 47), RR=0
- Peak lateral G: **4.22g** (↑from 3.56 — much more confidence on stiffer platform)
- Shock vel p99: LF=148 (↓from 183), RF=169 (↓from 188) — stiffer HS dampers working
- **Tyre surface temps**: 45–60°C (slightly warmer, still cold — 5 more laps in session)
- Tyre pressures: F 171-174 kPa (24.8-25.3 PSI), R 167-169 kPa (24.3-24.5 PSI)
- CFSR >200kph mean=91.6mm min=71.5mm (improved)

### Both Sessions Share (Unchanged)
- Wing 17°, Roll spring 100 N/mm, Rear coil 180 N/mm, Rear spring perch 99mm
- ARBs: Connected/1 front, Stiff/10 rear
- Brake bias 44%, Pad Medium, MC F/R 20.6/22.2mm
- Diff 90Nm/45°/70°/4 plates, TC 4/6
- Fuel 58L, Short gears, Front roll LS=6

---

## Engineering Analysis

### What the Driver Did Right
The driver's tuning direction was **correct and aggressive**:
1. **Stiffened front heave +60 N/mm** → reduced LF bottoming (20→16 samples), raised mean LF RH at speed by 2.1mm
2. **Stiffened front HS dampers +5 clicks** → shock vel p99 dropped 35mm/s (183→148 LF)
3. **Stiffened rear third +30 N/mm and 3rd dampers** → better rear platform support
4. **Added rear toe-in** (-1.1→-1.6) → more rear stability
5. **Result**: 0.7s faster, peak lateral G 3.56→4.22g (more grip utilized), 40% fewer front bottoming events

### What's Still Wrong (Track Physics)
1. **Front bottoming persists** — RF still hits -16.3mm and has 29 bottoming samples at speed. The front platform needs MORE support.
2. **Tyre temps far from window** — 45-60°C surface vs 85°C target. Both sessions are too short (5 laps) for Vision tread conditioning. This is expected, not a setup failure.
3. **RF consistently worse than LF** — RF bottoming is 2x LF in both sessions. This suggests asymmetric loading (Laguna Seca's Corkscrew and off-camber sections load the RF heavily). Or a left-right pushrod/perch asymmetry.
4. **Rear RH stable** — rear never bottoms, 33mm at speed. No rear platform concern.

### What the Pipeline Got Right
- **Direction of heave change**: recommending even stiffer (310 N/mm heave) is correct — driver went 230→290 and still has bottoming
- **Assessment "dangerous"**: correct — 11 vortex burst events confirmed by negative front ride heights
- **Damper LS values unchanged**: correct — driver's LS dampers (11/10 front, 11/11 rear) are good, no need to change
- **ARB config unchanged**: correct — Connected/Stiff is a reasonable baseline
- **Tyre pressure recommendation**: 152 kPa cold is reasonable (minimum allowed)

### What the Pipeline Got Wrong
1. **Front heave output: 340 N/mm (total)** — Report conflates heave spring (310) with total axle stiffness (310 + 2×roll_spring_contribution). Misleading label.
2. **Torsion bar OD: 13.90mm** — Nonsensical for Porsche (uses roll spring, not torsion bar)
3. **Front heave perch: 40mm** — Solver wanted 5mm, clamped to 40mm minimum. This 35mm clamping invalidates the ride height prediction.
4. **Rear spring: 180 N/mm in card vs 110 N/mm in solver trace** — Driver anchor fires silently; trace shows pre-anchor value
5. **Calibration corrupted** — front RH model R²=0.23 from cross-track contamination
6. **LLTD gap 39.1% vs 51.2%** — known epistemic limitation, not a Laguna-specific issue

---

## Critical Code Issues

### Issue 1: Cross-Track Calibration Contamination (BLOCKER)
**Root cause**: `auto_calibrate.py` pools ALL sessions regardless of track.

- `_setup_key()` at `auto_calibrate.py:74-109` — track NOT in fingerprint
- `fit_models_from_points()` at lines 983-995 — all data pooled into single regression
- `GarageSetupState` in `garage.py:38-62` — no track field

Adding 2 Laguna Seca sessions to 38 Algarve sessions destroyed front RH model (R² 0.999→0.230). The two tracks have completely different ride height physics (elevation, banking, surface grip, aero ref speed).

**Fix approach**:
1. Add `track_key` to `CalibrationPoint` fingerprinting (already stored but unused)
2. In `fit_models_from_points()`, group by `track_key()`, fit per-track models
3. Store as `{track: model_dict}` in models.json
4. Fall back to pooled model when per-track data < 5 unique setups
5. Reuse existing `car_model/registry.py:track_key()` for normalization

**Files**: `car_model/auto_calibrate.py`, `car_model/garage.py`, `data/calibration/porsche/models.json`

### Issue 2: Porsche Roll Spring vs Torsion Bar Confusion
**Root cause**: Porsche has `front_is_roll_spring=True` and `front_torsion_c=0.0` (`cars.py:2784`). No front torsion bar OD adjustment exists. But the solver outputs `front_torsion_od_mm=18.20` and the report shows "Torsion: 13.90mm OD 0.123 Turns" (all meaningless).

**Fix approach**:
1. `output/report.py:617` — Show "Roll spring: 100 N/mm" when `front_is_roll_spring=True`
2. `solver/corner_spring_solver.py` — Skip torsion OD computation for roll-spring cars
3. `output/garage_validator.py` — Use (0,0) range for torsion OD when Porsche

**Files**: `output/report.py`, `solver/corner_spring_solver.py`, `output/garage_validator.py`

### Issue 3: Solver Ignoring Garage Range Constraints
**Root cause**: Solver computes heave perch=5mm but Porsche range is 40-90mm. Solver doesn't know about garage ranges; validator clamps after the fact.

A 35mm perch clamping fundamentally changes the spring preload and invalidates the ride height calculation. The solver should use garage ranges as optimization bounds.

**Fix approach**: Pass `garage_ranges` to `heave_solver.py` and `corner_spring_solver.py` as bounds.

**Files**: `solver/heave_solver.py`, `solver/corner_spring_solver.py`, `solver/solve_chain.py`

### Issue 4: Trace Shows Pre-Anchor Values (Misleading)
The solver trace reports `rear_spring_rate_nmm=110.00` but the final output is 180 N/mm (driver-anchored). The trace should show the FINAL value with an annotation like "(anchored from driver: 180)".

**Files**: `solver/corner_spring_solver.py`, `solver/solve_chain.py` trace output

---

## Immediate Workaround (No Code Changes)

1. **Re-run calibration excluding Laguna Seca data**:
   ```bash
   # Move Laguna IBTs out of ibtfiles/ temporarily
   mkdir ibtfiles/laguna_hold
   mv ibtfiles/porsche963gtp_lagunaseca* ibtfiles/laguna_hold/
   python -m car_model.auto_calibrate --car porsche --ibt-dir ibtfiles/
   # Move them back
   mv ibtfiles/laguna_hold/porsche963gtp_lagunaseca* ibtfiles/
   rmdir ibtfiles/laguna_hold
   ```
   This restores Algarve-only models (front RH R²≈0.999).

2. **Use Algarve-calibrated models for Laguna Seca** — not track-specific but at least the coefficients aren't corrupted.

3. **Manual verification**: After pipeline output, check that:
   - Front heave perch is within 40-90mm
   - No torsion bar OD values appear (Porsche doesn't have them)
   - Rear spring matches driver's 180 N/mm (until proper physics target is computed)

---

## Setup Engineering Recommendations (For the Driver)

Based on the telemetry, the driver should continue stiffening:

1. **Front heave → 310-340 N/mm** (currently 290, still bottoming). The pipeline's direction is correct.
2. **Front HS comp → 8-9 clicks** (currently 7). RF bottoming is the worst offender; HS dampers control bump absorption.
3. **Front heave perch → 65-68mm** (currently 63). More preload = higher platform under load.
4. **Keep running laps** — tyre temps need 15+ laps to reach window. Both sessions were too short for meaningful thermal data. The "dangerous" vortex events may reduce as tyres condition and the driver adapts lines.
5. **Investigate RF asymmetry** — RF bottoms 2x more than LF. Check if Laguna's track characteristics (off-camber T1-T2, Corkscrew) explain this or if there's a setup asymmetry to address.

---

## Verification Plan (After Code Fixes)

1. Run `auto_calibrate --car porsche --ibt-dir ibtfiles/` — verify per-track models created
2. Algarve models: front RH R²≥0.99, rear R²≥0.91 (restored)
3. Laguna models: should show "insufficient data (2/5 unique setups)" or weak models with honest R²
4. Run `pipeline.produce` for Laguna Seca — verify:
   - No torsion OD in report for Porsche
   - Front heave perch within 40-90mm
   - No "clamped" warnings for Porsche-specific parameters
5. Run `python -m pytest tests/test_setup_regression.py` — no regressions
