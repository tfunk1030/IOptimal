# GTP Garage Model Research
*Generated: 2026-04-01 | Sources: Official iRacing User Manuals (BMW, Cadillac, Porsche PDFs), Acura ARX-06 ManualsLib, Ferrari 499P observation data (no official PDF found), IBT observation analysis*

---

## Summary of Findings

Official iRacing user manuals obtained for BMW M Hybrid V8, Cadillac V-Series.R, Porsche 963, Acura ARX-06 (via ManualsLib). No official PDF exists for the Ferrari 499P (added after initial GTP launch, LMH vs LMDh).

**Critical structural differences confirmed:**

| Feature | BMW | Cadillac | Porsche | Acura | Ferrari |
|---------|-----|----------|---------|-------|---------|
| Front spring system | Torsion Bar O.D. | Torsion Bar O.D. | Roll Spring + Torsion Bar | Torsion Bar O.D. | Indexed torsion bar |
| Rear spring system | Coil spring (N/mm) | Coil spring (N/mm) | Coil spring (N/mm) | **Rear Heave Spring** | Indexed heave + indexed torsion |
| Heave element | Heave Slider (passive) | Heave Slider (passive) | Heave Slider (passive) | **Heave Damper (active)** | Heave Slider (passive) |
| Front ARB format | Size (Soft only) + Blades | Size + Blades | **Connected/Disconnected** + Blades | Size (Disconnected+) + Blades | Size (A-E/Disc) + Blades |
| Roll resistance front | ARB + Torsion bars | ARB + Torsion bars | ARB + **Roll Spring (unique)** | ARB + Torsion bars | ARB + Indexed torsion |
| Front Roll Damper | ❌ | ❌ | ✅ (unique) | ❌ | ❌ |
| Rear torsion bar | ❌ | ❌ | Via pushrod | ❌ | ✅ indexed 0-18 |
| Diff preload | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## Per-Car Parameter Tables

### BMW M Hybrid V8
*Source: Official iRacing PDF (s100.iracing.com), IBT observations (102 sessions at Sebring)*

**Structure: Pushrod-actuated independent torsion bar front AND rear with heave + third spring**

