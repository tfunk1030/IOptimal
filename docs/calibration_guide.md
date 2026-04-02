# iOptimal GTP Calibration Guide
*Generated 2026-04-02 | Branch: claw-research*

---

## ⚠️ The One Rule That Changes Everything

**You cannot change one parameter in isolation.**

Every spring, bar, and ride height target is coupled. Before touching anything:
1. Understand the full dependency chain below
2. Identify every downstream parameter that must move with it
3. Run a fresh IBT session with the change to re-anchor the calibration

Missing this causes the solver to recommend setups that look valid on paper but are physically impossible — e.g., a static RH target that requires the car to sit on the floor, or a LLTD that no ARB combination can achieve.

---

## Dependency Map

```
TORSION BAR OD
  ↓ front corner spring rate changes
  ↓ → LLTD range shifts (same ARB blade now produces different roll stiffness ratio)
  ↓ → heave deflection under aero load changes (front stiffness changed)
  ↓   → STATIC RH TARGET must move to achieve same DYNAMIC RH
  ↓   → VORTEX THRESHOLD (min safe front RH) may change
  ↓ → damping targets may need revisit (natural freq changed)
  ↓ → if chasing same LLTD: ARB blade must compensate

HEAVE SPRING (front or rear)
  ↓ front/rear heave stiffness changes
  ↓ → heave deflection under aero downforce changes
  ↓   → to maintain same dynamic RH: static RH must change
  ↓   → static RH target stored in solver must be updated
  ↓ → platform natural frequency changes
  ↓   → damper zeta targets change (critical damping force changes)
  ↓   → damper click recommendations invalid until re-calibrated
  ↓ → floor clearance/bottoming risk profile changes
  ↓   → vortex threshold may need recalculation

REAR THIRD SPRING
  ↓ rear platform compliance changes
  ↓ → rear dynamic RH variance changes (car rides higher or lower under load)
  ↓   → static RH target for rear must change
  ↓   → rake angle changes → DF balance shifts
  ↓   → wing angle recommendation may shift to compensate
  ↓ → braking platform behavior changes
  ↓   → diff preload interaction (exit traction affected by pitch change)

ARB BLADE or SIZE
  ↓ roll stiffness changes (front or rear)
  ↓ → LLTD ratio shifts
  ↓   → if LLTD was calibrated, it's now wrong
  ↓   → camber under roll changes → tyre contact patch changes
  ↓ → weight transfer rate changes
  ↓   → diff preload tuning may need revisit
  ↓ NOTE: changing blade does NOT affect static RH or heave deflection
         (ARB only loads in roll, not heave)

STATIC RH TARGET (garage setting)
  ↓ sets the baseline before any aero/mechanical load
  ↓ → dynamic RH = static RH - deflection under load
  ↓   deflection ≈ (aero_downforce_per_corner) / (heave_spring_rate)
  ↓ → if dynamic RH changes: DF balance changes → wing recommendation may shift
  ↓ → floor clearance: dynamic RH must stay above vortex threshold
  ↓ LEGAL MINIMUM: 30mm static (all cars, iRacing enforced)

DAMPER CLICKS
  ↓ changes transient behavior only — does NOT change static or mean dynamic RH
  ↓ → affects platform stability metric (how quickly car settles)
  ↓ → affects how IBT records dynamic RH variance (σ)
  ↓ NOTE: changing dampers without changing springs = safe isolated change
         (only parameter group that doesn't cascade into RH or LLTD)
```

---

## Per-Car Status + Parameter Reference

---

### 🔵 BMW M Hybrid V8 — Calibration Status: **8.5 / 10**

**Springs (continuous, not indexed):**
| Parameter | iRacing Label | Solver Field | Range | Legal | Status |
|-----------|--------------|-------------|-------|-------|--------|
| Front torsion bar OD | TorsionBarDiameter | `torsion_bar_od_mm` | 13.9–18.2mm (0.1mm steps) | ✅ | ✅ CALIBRATED — 14 options, validated |
| Front heave spring | HeaveSpringRate (front) | `front_heave_nmm` | 0–900 N/mm | ✅ | ✅ CALIBRATED |
| Rear third spring | ThirdSpringRate (rear) | `rear_third_nmm` | 100–900 N/mm | ✅ | ✅ CALIBRATED |
| Rear torsion bar | rear coil spring | continuous | 100–300 N/mm | ✅ | ✅ |

