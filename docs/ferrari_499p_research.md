# Ferrari 499P — iOptimal Research Document
*Generated: 2026-03-19 | Sources: Wikipedia, Racecar Engineering, CoachDaveAcademy, IBT telemetry (6 Sebring sessions), cars.py audit*

---

## 1. Real-Car Architecture (What Makes Ferrari Different)

| Attribute | Ferrari 499P | BMW M Hybrid V8 |
|---|---|---|
| Regs | LMH (bespoke) | LMDh (Dallara spec) |
| Chassis | Ferrari/Dallara carbon monocoque | Dallara LMP2 base |
| Engine | 3.0L twin-turbo V6 (F163CG), stressed member | 4.4L twin-turbo V8 |
| Power | 660 hp ICE + 268 hp hybrid = ~680 hp total | 670 hp (ICE only) |
| Hybrid | 200 kW front axle (AWD above 190 kph) | None |
| Drivetrain | RWD below 190 kph, AWD above | RWD always |
| Battery | Ferrari 800V, 200 kW (4× bigger than LMDh spec 50 kW) |
| Weight | 1,030 kg (same GTP minimum) | 1,030 kg |
| Wheelbase | **2,740 mm** (confirmed) | ~2,740 mm (Dallara) |
| Suspension | Double wishbone **pushrod** both axles | Pushrod both axles |
| Front corner spring | **Torsion bars** | Torsion bars |
| Rear corner spring | **Torsion bars** (NOT coil) | Coil springs |
| Tyres | Michelin 29/71-18 front, 34/71-18 rear | Michelin 31/71-18 est. |
| Brakes | Brembo carbon 380/355mm, 6-piston mono | Brembo similar |

**Key difference from BMW:** Ferrari uses torsion bars on BOTH axles, not coil on rear. The "rear_spring" in iRacing is actually a rear torsion bar OD index (0–18), not N/mm.

---

## 2. iRacing Parameter Schema — Critical: All Springs Are INDEXED

**The Ferrari does NOT use physical N/mm values in the garage.** Every spring/OD parameter is an integer index. This is the single biggest modeling challenge.

### Front Torsion Bar (corner spring)
- Garage parameter: `front_torsion_od_mm` — range **0–18** (index, not mm)
- Physical mapping (estimated, C = 0.0008036, OD range 16.0–25.0 mm):

| Index | OD (mm) | Wheel Rate (N/mm) | Natural Freq (176 kg) |
|---|---|---|---|
| 0 | 16.00 | 52.7 | 2.76 Hz |
| 1 | 16.50 | 59.6 | 2.93 Hz |
| 2 | 17.00 | 67.1 | 3.11 Hz |
| **3** | **17.50** | **75.4** | **3.29 Hz** ← observed baseline |
| 4 | 18.00 | 84.4 | 3.48 Hz |
| 5 | 18.50 | 94.1 | 3.67 Hz |
| 6 | 19.00 | 104.7 | 3.88 Hz |
| 7 | 19.50 | 116.2 | 4.08 Hz |
| 8 | 20.00 | 128.6 | 4.29 Hz |
| 9 | 20.50 | 141.9 | 4.51 Hz |
| 10 | 21.00 | 156.3 | 4.73 Hz |
| 11 | 21.50 | 171.7 | 4.97 Hz |
| 12 | 22.00 | 188.2 | 5.20 Hz |
| 13 | 22.50 | 206.0 | 5.44 Hz |
| 18 | 25.00 | 313.9 | 6.72 Hz |

**⚠️ UNVERIFIED:** The OD range (16–25 mm) and C constant for Ferrari may differ from BMW. Need physical measurement or calibration from deflection data. The OD reference at index ≈ 10 is 21.0 mm (calibrated: `front_torsion_od_ref_mm=20.9` in cars.py).

### Rear Torsion Bar (corner spring — NOT a coil)
- Garage parameter: `rear_spring_nmm` — range **0–18** (index, not N/mm)
- Estimated linear mapping (80–350 N/mm effective wheel rate):