| Parameter | iRacing Label | Type | Range | Unit | Verified | Notes |
|-----------|--------------|------|-------|------|----------|-------|
| Front Heave Spring | HEAVE SPRING | continuous | ~20-900 | N/mm | ✅ IBT | Values 10-900 seen in obs |
| Heave Perch Offset | HEAVE PERCH OFFSET | continuous | — | mm | ✅ manual | Adjusts RH + preload |
| Heave Spring Defl | HEAVE SPRING DEFL | readout only | 0.6-25.0 legal | mm | ✅ manual | Not adjustable directly |
| Heave Slider Defl | HEAVE SLIDER DEFL | readout only | 25-45 legal | mm | ✅ manual | Passive — no damping |
| Front ARB Size | ARB SIZE | label (discrete) | Soft only | — | ✅ obs | Only 1 option for front |
| Front ARB Blades | ARB BLADES | indexed 1-5 | 1-5 | — | ✅ obs | In-car via F8 FARB |
| Front Torsion Bar OD | TORSION BAR O.D. | od_mm (discrete) | 13.90/14.34/15.14 | mm | ✅ IBT | 3 options confirmed |
| Front Torsion Bar Turns | TORSION BAR TURNS | continuous | — | turns | ✅ manual | Per-corner for crossweight |
| Front Toe | TOE-IN | continuous | — | mm | ✅ IBT | Negative = toe-out |
| Front Pushrod | PUSHROD LENGTH OFFSET | continuous | — | mm | ✅ IBT | RH adj without heave preload |
| Rear Spring Rate | SPRING RATE | continuous | ~100-400 | N/mm | ✅ IBT | Symmetric pairs |
| Rear Spring Perch Offset | SPRING PERCH OFFSET | continuous | — | mm | ✅ manual | Per-corner for crossweight |
| Rear Third Spring | THIRD SPRING | continuous | ~100-900 | N/mm | ✅ IBT | Controls aero platform |
| Rear ARB Size | ARB SIZE | label (discrete) | Soft/Medium/Stiff | — | ✅ obs | 3 options confirmed |
| Rear ARB Blades | ARB BLADES | indexed 1-5 | 1-5 | — | ✅ obs | In-car via F8 RARB |
| Rear Toe | TOE-IN | continuous | — | mm | ✅ IBT | |
| Rear Pushrod | PUSHROD LENGTH OFFSET | continuous | — | mm | ✅ IBT | |
| Front Camber | CAMBER (front corner) | continuous | ~-3.5 to 0 | deg | ✅ IBT | |
| Rear Camber | CAMBER (rear corner) | continuous | ~-3.0 to 0 | deg | ✅ IBT | |
| Brake Bias | BRAKE PRESSURE BIAS | continuous | ~44-50 | % | ✅ IBT | |
| Diff Preload | PRELOAD | continuous | 0-100 | Nm | ✅ IBT | |
| Diff Ramp | COAST/DRIVE RAMP ANGLES | label | 40/65, 45/70, 50/75 | — | ⚠️ partial | Ramp + clutch plates |
| TC Gain | TRACTION CONTROL GAIN | indexed | 0-11 | — | ✅ IBT | |
| TC Slip | TRACTION CONTROL SLIP | indexed | 0-11 | — | ✅ IBT | |
| Wing | REAR WING ANGLE | discrete | 12-17 | deg | ✅ IBT | 1-deg steps |
| Hybrid Mode | MGU-K DEPLOY MODE | label | No Deploy/Qual/Attack/Balanced/Build | — | ✅ manual | |
| Gear Stack | GEAR STACK | label | Short/Long | — | ✅ manual | |
| LS Comp (front) | LS COMP DAMPING | clicks | 1-11 | — | ✅ IBT | Front corners |
| HS Comp (front) | HS COMP DAMPING | clicks | 1-11 | — | ✅ IBT | |
| LS Rbd (front) | LS REBOUND DAMPING | clicks | 1-11 | — | ✅ IBT | |
| HS Rbd (front) | HS REBOUND DAMPING | clicks | 1-11 | — | ✅ IBT | |
| HS Slope (rear) | HS COMP DAMPING SLOPE | clicks | 1-11 | — | ✅ IBT | Rear corners ONLY |
| LS Comp (rear) | LS COMP DAMPING | clicks | 1-11 | — | ✅ IBT | |
| HS Comp (rear) | HS COMP DAMPING | clicks | 1-11 | — | ✅ IBT | |
| LS Rbd (rear) | LS REBOUND DAMPING | clicks | 1-11 | — | ✅ IBT | |
| HS Rbd (rear) | HS REBOUND DAMPING | clicks | 1-11 | — | ✅ IBT | |

---

### Cadillac V-Series.R
*Source: Official iRacing PDF, IBT observations (4 sessions at Silverstone)*

**Same Dallara chassis as BMW. Key differences: Power Steering Assist, Brake Bias Target/Migration, Throttle Shape.**

| Parameter | iRacing Label | Type | Range | Unit | Verified | Notes |
|-----------|--------------|------|-------|------|----------|-------|
| Front Heave Spring | HEAVE SPRING | continuous | ~20-200 | N/mm | ✅ IBT | Narrower range than BMW |
| Rear Third Spring | THIRD SPRING | continuous | ~100-1000 | N/mm | ✅ IBT | |
| Front Torsion Bar OD | TORSION BAR O.D. | od_mm (discrete) | 13.90/14.34/14.76 | mm | ✅ IBT | Different top OD than BMW |
| Front ARB Size | ARB SIZE | label | Soft (only) | — | ✅ obs | Only 1 option |
| Rear ARB Size | ARB SIZE | label | Soft/Medium | — | ✅ obs | Only 2 options (not 3) |
| Power Steering Assist | POWER STEERING ASSIST | continuous | — | level | ✅ manual | Unique to Cadillac |
| Brake Bias Target | BRAKE BIAS TARGET | continuous | ±clicks | % offset | ✅ manual | 0.5%/click |
| Brake Bias Migration | BRAKE BIAS MIGRATION | continuous | — | %/click | ✅ manual | |
| Throttle Shape | THROTTLE SHAPE | indexed | 1-N | — | ✅ manual | Unique to Cadillac |
| Diff Preload | PRELOAD | continuous | 0-100 | Nm | ✅ IBT | |
| TC Gain | TRACTION CONTROL GAIN | indexed | 0-11 | — | ✅ IBT | |
| Wing | REAR WING ANGLE | discrete | 12-17 | deg | ✅ IBT | |

