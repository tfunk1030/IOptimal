# Porsche 963 Calibration Plan -- Algarve Grand Prix

> Goal: Bring Porsche from "unsupported" to "exploratory+" for Algarve this week  
> Created: 2026-04-03

---

## Current Porsche 963 Status: UNSUPPORTED

### What We Have
- 6 aero maps parsed (wing 12-17)
- 2 Sebring observations (both with broken damper parsing -- all zeros)
- Roll gradient: 0.84 deg/g (low confidence, 2 sessions)
- Aero compression rough estimate: front 12mm, rear 23mm
- Global empirical model with "low" confidence
- Starting setup screenshots for Algarve (your 4 images)

### What's Missing (All Marked ESTIMATE in car model)
1. **Garage output model** -- No regression for display values (RH, deflections, slider)
2. **Ride height model** -- Uncalibrated (all zeros)
3. **Deflection model** -- Uncalibrated (16 regressions all zero)
4. **Effective mass (m_eff)** -- front=176kg, rear=2100kg (wild estimates from BMW scaling)
5. **Damper force/click** -- Using generic 18N LS, 80N HS (DSSV likely very different)
6. **ARB stiffness** -- All estimates (5000/10000/15000 front, 1500/3000/4500 rear)
7. **Torsion bar C constant** -- Using BMW value 0.0008036 (Multimatic may differ)
8. **Motion ratios** -- rear=0.60 (estimate)
9. **Pushrod geometry** -- All estimated coefficients
10. **Damper zeta targets** -- None (using uncalibrated defaults)
11. **LLTD target** -- None (using formula estimate)
12. **Setup reader** -- Dampers parsing broken (all zeros in observations)
13. **Algarve track profile** -- Doesn't exist yet

### What the Starting Setup Screenshots Tell Us

From your 4 images (Garage 61 - SOELPEC Precision Racing, Algarve):

**CHASSIS:**
| Parameter | Value |
|-----------|-------|
| Front heave spring | 180 N/mm |
| Front heave perch offset | 58.0 mm |
| Front heave spring defl | 18.9 of 66.8 mm |
| Front heave slider defl | 18.5 of 94.0 mm |
| Front roll spring | 100 N/mm |
| Front roll perch offset | 15.0 mm |
| Front roll spring defl | 0.00 of 50.0 mm |
| Front roll damper defl | 0.0 of 35.0 mm |
| Front ARB | Connected, blade 1 |
| Front toe-in | -1.2 mm |
| Front pushrod offset | -39.5 mm |
| Front RH | 30.0 mm (warning) |
| Front camber | -2.8 deg |
| Front corner weight | 2689 N |
| Rear RH | 50.0 mm |
| Rear shock defl | 34.2 of 100.0 mm |
| Rear spring defl | 13.0 of 76.8 mm |
| Rear spring perch offset | 99.0 mm |
| Rear spring rate | 180 N/mm |
| Rear camber | -1.9 deg (warning) |
| Rear toe-in | -1.5 mm |
| Rear third spring | 80 N/mm |
| Rear third perch offset | 120.5 mm |
| Rear third spring defl | 13.8 of 95.3 mm |
| Rear third slider defl | 40.5 of 84.0 mm |
| Rear ARB | Stiff, blade 2 |
| Rear pushrod offset | 30.0 mm |
| Cross weight | 50.0% |

**DAMPERS:**
| Location | LS Comp | HS Comp | HS Slope | LS Rbd | HS Rbd |
|----------|---------|---------|----------|--------|--------|
| Front heave | 7 | 11 | - | 7 | 11 |
| Front roll | 8 (LS) | 11 (HS) | 11 (slope) | - | - |
| Left rear | 7 | 10 | 11 | 5 | 10 |
| Right rear | 7 | 10 | 11 | 5 | 10 |
| Rear 3rd | 5 | 5 | - | 4 | 5 |

**BRAKES/DRIVE UNIT:**
| Parameter | Value |
|-----------|-------|
| Pad compound | Medium |
| Front master cyl | 20.6 mm |
| Rear master cyl | 22.2 mm |
| Brake bias | 44.75% |
| Brake bias target | 0 |
| Brake bias migration | 0 |
| Fuel level | 58.0 L |
| TC gain (TCLA) | 5 |
| TC slip (TCLO) | 7 |
| Gear stack | Short |
| Diff ramp | 50/75 |
| Diff clutch plates | 6 |
| Diff preload | 0 Nm |

### Key Observations from Screenshots