**Dampers (0–11 clicks per channel):**
| Channel | iRacing Label | Solver Field | Baseline | Status |
|---------|--------------|-------------|----------|--------|
| LS Compression | LsCompDamp | `*_ls_comp_clicks` | F:7 R:6 | ✅ zeta=0.68/0.47 CALIBRATED |
| HS Compression | HsCompDamp | `*_hs_comp_clicks` | F:5 R:3 | ✅ zeta=0.23/0.20 CALIBRATED |
| LS Rebound | LsRbdDamp | `*_ls_rbd_clicks` | F:6 R:7 | ✅ |
| HS Rebound | HsRbdDamp | `*_hs_rbd_clicks` | F:8 R:9 | ✅ |

**ARB:**
| Parameter | Sizes | Blades | Stiffness per size |
|-----------|-------|--------|-------------------|
| Front | Disc / Soft / Medium / Stiff | 1–5 | 0 / 5500 / 11000 / 16500 N/mm·deg |
| Rear | Soft / Medium / Stiff | 1–5 | 1500 / 3000 / 4500 N/mm·deg |

**Other:**
- Wing: 12–17 deg (1° steps) — 6 aero maps ✅
- Diff preload: calibrated
- Weight dist front: 47.3% (measured)
- DF balance target: 50.14% (validated from Sebring telemetry)
- m_eff front: 228 kg ✅ calibrated; rear: 2395 kg ✅

**What's not perfect:**
- k-NN limited to Sebring (102 sessions). At a new track, solver has no empirical data — first 3 sessions will be physics-only.
- Torsion arb coupling = 0.25 (estimated, not validated from click sweep)

---

### 🔴 Ferrari 499P — Calibration Status: **6.5 / 10**

**Springs (INDEXED — not continuous):**

| Parameter | iRacing Label | Index | Physical Value | Status |
|-----------|--------------|-------|----------------|--------|
| Front Heave | HeaveSpringRate | 0 | 30 N/mm | ⚠️ extrapolated |
| | | 1 | 50 N/mm | ✅ validated (IBT Mar19/20) |
| | | 2 | 70 N/mm | ⚠️ linear estimate |
| | | 3 | 90 N/mm | ⚠️ linear estimate |
| | | 4 | 110 N/mm | ⚠️ linear estimate |
| | | 5 | 130 N/mm | ⚠️ linear estimate |
| | | 6 | 150 N/mm | ⚠️ linear estimate |
| | | 7 | 170 N/mm | ⚠️ linear estimate |
| | | 8 | 190 N/mm | ⚠️ linear estimate |
| Rear Third | ThirdSpringRate | 0 | 410 N/mm | ⚠️ extrapolated |
| | | 1 | 470 N/mm | ⚠️ estimate |
| | | 2 | 530 N/mm | ✅ validated (IBT Mar19/20) |
| | | 3 | 590 N/mm | ⚠️ estimate |
| | | 4 | 650 N/mm | ⚠️ estimate |

> ⚠️ **Index→N/mm is assumed linear at 20 N/mm/step (heave) and 60 N/mm/step (third).**
> Only index 1 (heave) and index 2 (third) are validated from actual IBT data.
> **Changing heave or third index requires validating the N/mm at that index** — take a garage screenshot of ShockDeflStatic and TorsionBarDefl to verify.

**Torsion Bar (front — validated from garage screenshots):**
| Index | Stiffness | Source |
|-------|-----------|--------|
| 0 | 204.7 N/mm | estimated |
| 2 | 220.6 N/mm | ✅ garage: defl=12.1mm, cw=2669N |
| 5 | 266.9 N/mm | ✅ garage: defl=10.0mm |
| 9 | 317.7 N/mm | ✅ garage: defl=8.4mm |
| 11 | 317.7 N/mm | ✅ garage: defl=8.4mm |
| 15 | 360.7 N/mm | ✅ garage: defl=7.4mm |
| 18 | 444.8 N/mm | ✅ garage: defl=6.0mm |

> Rear torsion also validated at 4 index points. Between validated points: interpolated.