*All BMW parameters also present (same chassis, same damper system, same ARB blade counts).*

---

### Porsche 963
*Source: Official iRacing PDF, IBT observations (2 sessions at Sebring — very sparse)*

**MULTIMATIC chassis — completely different suspension architecture from BMW/Cadillac/Acura/Ferrari.**

**UNIQUE PARAMETERS (not present on other cars):**
- **Roll Spring**: Front-only spring that resists roll but not heave. Analogous to a very stiff ARB but implemented as a spring. Adjustable N/mm.
- **Roll Perch Offset**: Preloads the Roll Spring. Must keep Roll Spring Deflection near-zero for tech compliance.
- **Roll Damper** (front): Separate damper for roll motion only. Has LS + HS + HS Slope. Compression and rebound are linked (same value).
- **Front ARB Setting**: Connected/Disconnected toggle (not size label). Must be Connected to use ARB Adjustment.

| Parameter | iRacing Label | Type | Range | Unit | Verified | Notes |
|-----------|--------------|------|-------|------|----------|-------|
| Front Heave Spring | HEAVE SPRING | continuous | ~20-200 | N/mm | ✅ IBT | |
| Front Roll Spring | ROLL SPRING | continuous | — | N/mm | ✅ manual | **UNIQUE — replaces torsion bar function** |
| Front Roll Perch Offset | ROLL PERCH OFFSET | continuous | — | mm | ✅ manual | Keep Roll Spring Defl ≈ 0 |
| Front ARB Setting | ARB SETTING | label | Connected/Disconnected | — | ✅ manual | **NOT a size — boolean** |
| Front ARB Adjustment | ARB ADJUSTMENT | indexed 1-5 | 1-5 | — | ✅ manual | In-car via F8 FARB. Active only when Connected |
| Front Roll Damper LS | LS DAMPING (Roll) | continuous | — | — | ✅ manual | **UNIQUE — comp + rbd linked** |
| Front Roll Damper HS | HS DAMPING (Roll) | continuous | — | — | ✅ manual | |
| Front Roll Damper HS Slope | HS DAMP SLOPE | continuous | — | — | ✅ manual | |
| Front Torsion Bar Turns | TORSION BAR TURNS | continuous | — | turns | ✅ manual | Per-corner for crossweight |
| Front Torsion Bar OD | TORSION BAR O.D. | od_mm (discrete) | — | mm | ⚠️ unclear | Not listed separately in front chassis section; may be fixed |
| Rear Spring Rate | SPRING RATE | continuous | ~100-400 | N/mm | ✅ IBT | |
| Rear Third Spring | THIRD SPRING | continuous | ~100-300 | N/mm | ✅ IBT | Narrower range than BMW |
| Rear ARB Size | ARB SIZE | label | Soft/Disconnected | — | ✅ obs | Only 'Soft' seen in obs |
| Rear ARB Adjustment | ARB ADJUSTMENT | indexed 1-5 | 1-5 | — | ✅ manual | In-car via F8 RARB |
| Rear Pushrod | PUSHROD LENGTH DELTA | continuous | — | mm | ✅ manual | Note: "Delta" not "Offset" |
| HS Comp Slope | HS COMP DAMP SLOPE | clicks | — | — | ✅ manual | **Rear corners ONLY** (not front) |
| Wing | REAR WING ANGLE | discrete | 12-17 | deg | ✅ IBT | |

**Physics note:** Porsche DSSV dampers are spool-valve type. Click behavior is non-linear vs BMW shim stack. ALL zeta targets from BMW IBT are invalid for Porsche. Needs dedicated calibration sessions.

**Data gaps:** Only 2 Porsche sessions total. Front Torsion Bar OD range unknown. Roll Spring N/mm range unknown. DSSV click force curve unknown.

---

### Acura ARX-06
*Source: Official iRacing manual via ManualsLib, IBT observations (5 sessions at Hockenheim)*