1. **Porsche has ROLL SPRINGS + ROLL DAMPERS** (front section) -- this is the Multimatic chassis-specific feature. Roll spring 100 N/mm with perch offset 15mm. Roll damper defl showing 0.0 of 35.0mm.
2. **Front heave has NO HS slope** (unlike corner dampers which have HS slope = 11)
3. **Rear 3rd has separate dampers** (LS/HS comp + LS/HS rbd, no slope)
4. **Rear corners have full 5-param dampers** (LS comp, HS comp, HS slope, LS rbd, HS rbd)
5. **Front roll has 3 damper params** (LS, HS, HS slope) -- different from heave (4 params)
6. **20-click DSSV dampers confirmed** -- range goes to at least 11
7. **Diff preload = 0 Nm** and **ramp = 50/75** -- very different from BMW/Acura
8. **Rear toe = -1.5mm** (toe-out, aggressive for stability)
9. **Front RH at 30.0mm with warning** -- at the floor limit

---

## Calibration Plan: 6 Steps to "Exploratory+"

### STEP 1: Fix Setup Reader for Porsche (Code Change - I do this)
**What:** The existing observations have all damper clicks = 0 because the setup reader doesn't properly parse Porsche's unique damper layout (front heave 4-param, front roll 3-param, rear corner 5-param, rear 3rd 4-param).

**Action:** I will fix `analyzer/setup_reader.py` and `car_model/setup_registry.py` to properly parse:
- Front heave dampers (LS comp, HS comp, LS rbd, HS rbd)
- Front roll dampers (LS, HS, HS slope)
- Rear corner dampers (LS comp, HS comp, HS slope, LS rbd, HS rbd)
- Rear 3rd dampers (LS comp, HS comp, LS rbd, HS rbd)
- Roll spring + perch offset
- Roll spring/damper deflections

### STEP 2: Build Algarve Track Profile (You provide data)
**What you need to do:**
1. Go to Algarve in iRacing with the Porsche 963
2. Run **5+ clean laps** at race pace (not hotlapping, normal fuel)
3. Save the IBT file
4. Give me the IBT file path

**What I'll do:** Build the TrackProfile JSON (corner speeds, braking zones, shock velocity spectrum, surface roughness, kerb locations). This is track-specific, not car-specific -- one session is enough.

### STEP 3: Garage Calibration Sweep (You provide data -- CRITICAL)
**What:** We need 6-8 varied garage screenshots to build regression models for how Porsche's display values (RH, deflections, slider positions) respond to setup changes.

**You need to do these sweeps in the Porsche garage at Algarve (take a screenshot of the CHASSIS tab after each change, then UNDO before the next):**

#### Sweep 1: Front Heave Spring (3 screenshots)
Starting from base setup, change ONLY the front heave spring:
1. **Screenshot A1:** Base setup as-is (heave = 180 N/mm) -- already have this from your images
2. **Screenshot A2:** Change front heave to **120 N/mm** -- screenshot CHASSIS tab
3. **Screenshot A3:** Change front heave to **250 N/mm** -- screenshot CHASSIS tab
4. **Undo back to 180 N/mm**

#### Sweep 2: Front Heave Perch Offset (2 screenshots)
Starting from base setup, change ONLY the front heave perch:
5. **Screenshot B1:** Change front heave perch to **40.0 mm** -- screenshot CHASSIS tab
6. **Screenshot B2:** Change front heave perch to **75.0 mm** -- screenshot CHASSIS tab
7. **Undo back to 58.0 mm**

#### Sweep 3: Rear Third Spring (2 screenshots)
Starting from base setup, change ONLY the rear third spring:
8. **Screenshot C1:** Change rear third to **120 N/mm** -- screenshot CHASSIS tab
9. **Screenshot C2:** Change rear third to **200 N/mm** -- screenshot CHASSIS tab
10. **Undo back to 80 N/mm**

#### Sweep 4: Rear Spring Rate (2 screenshots)
Starting from base setup, change ONLY the rear spring rate:
11. **Screenshot D1:** Change rear spring to **120 N/mm** -- screenshot CHASSIS tab
12. **Screenshot D2:** Change rear spring to **220 N/mm** -- screenshot CHASSIS tab
13. **Undo back to 180 N/mm**

#### Sweep 5: Front Pushrod (2 screenshots)
Starting from base setup, change ONLY the front pushrod offset:
14. **Screenshot E1:** Change front pushrod to **-30.0 mm** -- screenshot CHASSIS tab
15. **Screenshot E2:** Change front pushrod to **-45.0 mm** -- screenshot CHASSIS tab
16. **Undo back to -39.5 mm**

#### Sweep 6: Front Camber (2 screenshots)
Starting from base setup, change ONLY the front camber:
17. **Screenshot F1:** Change front camber to **-2.0 deg** -- screenshot CHASSIS tab
18. **Screenshot F2:** Change front camber to **-3.5 deg** -- screenshot CHASSIS tab
19. **Undo back to -2.8 deg**

#### Sweep 7: Fuel (1 screenshot)
20. **Screenshot G1:** Change fuel to **25 L** -- screenshot CHASSIS tab (shows weight/RH effect)
21. **Undo back to 58 L**