**Dampers (0–40 clicks per channel) — ⚠️ UNCALIBRATED:**
| Channel | iRacing Label | Solver Field | Current zeta | Status |
|---------|--------------|-------------|-------------|--------|
| LS Comp | LsCompDamp | `*_ls_comp_clicks` | 0.55 (BMW copy) | ❌ ESTIMATE |
| HS Comp | HsCompDamp | `*_hs_comp_clicks` | 0.20 (BMW copy) | ❌ ESTIMATE |
| LS Rebound | LsRbdDamp | `*_ls_rbd_clicks` | 0.40 (BMW copy) | ❌ ESTIMATE |
| HS Rebound | HsRbdDamp | `*_hs_rbd_clicks` | 0.18 (BMW copy) | ❌ ESTIMATE |

> BMW uses 0–11 clicks; Ferrari uses 0–40 clicks. Force per click is also different.
> **All damper click recommendations are directional at best until a click sweep is run.**
> See calibration procedure below.

**ARB:**
| Parameter | Sizes | Blades | Notes |
|-----------|-------|--------|-------|
| Front | Disc / A / B / C / D / E | 1–5 | Different size labels from BMW |
| Rear | Disc / A / B / C / D / E | 1–5 | |

> Stiffness per size NOT validated for Ferrari. BMW values used as starting point.
> **Changing ARB size on Ferrari requires a LLTD correlation session to verify.**

**Other:**
- Wing: 12–17 deg (1° steps) — 6 aero maps ✅ (axis convention validated)
- DF balance target: **48.3%** (calibrated 2026-04-02 from 17 Hockenheim sessions)
- Weight dist front: 47.6% (measured from IBT corner weights)
- Brake bias: 54.0% (measured from IBT BrakePressureBias)
- LLTD: ~0.510 (car constant, σ=0.0016 across 19 sessions) ✅
- m_eff front: 1439 kg ✅ (7 sessions); rear: 1500 kg ⚠️ (high variance)
- k-NN: 17 Hockenheim + 11 Sebring sessions

**What needs calibration:**
1. Damper zeta (click sweep — highest priority)
2. Heave index → N/mm above index 1 (run index 3, 5, 7 and screenshot ShockDeflStatic)
3. Rear m_eff (more sessions needed, high variance)
4. ARB stiffness per size label (LLTD sweep)

---

### 🟡 Cadillac V-Series.R — Calibration Status: **3 / 10**

**Springs (continuous):**
| Parameter | Range | Legal | Status |
|-----------|-------|-------|--------|
| Front heave | 20–200 N/mm | ✅ | ⚠️ m_eff=266kg (unverified) |
| Rear third | 100–1000 N/mm | ✅ | ⚠️ m_eff=2870kg (BMW copy) |
| Rear spring (coil) | 105–300 N/mm (5 N/mm steps) | ✅ | ❌ ESTIMATE |

> **m_eff rear = 2870 kg is a direct BMW copy. Cadillac has a different mass distribution.**
> **This will produce wrong heave frequency calculations and wrong spring rate recommendations.**

**Torsion Bar:**
| Parameter | Options | Status |
|-----------|---------|--------|
| Front torsion OD | **NOT SET** (empty array) | ❌ MISSING |
| Rear torsion | Not applicable (coil rear) | — |

> `torsion_od_options = []` means the solver falls back to continuous torsion model.
> Cadillac discrete torsion bar OD values NOT populated in `cars.py`.
> From the Cadillac manual: ODs are [13.90, 14.34, 14.76] mm — **these need to be added.**

**Dampers (1–11 clicks):**
| Channel | Status |
|---------|--------|
| All 4 channels | ❌ zeta = BMW copies (0.55/0.20/0.40/0.18), `zeta_is_calibrated=False` |

**ARB:** Same size labels/stiffness as BMW (unvalidated for Cadillac)

**Other:**
- Wing: 12–17 deg — aero maps present ✅
- DF balance target: 52.0% (estimated from aero map sweep only, not validated)
- Weight dist front: 48.5% (from Cadillac manual — unverified in IBT)
- Brake bias: 47.5% (from manual — unverified)
- Cadillac adapter bug: ✅ FIXED (c63c725) — was using BMW adapter