**UNIQUE PARAMETERS vs BMW:**
- **Rear Heave Spring**: Acura uses a "Rear Heave Spring" — NOT a "Third Spring" like BMW/Cadillac/Porsche.
- **Heave Damper Deflection**: Acura front heave has an ACTIVE DAMPER (not passive slider). This means the front heave element provides actual damping forces in vertical motion.
- **Front ARB includes Disconnected option**: BMW front only has "Soft". Acura has Disconnected + Soft + possibly Medium.

| Parameter | iRacing Label | Type | Range | Unit | Verified | Notes |
|-----------|--------------|------|-------|------|----------|-------|
| Front Heave Spring | HEAVE SPRING | continuous | ~90-400 | N/mm | ✅ IBT | Much stiffer range than BMW |
| Heave Damper Defl | HEAVE DAMPER DEFL | readout only | — | mm | ✅ manual | **UNIQUE — active damper, not passive slider** |
| Front Torsion Bar OD | TORSION BAR O.D. | od_mm (discrete) | 13.90/15.51/15.86 | mm | ✅ IBT | Different options from BMW |
| Front Torsion Bar Turns | TORSION BAR TURNS | continuous | — | turns | ✅ manual | Per-corner for crossweight |
| Front ARB Size | ARB SIZE | label | Disconnected/Soft/(Medium?) | — | ✅ partial | Obs show '', 'Soft', 'Medium' |
| Front ARB Blades | ARB BLADES | indexed 1-5 | 1-5 | — | ✅ manual | In-car via F8 FARB |
| **Rear Heave Spring** | **HEAVE SPRING (rear)** | continuous | ~60-300 | N/mm | ✅ IBT | **NOT Third Spring — unique** |
| Rear Heave Perch Offset | HEAVE PERCH OFFSET (rear) | continuous | — | mm | ✅ manual | |
| Rear Spring Rate | SPRING RATE | continuous | — | N/mm | ✅ IBT | All obs show 0.0 — may not exist |
| Rear ARB Size | ARB SIZE (rear) | label | Soft/Medium | — | ✅ obs | |
| Rear ARB Blades | ARB BLADES (rear) | indexed 1-5 | 1-5 | — | ✅ manual | |
| Wing | REAR WING ANGLE | discrete | 6.0-10.0 | deg | ✅ IBT | **0.5-deg steps** — completely different range |
| Hybrid Mode | MGU-K DEPLOY MODE | label | No Deploy/Qual/Attack/Balanced/Build | — | ✅ manual | Same 5 modes as BMW |
| Diff Preload | PRELOAD | continuous | 0-100 | Nm | ✅ IBT | |
| TC Gain | TRACTION CONTROL GAIN | indexed | 0-11 | — | ✅ IBT | |

---

### Ferrari 499P
*Source: No official iRacing PDF found (LMH car, added after initial GTP launch). Data from 20 IBT sessions (8 Hockenheim, 12 Sebring).*

**UNIQUE PARAMETERS — all spring/torsion adjustments are INDEXED (integer indices), not physical values:**

| Parameter | iRacing Label | Type | Index Range | Physical Range | Unit | Verified |
|-----------|--------------|------|-------------|----------------|------|----------|
| Front Heave Spring | Front Heave Spring Index | indexed | 0-8 | 30-190 N/mm | idx | ✅ IBT |
| Rear Third Spring | Rear Heave/Third Index | indexed | 0-9 | ~410-950 N/mm | idx | ✅ IBT |
| Front Torsion Bar | Front Torsion Bar Index | indexed | 0-18 | — | idx | ✅ IBT |
| **Rear Torsion Bar** | **Rear Torsion Bar Index** | indexed | 0-18 | — | idx | ✅ IBT (unique!) |
| Front ARB Size | ARB Size | label | Disconnected/A/B/C/D/E | — | — | ✅ IBT |
| Front ARB Blade | ARB Blade | indexed | 1-5 | — | — | ✅ IBT |
| Rear ARB Size | ARB Size | label | Disconnected/A/B/C/D/E | — | — | ✅ IBT |
| Rear ARB Blade | ARB Blade | indexed | 1-5 | — | — | ✅ IBT |
| Front Dampers | LS/HS Comp + Rbd | indexed | 0-40 | — | clicks | ✅ IBT |
| Rear Dampers | LS/HS Comp + Rbd | indexed | 0-40 | — | clicks | ✅ IBT |
| Wing | Wing Angle | discrete | 12-17 | 12-17 | deg | ✅ IBT (all 20 sessions: 17) |
| Diff Preload | Diff Preload | continuous | 0-50 | 0-50 | Nm | ✅ IBT |
| Diff Ramp | Diff Setting | label | Less Locking/More Locking | — | — | ✅ IBT |
| TC Gain | TC Gain | indexed | 0-11 | — | — | ✅ IBT |
| Brake Bias | Brake Pressure Bias | continuous | ~49-57 | — | % | ✅ IBT |
| Hybrid | Rear-Drive Enable | label | — | — | — | ⚠️ not in obs schema |