| Index | Est. Wheel Rate | Natural Freq (176 kg) |
|---|---|---|
| 0 | ~80 N/mm | 3.40 Hz |
| 2 | ~110 N/mm | 3.98 Hz |
| **8** | **~200 N/mm** | **5.37 Hz** ← observed baseline |
| 12 | ~260 N/mm | 6.11 Hz |
| 18 | ~350 N/mm | 7.09 Hz |

**⚠️ CRITICAL GAP:** The rear torsion bar mapping is a complete ESTIMATE (80–350 N/mm linear). No deflection calibration has been done. Need IBT sessions at different rear indices to build a calibration curve.

### Front Heave Spring
- Garage parameter: `front_heave_nmm` — range **0–8** (index, not N/mm)
- Observed: index 1 → estimated 50 N/mm (from cars.py comment)
- ⚠️ Only ONE spring index value in all 6 sessions — cannot calibrate

### Rear Heave Spring  
- Garage parameter: `rear_third_nmm` — range **0–9** (index, not N/mm)
- Observed: index 2 → estimated 530 N/mm (from cars.py comment)
- ⚠️ Only ONE value in all 6 sessions — cannot calibrate

---

## 3. Damper Architecture

Ferrari's damper system is **fundamentally different** from BMW's 0–11 click range:

| Property | Ferrari 499P | BMW M Hybrid V8 |
|---|---|---|
| Click range | 0–40 per axis | 0–11 per axis |
| LS force/click | **7.0 N** (ESTIMATE) | ~100–150 N |
| HS force/click | **30.0 N** (ESTIMATE) | ~200+ N |
| Total LS force range | 0–280 N | 0–1100 N+ |
| Precision | Very fine (40 steps) | Coarser (11 steps) |

**Baseline (from verified S1 IBT):**
```
Front LS:  13 comp / 25 rbd  (rbd >> comp — unusual, soft entry/firm rebound)
Front HS:  15 comp / 6 rbd   (comp >> rbd — high-speed mechanical grip)
Rear LS:   18 comp / 8 rbd   
Rear HS:   40 comp / 40 rbd  (both maxed — rear platform stability priority)
```

**Key behavioral insight (from IBT delta observation):**
- Reducing LS comp by 2, increasing LS rbd by 7 = 0.600s faster
- Effect: understeer_high +0.06°, body_slip +0.67° — trades grip for stability
- Suggests baseline is over-damped on front LS compression

**⚠️ Force-per-click is ESTIMATED.** BMW calibration used physical damper test data. Ferrari needs:
1. Variable LS comp test: hold rbd constant, vary comp ±5 clicks, measure RH std change
2. Infer c from σ_RH change: Δσ = Δc / (2 * m_eff * ω)

---

## 4. Aerodynamics

| Property | Value | Source |
|---|---|---|
| Wing angles (iRacing) | 12°, 13°, 14°, 15°, 16°, 17° | cars.py |
| Default DF balance | 49.5% front | CALIBRATED — hybrid shifts need rearward |
| Vortex burst threshold | 2.0 mm | ESTIMATE (same as BMW) |
| Aero compression @ 230 kph | F=15.1mm, R=8.3mm | CALIBRATED from IBT |
| tyre_load_sensitivity | 0.25 | ESTIMATE (higher than BMW 0.20) |

**Aero maps:** 6 wing angles × full DF/Drag/Balance curves loaded (`data/aero-maps/ferrari_wing_*.json`)

**Ferrari aero quirk (from LMU/WEC data):**
- High-speed stability excellent (LMH chassis more aero-stable than LMDh)
- Rear diffuser is more aggressive — floor generates more rear DF share
- Ground effect kicks in harder at low RH — vortex threshold may be tighter than BMW

**Hybrid aero interaction (unmodeled):**
- Above 190 kph, front axle hybrid engages — changes front tyre loading, affects aero balance
- At peak speed (297 kph from IBT), hybrid adds ~200 kW to front wheels
- This dynamically shifts weight/grip balance to front — LLTD model needs a hybrid correction term

---

## 5. IBT Telemetry Data (6 Sebring Sessions)

**All sessions ran same setup (heave=1, third=2, torsion=3, rear=8):**