**What needs calibration (priority order):**
1. m_eff front and rear (heave sweep — 3 sessions)
2. Torsion bar OD options (add [13.90, 14.34, 14.76] to cars.py)
3. DF balance target (run sessions at different RH, check aero map)
4. Damper zeta (click sweep)
5. Weight dist front (garage screenshot of corner weights)

---

### ⚪ Porsche 963 — Calibration Status: **2 / 10**

**Springs (continuous):**
| Parameter | Range | Status |
|-----------|-------|--------|
| Front heave | 20–200 N/mm | ❌ m_eff=176kg (unverified, reasonable) |
| Rear third | 100–1000 N/mm | ❌ m_eff=2870kg (BMW copy — WRONG) |

**Torsion Bar:**
- `torsion_od_options = []` — **MISSING**, same problem as Cadillac
- Porsche torsion bar ODs from manual: [13.9, 14.34, 14.76] mm (confirm before adding)

**Dampers (0–11 clicks):**
| Issue | Detail |
|-------|--------|
| DSSV spool valve | BMW uses digressive shim stacks. Porsche uses DSSV spool valve — non-linear |
| All zeta values | BMW copies — completely invalid for spool-valve behavior |
| Has roll spring | `has_roll_dampers=False` — but Porsche 963 has a mechanical roll spring system NOT modeled |

> **The Porsche damper model is fundamentally wrong.** DSSV behavior is non-linear and cannot
> be described by the same force-per-click model used for BMW. The roll spring system also
> adds a compliance path that no other car has. Until both are modeled, Porsche recommendations
> should be treated as directional only.

**ARB:**
- Front only has Soft/Medium/Stiff (no Disconnected option)
- Stiffness values unvalidated for Porsche

**Other:**
- Wing: 12–17 deg — aero maps present ✅
- DF balance target: 50.5% (estimate only)
- CG height: 345mm (BMW-adjacent, unverified)
- Brake bias: 46.0% (from manual, unverified in IBT)

**What needs calibration (priority order):**
1. Model the DSSV damper non-linearity (architecture change required)
2. Add roll spring to solver (currently not modeled)
3. m_eff front and rear (heave sweep)
4. Torsion bar OD options
5. DF balance target

---

### 🟢 Acura ARX-06 — Calibration Status: **2.5 / 10**