**Ferrari LLTD:** Empirically constant at 0.510 ± 0.002 across ALL 20 sessions despite large setup variation. Physics decomposition invalid (torsion bar rates produce 0.42, not 0.51). Fixed as car constant.

---

## Physics Constants Status

| Car | Constant | Value | Source | Status |
|-----|----------|-------|--------|--------|
| BMW | front m_eff | 228 kg | IBT 73+ sessions | ✅ CALIBRATED |
| BMW | rear m_eff | 2395 kg | IBT (multi-session) | ✅ CALIBRATED |
| BMW | rear_motion_ratio | 0.536 | IBT back-solve | ✅ CALIBRATED |
| BMW | torsion_c | 0.001282 | IBT 19-entry lookup | ✅ CALIBRATED |
| BMW | zeta_ls_front | 0.88 | IBT (73 sessions) | ✅ CALIBRATED |
| BMW | LLTD_target | 0.41 | IBT mean | ✅ CALIBRATED |
| Ferrari | front m_eff | 1439 kg | IBT 7 sessions | ✅ CALIBRATED |
| Ferrari | rear m_eff | 1500 kg | IBT (high variance) | ⚠️ ESTIMATE (variance too high) |
| Ferrari | rear_motion_ratio | 0.612 | LLTD back-solve | ✅ CALIBRATED (1 point) |
| Ferrari | LLTD_target | 0.510 | IBT 20 sessions | ✅ CALIBRATED |
| Ferrari | ARB stiffness | [0,3000,...] | Manual estimate | ❌ ESTIMATE — unvalidated |
| Ferrari | Torsion bar idx→N/mm | 30+20*idx | Physics estimate | ⚠️ ESTIMATE (heave_index_unvalidated=True) |
| Ferrari | Rear torsion idx→N/mm | 410+60*idx | Physics estimate | ❌ ESTIMATE |
| Cadillac | front m_eff | 228 kg | BMW copy | ❌ ESTIMATE — needs IBT |
| Cadillac | rear m_eff | 2395 kg | BMW copy | ❌ ESTIMATE — needs IBT |
| Cadillac | torsion_c | unknown | BMW copy | ❌ ESTIMATE |
| Cadillac | LLTD_target | derived | theoretical | ❌ ESTIMATE — needs IBT |
| Cadillac | zeta targets | BMW copy | | ❌ ESTIMATE |
| Porsche | ALL physics | BMW copy | | ❌ ESTIMATE — DSSV dampers completely different |
| Porsche | Roll Spring stiffness | unknown | no data | ❌ UNKNOWN |
| Acura | front m_eff | 228 kg | BMW copy | ❌ ESTIMATE |
| Acura | rear m_eff | 2395 kg | BMW copy | ❌ ESTIMATE |
| Acura | LLTD_target | derived | theoretical | ❌ ESTIMATE |
| Acura | zeta targets | BMW copy | | ❌ ESTIMATE |

---

## Discrepancies Found: Code vs Reality

### 1. Porsche front ARB schema — WRONG
**Code:** `front_arb_size.exists = False` (our current schema)
**Reality:** Porsche front ARB Setting = Connected/Disconnected (boolean, not size). EXISTS but is different type.
**Fix:** `front_arb_size.discrete_values = ["Disconnected", "Connected"]`, `exists = True`