| Metric | Observed | BMW Comparison |
|---|---|---|
| Best lap | 108.665s | 108.829s |
| Dynamic front RH | 21.9 mm | 23.3 mm |
| Dynamic rear RH | 30.7 mm | 44.9 mm |
| Rake | **8.8 mm** | **19.1 mm** — Ferrari runs WAY lower rake |
| Front RH σ | 4.63 mm | target ~3-4 mm |
| LLTD measured | 50.99% | ~51.0% |
| Peak lat G | 3.77 g | ~4.1 g (stiffer BMW platform) |
| Understeer (mean) | 0.070° | — |
| Understeer (high-speed) | 0.205° | — |
| Body slip p95 | 3.69° | — |
| Brake bias | 54.0% | 46.0% — Ferrari is 8% more front-biased |
| Front heave defl p99 | **62.54 mm** | ~5-15 mm — Ferrari heave uses 60% of travel! |
| Front bottoming events | 1 | 0 |
| Roll gradient | **0.036 deg/g** | **0.806 deg/g** — ⚠️ SUSPECT: likely sensor/parsing issue |

**⚠️ Roll gradient anomaly:** 0.036 deg/g is physically impossible for a car with this spring rate. BMW measures 0.806 deg/g with similar springs. Likely the roll sensor is on a different axis in the Ferrari IBT. This affects m_eff calibration — **do not trust current roll_gradient for Ferrari.**