**Total: ~15 screenshots, ~15 minutes in the garage**

Each screenshot lets me see how iRacing's internal physics responds to one parameter change. From this I can build:
- Front/rear RH regression models
- Heave spring deflection models
- Heave slider position models
- Third spring deflection models
- Corner weight response
- Pushrod-to-RH relationship

### STEP 4: Driving Sessions for Telemetry (You provide IBT files)
**What you need to do:**
Run 5+ sessions at Algarve with the Porsche, varying the setup between sessions:

| Session | Change from base | Purpose |
|---------|-----------------|---------|
| 1 | Base setup as-is | Baseline telemetry |
| 2 | Front heave 150 N/mm (softer) | m_eff front calibration |
| 3 | Front heave 220 N/mm (stiffer) | m_eff front calibration |
| 4 | Rear ARB blade 4 (stiffer) | LLTD calibration |
| 5 | Rear ARB blade 1 (softer) | LLTD calibration |
| 6 (optional) | Front camber -3.2 | Tyre thermal calibration |
| 7 (optional) | Rear spring 140 N/mm | Rear variance calibration |

**Per session:** Run at least 5 clean laps, save the IBT file. Aim for consistent driving (no experiments mid-session).

**Give me all the IBT file paths when done.**

### STEP 5: Ingest and Calibrate (I do this)
With your data, I will:
1. Build Algarve track profile from first IBT
2. Ingest all sessions into knowledge store
3. Extract calibration points from each session
4. Fit models:
   - Pushrod-to-RH regressions (front and rear)
   - Heave spring deflection models
   - m_eff from heave sweep (front and rear)
   - LLTD from ARB sweep
   - Damper zeta targets from best-lap telemetry
   - Aero compression from at-speed RH data
5. Apply calibrated values to Porsche car model
6. Update support tier to "exploratory"

### STEP 6: Produce Setup (I do this)
Run the full pipeline:
```bash
python -m pipeline.produce --car porsche --ibt {best_session}.ibt --wing {wing} --scenario-profile single_lap_safe --sto porsche_algarve.sto
```

---

## Priority Order (If Time Is Short)

If you only have time for partial calibration:

1. **MUST DO:** Step 2 (one IBT session at Algarve) -- gives us track profile + baseline telemetry
2. **MUST DO:** Step 3 sweeps 1-3 only (heave spring + perch + third spring -- 7 screenshots) -- gives us core deflection models
3. **HIGH VALUE:** Step 4 sessions 1-3 (base + heave sweep) -- gives us m_eff calibration
4. **NICE TO HAVE:** Step 4 sessions 4-5 (ARB sweep) -- gives us LLTD calibration
5. **NICE TO HAVE:** Remaining Step 3 sweeps (pushrod, camber, fuel)

**Minimum viable calibration: 1 IBT session + 7 garage screenshots = ~30 minutes of your time.**

---

## What the Starting Setup Tells Us About Porsche Architecture

### Porsche Has a Unique Damper Layout
```
FRONT:
  Heave dampers: 4 params (LS comp, HS comp, LS rbd, HS rbd) -- NO slope
  Roll dampers:  3 params (LS, HS, HS slope)
  
REAR CORNERS:
  Per-corner:    5 params (LS comp, HS comp, HS slope, LS rbd, HS rbd)
  
REAR 3rd:
  Third dampers: 4 params (LS comp, HS comp, LS rbd, HS rbd) -- NO slope
```

This is different from:
- BMW: per-corner only (4 corners x 5 params)
- Acura: heave + roll (2 axles x heave 5 params + roll 2 params)
- Ferrari: per-corner + heave dampers

### Roll Spring System
The Porsche has **front roll springs** (100 N/mm) with their own perch offset and deflection display. This is unique to the Multimatic chassis. The roll spring adds roll stiffness independently from the ARB -- meaning the ARB blade is NOT the only roll stiffness control.

### Implications for the Solver
1. Roll stiffness calculation must include: ARB + corner springs + roll spring contributions
2. The damper solver needs to handle front heave (4-param) and front roll (3-param) separately
3. The rear third damper is a separate subsystem from the corner dampers
4. The roll spring rate is another tuneable that affects LLTD

---

## Timeline

| Day | Your Action | My Action |
|-----|------------|-----------|
| Day 1 (Today) | Take 15 garage screenshots per Step 3 | Fix Porsche setup reader, update car model for roll springs |
| Day 1 | Run 1 session at Algarve (5+ clean laps) | Build Algarve track profile |
| Day 2 | Run heave sweep sessions (3 IBTs) | Ingest, calibrate m_eff, fit deflection models |
| Day 2 | Run ARB sweep sessions (2 IBTs) | Calibrate LLTD, fit empirical models |
| Day 3 | Run with produced setup, provide IBT | Validate predictions vs measured, refine |
| Day 4+ | Race prep | Final setup + stint analysis |