### 2. Porsche Roll Spring — MISSING from schema
**Code:** No Roll Spring field exists in `CarParamSchema` or `CarModel`
**Reality:** Roll Spring is the PRIMARY front roll stiffness parameter for Porsche (replaces torsion bar function)
**Fix:** Add `roll_spring` ParamDef to CarParamSchema + add to `cars.py` CornerSpringModel for Porsche

### 3. Acura rear spring field — WRONG label
**Code:** `rear_heave.canonical = "rear_third_nmm"`, display = "Rear Third Spring"
**Reality:** Acura calls it "Rear Heave Spring" NOT "Third Spring". Same mechanical function, different iRacing label.
**Fix:** `rear_heave.display_label = "Rear Heave Spring"` for Acura

### 4. Acura front heave — HEAVE DAMPER not SLIDER
**Code:** Objective function checks `heave_slider_defl` for Acura
**Reality:** Acura has HEAVE DAMPER (active), not HEAVE SLIDER (passive). Different mechanics.
**Impact:** Deflection limits may differ. Actively damped = different physics behavior.
**Fix:** Flag Acura heave as "damped" in CarModel; deflection limits need Acura-specific IBT calibration.

### 5. BMW front ARB — confirmed only "Soft"
**Code:** `front_arb_size.discrete_values = ["Soft"]` ✅ matches manual

### 6. Cadillac torsion bar OD options — DIFFERENT from BMW
**Code:** Uses same OD options as BMW (13.90, 14.34, 15.14)
**Reality:** Cadillac has 13.90, 14.34, **14.76** (not 15.14)
**Fix:** Update Cadillac torsion_c and OD options in cars.py

### 7. Ferrari indexed spring → N/mm mapping — UNVALIDATED
**Code:** `front_setting_anchor_index=1.0, front_rate_at_anchor_nmm=50.0, front_rate_per_index_nmm=20.0` → idx 5 = 130 N/mm
**Reality:** `heave_index_unvalidated = True` flag already set. Need IBT spring defl vs weight vs rate confirmation.

### 8. Porsche HS Comp Slope — front vs rear
**Code:** Assumes same damper structure (LS Comp/HS Comp/HS Slope/LS Rbd/HS Rbd) for all
**Reality:** HS Slope = REAR CORNERS ONLY for Porsche. Front heave has NO slope setting.

---

## Recommendations (Priority Order)

### Immediate (data from existing IBT sessions)
1. ✅ Ferrari LLTD fixed at 0.510 — DONE
2. ✅ Porsche ARB front schema corrected to Connected/Disconnected
3. ✅ Acura rear label corrected to "Rear Heave Spring"
4. Update Cadillac Torsion Bar OD from [13.90, 14.34, 15.14] → [13.90, 14.34, **14.76**]

### Short-term (need 1-2 IBT sessions each)
5. Run 3-5 Cadillac IBT sessions at any track → calibrate m_eff, LLTD, zeta targets
6. Run 3-5 Acura IBT sessions at any track → calibrate m_eff, LLTD, heave damper behavior
7. Run 3-5 Porsche IBT sessions at any track → Roll Spring range, ARB blade force, DSSV damper model

### Long-term (research needed)
8. Validate Ferrari indexed spring → N/mm conversion from garage screenshots or force sensor data
9. Validate Ferrari ARB stiffness model (currently linear estimate, may be non-linear)
10. Porsche DSSV damper force curve — requires dedicated click-sweep IBT sessions

---

## Next Steps

| Priority | Car | Action | Expected Impact |
|----------|-----|--------|----------------|
| 🔴 High | Porsche | Fix Roll Spring + ARB schema | Porsche recommendations meaningless without this |
| 🔴 High | All | Fix objective to read from `GarageModelBuilder` vs hardcoded BMW | Eliminates BMW data leaking to other cars |
| 🟡 Med | Cadillac | Fix Torsion Bar OD to [13.90, 14.34, 14.76] | Small but verifiable fix |
| 🟡 Med | Acura | Run 3 IBT sessions → calibrate m_eff + LLTD | Acura has 5 sessions but no calibrated physics |
| 🟡 Med | Ferrari | Garage screenshot of heave index → N/mm mapping | Validate/correct `front_rate_per_index_nmm=20` |
| 🟢 Low | BMW | Validate rear ARB stiffness model (Nm/deg per size) | BMW already works; this improves LLTD accuracy |