**Key insight from rake data:** Ferrari runs only 8.8 mm rake vs BMW's 19.1 mm. This means:
- Ferrari aero is much less rake-sensitive than BMW
- The DF balance target (49.5% vs BMW's 50.14%) reflects less rear DF from low rake
- Front RH target should be LOWER (~25 mm vs BMW's 30 mm) for more front downforce

---

## 6. Suspension Architecture Deep Dive

### Front: Torsion Bar + Heave Spring
- Double wishbone, pushrod actuation (same as BMW physically)
- Torsion bar = corner spring (roll + heave both contribute)
- Separate heave spring = purely heave mode
- **Front heave defl p99 = 62.54 mm at index 1** → suggests VERY soft heave spring (index 1)
  - At 50 N/mm heave, static compression = F/k = (1030 × 0.476/2 × 9.81)/50000 = 0.048 m = 48 mm ✓
  - Dynamic excursion on top of that = 14 mm → 62 mm total plausible ✓
  - This validates heave index 1 ≈ 40–60 N/mm range

### Rear: Torsion Bar + Rear Heave Spring  
- Ferrari rear is torsion bars (NOT coil springs like BMW)
- This means the rear spring index (0–18) maps to a torsion bar OD, not N/mm directly
- Rear torsion bar C constant may differ from front (different bar lengths/geometry)
- motion_ratio = 1.0 currently (needs verification from physical Ferrari data)

### AWD Hybrid Coupling
- Below 190 kph: pure RWD (ICE only)
- Above 190 kph: AWD (front 200 kW electric + rear ICE)
- From IBT: `hybrid_rear_drive_corner_pct: 90.0` — rear drive active at 90% of corners
- **Traction model needs updating:** Under braking+corner entry above 190 kph, the front hybrid regen ADDS front braking force → affects effective brake bias

---

## 7. ARB System

| Property | Ferrari | BMW |
|---|---|---|
| Size labels | Disconnected, A, B, C, D, E | Disconnected, Soft, Medium, Stiff |
| Front sizes | 6 | 4 |
| Rear sizes | 6 | 4 |
| Front stiffness | [0, 3000, 6000, 9000, 12000, 15000] N·m/deg | [0, 5500, 11000, 16500] N·m/deg |
| Rear stiffness | [0, 1500, 3000, 4500, 6000, 9000] N·m/deg | [0, 2500, 5000, 7500] N·m/deg |
| Front blade range | 1–5 | 1–5 |
| Rear blade range | 1–5 | 1–5 |
| Observed baseline | FARB A/blade 1, RARB B/blade 4 | FARB Soft/blade 1, RARB Soft/blade 4 |

**⚠️ ARB stiffness values are ESTIMATES.** No calibration data available. The size labels and blade multipliers need verification from LLTD measurement across multiple ARB sizes.

**Ferrari rear ARB range (E = 9000 N·m/deg) is 2× BMW's Stiff = 4500.** This suggests Ferrari needs much higher roll stiffness at rear to control the AWD dynamic behavior. Alternatively, the 9000 estimate is too high.

---

## 8. What Needs to Be Done (Priority Order)

### 🔴 CRITICAL (blocks accurate physics)

**1. Spring Index Calibration**
- Need 9+ IBT sessions varying front torsion bar index (0, 3, 6, 9, 12, 15, 18)
- From each: measure static deflection → compute actual wheel rate
- Same for rear (vary index 0, 4, 8, 12, 16, 18)
- Same for heave (vary index 0, 2, 4, 6, 8) and third (0, 3, 6, 9)
- Script: `python3 -m ioptimal --car ferrari --ibt "session.ibt" --wing 17`

**2. Torsion Bar C Constant Verification**
- Current C = 0.0008036 (BMW value). Ferrari bar geometry may give different C
- Compute: k_wheel = C × OD^4 → if deflection data gives different k at index 3, correct C
- Or use: k = force / deflection from IBT static corner weight data

**3. Rear Torsion Bar Architecture**
- Confirm it's actually a torsion bar (not a torsion coil hybrid)
- The iRacing garage tooltip may help confirm
- Check: does `rear_spring_range_nmm` map linearly like front? Or different C constant?

### 🟡 IMPORTANT (affects solver accuracy)

**4. Roll Gradient Fix**
- Current telemetry parser gives 0.036 deg/g — physically wrong
- Fix: check body_roll channel name in Ferrari IBT header
- BMW uses `Roll`, Ferrari may use `Yaw` or different axis
- If unfixable from parsing, use calculated roll from suspension: roll = LLTD × F_lateral × t / k_roll_total

**5. m_eff Calibration**
- Current: 176 kg front, 2870 kg rear (estimates)
- BMW was calibrated from: σ_RH = F_bump / (c_eff × ω), then c_eff = meas. → m_eff
- Ferrari needs same process once force-per-click is calibrated

**6. DF Balance Target**
- Current: 49.5% (estimate)
- From IBT (RARB B/blade 4): measured LLTD = 50.99%, understeer_high = 0.205° (slight US)
- Target DF balance should be verified from a setup where understeer = ~0.0°

**7. Hybrid LLTD Correction**
- Above 190 kph, front hybrid engages — shifts effective front grip up
- LLTD target should be LOWER than BMW (less rear roll stiffness needed) because AWD provides more front traction inherently
- Estimated correction: -1.5 to -2.0% LLTD target shift vs LMDh equivalent

### 🟢 ENHANCEMENTS (once basic model works)

**8. Gear Stack Option**
- Ferrari has Short/Long gear stack (unmodeled)
- Affects top speed vs acceleration — relevant for tracks with long straights
- Need: gearing ratio tables for each stack option

**9. Front Differential**
- Ferrari has BOTH front and rear differentials (from IBT: `front_diff_preload_nm: 0.0`)
- BMW has only rear diff
- Front diff preload affects corner entry behavior with hybrid engagement
- Currently not in the solver at all — needs adding to supporting parameters

**10. Hybrid Deployment Model**
- IBT shows: `hybrid_rear_drive_corner_pct: 90.0` — active in 90% of corners
- `hybrid_rear_drive_enabled: True`
- The hybrid provides up to 268 hp (200 kW) front axle above 190 kph
- This dramatically changes traction at corner exit at high speed
- Needs a simple model: at v > 190 kph, front traction available += 200 kW / v_ms

---

## 9. Verified Constants (CALIBRATED from IBT)

```python
# From 6 Sebring sessions (all at same spring setup):
weight_dist_front = 0.476     # from corner weights: 2725F / (2725F + 2997R) = 47.6%
brake_bias_pct = 54.0         # BrakePressureBias telemetry channel
front_pushrod_default_mm = -3.0  # IBT: both sessions pushrod=-3.0, RH=30.1mm
rear_base_rh_mm = 42.5        # from rear pushrod calibration
rear_pushrod_to_rh = 0.45     # slope from 2 IBTs
front_compression_mm = 15.1   # @ 230 kph (30.1 static - 15.0 dynamic average)
rear_compression_mm = 8.3     # @ 230 kph (48.8 static - 40.5 dynamic average)
wheelbase_m = 2.74            # Wikipedia confirmed
```

---

## 10. Recommended Test Session Plan

To build the calibration dataset needed, run these sessions:

**Session A: Front torsion sweep (7 sessions, 5 laps each)**
```
Vary: front_torsion_od_mm index = 0, 3, 6, 9, 12, 15, 18
Keep constant: rear idx=8, heave=1, third=2, ARB A/1 + B/4
Measure: front_rh_std_mm, roll_gradient, front_heave_defl_p99
```

**Session B: Rear torsion sweep (5 sessions)**
```
Vary: rear_spring_nmm index = 0, 4, 8, 12, 18
Keep constant: front idx=3, heave=1, third=2
Measure: rear_rh_std_mm, roll_gradient, LLTD
```

**Session C: Heave spring sweep (5 sessions)**
```
Vary: front_heave_nmm index = 0, 2, 4, 6, 8
Keep constant: everything else at baseline
Measure: front_rh_std_mm, front_bottoming_events, front_heave_defl_p99
```

**Session D: Third spring sweep (5 sessions)**
```
Vary: rear_third_nmm index = 0, 3, 6, 9
Measure: rear_rh_std_mm, rear_bottoming_events
```

**Session E: ARB validation (5 sessions)**
```
Vary: RARB size = Disc/A/B/C/D, blade=3 constant
Keep: torsion=3, rear=8, heave=1, third=2
Measure: lltd_measured, roll_gradient
→ Validates ARB stiffness table A/B/C/D/E mapping
```

Total: ~27 sessions, ~3 hours of driving. Would give complete calibration of all spring parameters.

---

## 11. Comparison: Ferrari vs BMW LLTD at Baseline

Using current estimates:
- Front wheel rate @ idx 3: **75.4 N/mm**
- Rear wheel rate @ idx 8: **~200 N/mm (estimate)**
- Front ARB A/blade 1: ~600 N·m/deg (20% of 3000)
- Rear ARB B/blade 4: ~2400 N·m/deg (80% of 3000)
- Track width: 1730/1650 mm

```
k_roll_springs_front = 2 × 75,400 × (0.865)² × π/180 = 2,271 N·m/deg
k_roll_springs_rear  = 2 × 200,000 × (0.825)² × π/180 = 9,462 N·m/deg

k_roll_front_total = 2,271 + 600  = 2,871 N·m/deg
k_roll_rear_total  = 9,462 + 2,400 = 11,862 N·m/deg

LLTD = k_roll_front_total / (k_roll_front_total + k_roll_rear_total)
     = 2,871 / (2,871 + 11,862) = 19.5%
```

**🚨 19.5% LLTD is catastrophically wrong (target ~50%).** This confirms the rear spring/ARB estimates are badly off. The rear is either much softer OR the motion ratio for the rear is much less than 1.0. This is the biggest calibration problem in the current Ferrari model.

Possible explanations:
1. Rear torsion bar C constant is different (bar is longer → lower C → lower k at same OD)
2. Rear motion_ratio is actually 0.4–0.5 (not 1.0)
3. Rear spring index → N/mm mapping is wrong (non-linear, or different range)
4. ARB stiffness estimates are off by 2–3×

**RECOMMENDATION:** Instrument the LLTD calculation for Ferrari with the measured 50.99% LLTD from IBT at baseline setup, and back-solve for the correct k_roll_rear. That gives: 

k_roll_rear_total = k_roll_front_total × (LLTD_rear / LLTD_front) = 2,871 × (0.4901/0.5099) = 2,759 N·m/deg

This means the rear total roll stiffness ≈ EQUAL to front at baseline. Either the rear torsion bars are much softer than estimated, or the ARB is much softer.

---

*This document should be updated as new IBT calibration sessions are run.*
*Key blocker: spring index → N/mm mapping. Everything else flows from that.*