**Springs — DIFFERENT ARCHITECTURE:**
| Parameter | Range | Status |
|-----------|-------|--------|
| Front heave | 90–400 N/mm | ⚠️ m_eff=450kg (unverified) |
| Rear third | 60–300 N/mm | ⚠️ m_eff=220kg (much lighter than BMW — this looks right for Acura's rear layout) |

> Unlike BMW/Ferrari (heave is a secondary spring above the corner), Acura's front has
> an **active heave damper** — the "spring rate" acts as a stiffness setting on a
> hydraulic system, not a passive slider. The deflection model is not the same.
> Current heave deflection calculations use the BMW passive slider model — incorrect.

**Torsion Bar:**
| Axle | Options | Status |
|------|---------|--------|
| Front | [13.9, 14.34, 14.76, 15.14, 15.51, 15.86] mm | ⚠️ populated, unvalidated |
| Rear | [13.9–18.2mm] full range | ⚠️ populated, unvalidated |

> Acura has **torsion bars on BOTH axles** (BMW only has front torsion bars). Rear torsion
> stiffness affects rear roll resistance AND couples with the heave damper system.

**Dampers (1–10 clicks per channel):**
- `has_roll_dampers=True` — Acura has dedicated roll damper channels
- Roll damper range: 1–10 clicks (front and rear)
- All zeta values: BMW copies — ❌ invalid

> The roll dampers are a 5th degree of freedom for damping that BMW doesn't have.
> The solver currently does NOT route roll damper recommendations to the correct parameter.

**Wing: 6.0–10.0 deg (0.5° steps)**
> **This is different from all other GTP cars (12–17 deg).** Acura runs significantly lower
> wing angles. The aero map coverage matches (9 maps from 6.0–10.0 deg ✅).

**Other:**
- DF balance target: 49.0% (estimate)
- Weight dist front: 47.0% (from manual, unverified)
- Rear motion ratio: 1.0 (vs BMW's 0.6) — Acura rear geometry is fundamentally different

---

## Calibration Procedures (All Cars)

### Step 1: Effective Mass (m_eff) — **3 sessions**

Purpose: determines how much the car deflects under aero load at a given spring rate.
If wrong: static RH targets will be off by mm, floor clearance calculations wrong.

```
Session 1: Run front heave at index/rate LOW (e.g., softest available)
Session 2: Run front heave at index/rate MID
Session 3: Run front heave at index/rate HIGH
→ Extract ShockDeflStatic from each IBT
→ Plot deflection vs spring rate → slope = F_aero / k_heave
→ F_aero = aero downforce per corner at race speed
→ m_eff = F_aero / g_eff
```

**After m_eff calibration: ALL static RH targets must be recalculated.**
The perch offset formula `perch = target_rh + deflection` depends directly on m_eff.

---

### Step 2: Spring Index → N/mm Validation (Ferrari only) — **2 sessions**

Purpose: verify actual spring rate at each index.

```
For each target index: check garage screenshot
→ Read ShockDeflStatic and TorsionBarDefl
→ Verify: k = corner_weight_N / ShockDeflStatic
→ Update IndexedLookupPoint in cars.py ferrari_indexed_controls
```

**Fastest approach**: run sessions at index 0, 4, 8 (extremes + midpoint) and interpolate.
Do NOT run all 9 — linear interpolation is fine if you have 3 anchor points.

---

### Step 3: LLTD / ARB Stiffness — **3 sessions**

Purpose: determine how many degrees of roll each ARB blade contributes.

```
Session 1: FARB=Disc (disconnected), RARB=Soft, blade 3
Session 2: FARB=Soft, blade 3, RARB=Soft, blade 3
Session 3: FARB=Stiff, blade 5, RARB=Stiff, blade 5
→ Extract LLTD from each IBT (CarLeftRight or suspension loads)
→ Fit: LLTD = f(front_arb_stiffness, rear_arb_stiffness)
→ Update ARBModel.front_stiffness_nmm_deg per size
```

**After ARB calibration: LLTD predictions become meaningful.**
Before this: ARB size recommendations are estimated from BMW stiffness values.

**⚠️ Dependency:** Do Step 1 first. LLTD is affected by the spring deflection under roll.
If m_eff is wrong, the aero-induced load changes will confound the LLTD measurement.

---

### Step 4: DF Balance Target — **1 session**

Purpose: find the actual operating balance the car runs at competitive ride heights.

```
Run 5+ clean laps at representative RH (mid-range heave, standard rear third)
→ Extract dynamic front RH and rear RH from IBT
→ Look up balance in aero map: AeroSurface.df_balance(dfrh, drrh)
→ Average across laps → set as default_df_balance_pct in cars.py
```

**After DF balance calibration: wing angle recommendations become accurate.**
Wrong balance target = solver recommends wrong wing angle (as seen with Ferrari 51.5% → 48.3%).

---

### Step 5: Damper Zeta — **6 sessions**

Purpose: determine the actual damping ratio per channel at each click setting.

```
Session pair (LS Comp): run clicks at 1/4, 1/2, 3/4 of max
Session pair (HS Comp): same
Session pair (LS/HS Rbd): same
→ Extract ShockVel histogram from IBT
→ Fit zeta = F_damper / (2 * sqrt(k * m_eff))
→ Back-calculate effective force per click for LS and HS regimes
→ Update DamperModel.zeta_* and set zeta_is_calibrated=True
```

> **⚠️ Dependency chain:** Do Steps 1 and 2 before Step 5.
> Zeta depends on m_eff (via critical damping force). Wrong m_eff → wrong zeta targets.
> Wrong spring rate (unvalidated index) → wrong natural frequency → wrong zeta at any click.

---

## What Happens When You Change A Torsion Bar

This is the most common misuse. The full cascade:

```
1. You change: Torsion bar OD (e.g., 13.9 → 14.34mm)
2. Front corner spring rate increases: ~204 → ~221 N/mm (+8%)
3. Front roll stiffness increases by same proportion
4. To maintain same LLTD: FARB blade must decrease OR RARB must increase
5. Front heave deflection under load DECREASES (stiffer front)
   → Car rides higher at front at same speed
   → Dynamic front RH increases by Δdefl = F_aero * (1/k_old - 1/k_new)
   → Typical: ~1-2mm RH change per torsion bar step
6. Static front RH TARGET must DECREASE by same amount to keep same dynamic RH
7. If static RH doesn't change: DF balance shifts (more front downforce)
8. If DF balance shifts: wing angle recommendation may change
9. Damper natural frequency has changed: zeta at current click settings is now different
   → You haven't changed damper clicks, but effective damping ratio has changed
```

**Minimum required actions after changing torsion bar OD:**
- [ ] Update static RH target: `static_rh_new = static_rh_old - Δdefl`
- [ ] Re-check ARB blade to maintain LLTD
- [ ] Re-run one IBT session to verify new dynamic RH
- [ ] Mark damper recommendations as EST until re-calibrated

---

## Quick Reference: Parameter Names (iRacing garage ↔ solver)

| iRacing Garage Label | Solver Field | Car |
|---------------------|-------------|-----|
| TorsionBarDiameter | `torsion_bar_od_mm` | BMW |
| HeaveSpringRate (front) | `front_heave_nmm` | All |
| ThirdSpringRate (rear) | `rear_third_nmm` | All |
| RearSpringRate | `rear_spring_nmm` | BMW/Porsche |
| ARBSize (front) | `front_arb_size` | All |
| ARBBlade (front) | `front_arb_blade` | All |
| ARBSize (rear) | `rear_arb_size` | All |
| ARBBlade (rear) | `rear_arb_blade` | All |
| RideHeight (front, static) | `front_rh_static` | All |
| RideHeight (rear, static) | `rear_rh_static` | All |
| AeroWingAngle | `wing_angle_deg` | All |
| LsCompDamp (per corner) | `lf/rf/lr/rr_ls_comp_clicks` | All |
| HsCompDamp (per corner) | `lf/rf/lr/rr_hs_comp_clicks` | All |
| LsRbdDamp (per corner) | `lf/rf/lr/rr_ls_rbd_clicks` | All |
| HsRbdDamp (per corner) | `lf/rf/lr/rr_hs_rbd_clicks` | All |
| DiffPreload | `diff_preload_nm` | All |
| DiffClutchPlates | `diff_clutch_plates` | BMW |
| FrontCamber | `front_camber_deg` | All |
| RearCamber | `rear_camber_deg` | All |
| FrontToe | `front_toe_mm` | All |
| RearToe | `rear_toe_mm` | All |
| BrakeBias | `brake_bias_pct` | All |
| TCGain | `tc_gain` | All |
| TCSlip | `tc_slip` | All |
| ShockDeflStatic | internal (deflection calibration) | All |
| TorsionBarDefl | internal (torsion stiffness calibration) | Ferrari |
| CarLeftRight | internal (LLTD measurement) | All |

---

## Calibration Session Budget Per Car

| Car | Sessions to 4/10 | Sessions to 6.5/10 | Sessions to 8/10 |
|-----|-----------------|---------------------|-----------------|
| BMW | Done ✅ | Done ✅ | Done ✅ |
| Ferrari | Done ✅ | ~5 more (dampers, index validation) | ~15 more (full damper sweep) |
| Cadillac | 3–5 (m_eff) | 10–12 | 20+ |
| Porsche | 3–5 (m_eff) | Blocked (DSSV model needed) | Blocked |
| Acura | 3–5 (m_eff) | 12–15 | 20+ |

---

## What "Calibrated" Actually Means Here

A parameter is **calibrated** when:
1. Its value was derived from IBT telemetry (not a manual estimate or BMW copy)
2. It has been validated against at least one independent session
3. The dependency chain below it has been checked — changing it doesn't silently break upstream assumptions

A parameter is **estimated** when:
- It was calculated from a physics formula with assumed constants
- It was copied from another car
- It was derived from a single session or single anchor point

The delta card confidence tiers (HIGH/MED/EST) reflect this directly.
If you see ⚠️ EST on a recommendation — the physics behind it hasn't been validated for that car.

---

*Last updated: 2026-04-02 by claw-research*
*Next review: after any spring, damper, or torsion bar calibration session*
