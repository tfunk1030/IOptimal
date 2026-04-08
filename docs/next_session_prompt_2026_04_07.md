# Next-Session Continuation Prompt — Porsche/Algarve Focus

Copy the block below into a fresh Claude Code session to continue this work. This version (2026-04-07 v3) is **Porsche 963 / Autodromo Internacional do Algarve only** — every command, comparison, and physics walkthrough targets Porsche/Algarve. BMW regression is still run as a safety net (so Porsche changes don't accidentally break BMW), but the focus and verification cycles are Porsche/Algarve.

---

## PROMPT TO PASTE

```
ultrathink. Read CLAUDE.md, docs/repo_audit.md, docs/overhaul_plan_2026_04_06.md,
docs/calibration_guide.md, and the memory files (MEMORY.md and every project_*
and feedback_* file it links). These contain the full state of the iOptimal
GTP setup solver project as of 2026-04-07.

This session is focused EXCLUSIVELY on Porsche 963 at Autodromo Internacional
do Algarve — Grand Prix. BMW/Sebring is the only other calibrated car/track
pair and we still run its regression test as a safety net (so Porsche changes
don't accidentally break BMW), but every analysis, comparison, hypothesis,
and fix in this session targets Porsche/Algarve.

## STEP 0: BEFORE TOUCHING ANY CODE — run the verification protocol

You MUST do all of Step 0 before proposing or writing a single change. This is
the only way to know what is actually broken vs what looks broken in passing.

### Step 0a: Identify the most recent Porsche/Algarve IBT file

```
python -c "
import os, glob
porsche = sorted(glob.glob('ibtfiles/porsche963gtp_algarve*.ibt'),
                 key=os.path.getmtime, reverse=True)[:5]
print('Most recent Porsche/Algarve IBTs:')
for p in porsche:
    mt = os.path.getmtime(p)
    sz = os.path.getsize(p) / (1024*1024)
    import datetime
    print(f'  {datetime.datetime.fromtimestamp(mt)} | {sz:.1f} MB | {p}')
"
```

Pick the MOST RECENT one (highest mtime). Use this as your test input for
everything below. Quote the path in subsequent commands; the filenames have
spaces.

### Step 0b: Read the raw IBT contents directly

DO NOT trust any analyzer-derived values yet — read the IBT directly so you
have your own ground truth. Run:

```
python -c "
from track_model.ibt_parser import IBTFile
import numpy as np
PATH = '<MOST RECENT PORSCHE IBT>'
f = IBTFile(PATH)
print('Track:', f.track_info())
print('Car:', f.car_info())
print('Duration:', f.duration_s, 's,  dt:', f.dt, 's')
laps = f.lap_boundaries()
print('Laps:', len(laps))
# Lap times (skip warmup lap 0)
for lt in f.lap_times(min_time=60.0)[:8]:
    print('  lap:', lt)
chans = [
    'Speed', 'LFrideHeight', 'RFrideHeight', 'LRrideHeight', 'RRrideHeight',
    'LatAccel', 'LongAccel', 'VertAccel',
    'LFshockDefl', 'RFshockDefl', 'LRshockDefl', 'RRshockDefl',
    'CFshockVel', 'CRshockVel',
    'Brake', 'Throttle', 'SteeringWheelAngle', 'FuelLevel',
    'Roll', 'Pitch', 'Yaw',
]
print()
print('Channel statistics (full session):')
for c in chans:
    if f.has_channel(c):
        a = f.channel(c)
        print(f'  {c}: n={len(a)}, mean={np.mean(a):.4f}, '
              f'min={np.min(a):.4f}, max={np.max(a):.4f}, '
              f'p95_abs={np.percentile(np.abs(a), 95):.4f}')
    else:
        print(f'  {c}: NOT PRESENT')
print()
# Sample at-speed RH (>150 kph)
if f.has_channel('Speed') and f.has_channel('LFrideHeight'):
    sp = f.channel('Speed') * 3.6
    fast = sp > 150
    for c in ['LFrideHeight','RFrideHeight','LRrideHeight','RRrideHeight']:
        if f.has_channel(c):
            rh = f.channel(c) * 1000.0  # m -> mm
            print(f'  {c} at >150kph: mean={rh[fast].mean():.2f}mm, '
                  f'p05={np.percentile(rh[fast],5):.2f}mm, '
                  f'p95={np.percentile(rh[fast],95):.2f}mm')
"
```

Write down (in your reply to the user) the actual at-speed ride heights, the
shock velocity p95 values, and the lap times. THIS IS YOUR GROUND TRUTH for
the rest of the session.

### Step 0c: Read the current setup state from the IBT session info

The IBT header contains the garage setup that was loaded for that session.
Extract it so you can compare against what the solver recommends:

```
python -c "
from track_model.ibt_parser import IBTFile
PATH = '<MOST RECENT PORSCHE IBT>'
f = IBTFile(PATH)
# session_info_yaml is the parsed setup string
si = getattr(f, 'session_info_yaml', None) or getattr(f, 'session_info', None)
if si is None:
    print('No session info available')
else:
    # Print just the DriverInfo / SetupYaml chunks
    if isinstance(si, dict):
        for key in ('DriverInfo', 'SessionInfo', 'CarSetup'):
            if key in si:
                print(f'=== {key} ===')
                print(si[key])
    else:
        print(str(si)[:2000])
"
```

If `session_info_yaml` isn't available on IBTFile, look at the analyzer:
```
python -c "
from analyzer.setup_reader import read_setup_from_ibt
PATH = '<MOST RECENT PORSCHE IBT>'
setup = read_setup_from_ibt(PATH)
for k, v in vars(setup).items():
    if not k.startswith('_'):
        print(f'  {k} = {v}')
" 2>&1 | head -60
```

Note: the function name may differ (`read_current_setup`, `parse_setup`,
etc.) — search `analyzer/setup_reader.py` for the public entry point if the
above fails. Once you have the current setup, write down: front/rear pushrod,
heave spring, third spring, rear corner spring, ARB sizes/blades, camber/toe,
damper clicks, brake bias.

### Step 0d: Run BOTH the standalone solver and the pipeline for Porsche

The solver and pipeline have 14+ known divergences (see Phase 4 of
overhaul_plan_2026_04_06.md). The user's frustration is rooted in this
inconsistency. You must run BOTH and DIFF the outputs:

```
# Standalone solver (track-only — no IBT, uses pre-built track profile)
python -m solver.solve --car porsche --track "algarve grand prix" \
    --wing 17 --json /tmp/porsche_solver_only.json \
    --sto /tmp/porsche_solver_only.sto

# Full pipeline (IBT-driven, telemetry-adaptive)
python -m pipeline.produce --car porsche \
    --ibt "<MOST RECENT PORSCHE IBT>" \
    --wing 17 --fuel 58 \
    --json /tmp/porsche_pipeline.json \
    --sto /tmp/porsche_pipeline.sto
```

Then DIFF the .sto outputs and the JSON parameter values:

```
python -c "
import re, json
def extract(p):
    return dict(re.findall(r'Id=\"(CarSetup_[^\"]+)\"\\s+Value=\"([^\"]+)\"', open(p).read()))
a = extract('/tmp/porsche_solver_only.sto')
b = extract('/tmp/porsche_pipeline.sto')
print('=== PORSCHE solver vs pipeline ===')
diffs = {k: (a[k], b[k]) for k in a.keys() & b.keys() if a[k] != b[k]}
only_a = sorted(set(a.keys()) - set(b.keys()))
only_b = sorted(set(b.keys()) - set(a.keys()))
print(f'  diffs: {len(diffs)}')
for k, (sa, sb) in sorted(diffs.items()):
    print(f'    {k}: solver={sa!r}  pipeline={sb!r}')
print(f'  only in solver-only: {only_a[:15]}')
print(f'  only in pipeline:    {only_b[:15]}')
"
```

Every diff is a known or unknown divergence between the two solver paths.
Document each one. If the diff is intentional (e.g., the pipeline applies a
modifier that's only available with telemetry), say so. If it's a regression,
that's a bug.

### Step 0e: Compare parameter NAMES to the actual garage XML IDs

Look for missing/wrong/TODO parameters in the Porsche pipeline output:

```
grep "TODO" /tmp/porsche_pipeline.sto
grep "Porsche" /tmp/porsche_pipeline.sto | head
grep "FrontRoll\|Rear3rd\|RollSpring\|RearRoll" /tmp/porsche_pipeline.sto
```

Verify the Porsche-specific parameters are present:
- `CarSetup_Chassis_LeftFront_RollSpring` and `CarSetup_Chassis_RightFront_RollSpring`
  (Porsche uses front roll spring, NOT torsion bar)
- `CarSetup_Dampers_FrontRoll_LsDamping`, `_HsDamping`, `_HsDampSlope`
- `CarSetup_Dampers_Rear3rd_LsCompDamping`, `_HsCompDamping`, `_LsRbdDamping`,
  `_HsRbdDamping` (Porsche has separate rear 3rd damper, 4 channels, no slope)
- `CarSetup_Dampers_RearRoll_LsDamping`, `_HsDamping`
- `CarSetup_BrakesDriveUnit_DiffSpec_CoastRampAngle` AND `_DriveRampAngle`
  (Porsche uses two separate XML IDs, NOT the combined string BMW uses)
- All four corners of `CarSetup_TiresAero_*_StartingPressure` should be the
  per-corner cold pressure, not all the same value.

Any TODO comment is a parameter the system can compute but doesn't know how
to write. These are mapping bugs — fix in `output/setup_writer.py`
`_PORSCHE_PARAM_IDS`.

### Step 0f: Compare solver output to what the IBT actually shows

For each value in `/tmp/porsche_pipeline.sto`, check whether it agrees with
the IBT telemetry from Step 0b. Build a comparison table:

| Output value | IBT-derived ground truth | Solver output | Δ |
|---|---|---|---|
| Static front RH (target) | session_info `LFrideHeight` (low-speed mean) | `CarSetup_Chassis_LeftFront_RideHeight` | |
| Static rear RH (target) | session_info `LRrideHeight` (low-speed mean) | `CarSetup_Chassis_LeftRear_RideHeight` | |
| Dynamic front RH | LFrideHeight at >150 kph | rake solver `dynamic_front_rh_mm` | |
| Dynamic rear RH | LRrideHeight at >150 kph | rake solver `dynamic_rear_rh_mm` | |
| Front camber | session_info `LFcamber` | `CarSetup_Chassis_LeftFront_Camber` | |
| Rear camber | session_info `LRcamber` | `CarSetup_Chassis_LeftRear_Camber` | |
| Front pushrod | session_info | `CarSetup_Chassis_Front_PushrodLengthOffset` | |
| Rear pushrod | session_info | `CarSetup_Chassis_Rear_PushrodLengthOffset` | |
| Front heave spring | session_info | `CarSetup_Chassis_Front_HeaveSpring` | |
| Rear third spring | session_info | `CarSetup_Chassis_Rear_HeaveSpring` | |
| Rear corner spring | session_info | `CarSetup_Chassis_LeftRear_SpringRate` | |
| Cold tyre LF | session_info `LFcoldPressure` | `CarSetup_TiresAero_LeftFront_StartingPressure` | |
| Brake bias | session_info | `CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias` | |

Print the comparison out for the user. For each row where |Δ| is large, you
MUST diagnose the physics in Step 0g.

### Step 0g: Reason in physics for every discrepancy

For every mismatch you identify in Step 0f, walk through the physics:

1. **Ride height mismatch** — check in this order:
   - Aero compression model (V² scaling). The compression at the operating
     speed is `comp.front_at_speed(track.median_speed_kph)`, not the reference
     speed. Compute the expected compression and verify the rake solver uses it.
   - Static RH model coefficients. Read `data/calibration/porsche/models.json`
     and check that `front_ride_height` and `rear_ride_height` use the
     compliance features (`inv_front_heave`, `inv_rear_third`, `inv_rear_spring`).
     If they don't, auto-cal regressed back to a worse model.
   - Garage feasibility cap. The rake solver caps target rear RH if the
     pushrod can't reach it. Look for the warning message in the pipeline
     output.
   - Pushrod range exhaustion. If rear pushrod = +40 (Porsche max), the
     target was clamped.

2. **Camber/toe mismatch** — check:
   - `_optimal_camber()` in solver/wheel_geometry_solver.py uses
     `roll_gain * representative_roll_deg` where representative_roll is the
     measured p95 lateral roll. Verify the roll_gain in the car definition
     and the measured roll from telemetry.
   - `roll_gains_calibrated` flag. If False, the geometry solver may be
     using estimates.
   - Garage step snap can shift the recommendation by 0.1°.

3. **Damper click mismatch** — check:
   - `damper_zeta` calibration session count (in models.json zeta_n_sessions).
     For Porsche this should be ~79.
   - Reference velocity. HS reference is the track p95 shock velocity. If
     p95 is wrong, the click force will be wrong.
   - Force-per-click for DSSV vs shim-stack. Porsche uses DSSV; the
     coefficients in cars.py should reflect that. Currently they're
     marked as estimates pending click-sweep validation.
   - Front Roll HS slope MUST be propagated through
     `solution_from_explicit_settings` (we fixed this earlier — verify it
     stayed fixed).
   - Rear 3rd damper has 4 channels, NO HS slope. Verify all 4 are written.

4. **Spring rate mismatch** — check:
   - `m_eff` value used by the heave solver. It's the scalar `rear_m_eff_kg`
     unless the rate-table lookup is enabled (currently OFF for Porsche
     because the table is noisy). Look for the actual value the solver used.
   - `sigma_target_mm` (10mm by default) — is that right for Algarve's
     surface roughness?
   - Modifier floors. The solver may be hitting `front_heave_min_floor_nmm`
     or `rear_third_min_floor_nmm` from `solver/modifiers.py`.
   - Garage range bounds. Porsche heave is 150-600, third is 0-800.

5. **DF balance error** — check:
   - The aero map's achievable range at the recommended dynamic RH. For
     Porsche the aero axes are SWAPPED (`aero_axes_swapped=True` in
     car definition). Test the map directly with `aero_model.interpolator`.
   - Default `default_df_balance_pct` in the Porsche definition. Currently
     ~50.5% (47.1% weight dist + ~3.4%). Is this physically right or should
     it be different?
   - Garage feasibility cap. If the pipeline prints
     `Capping rear dynamic` then the target was unreachable.

6. **LLTD mismatch** — check:
   - Roll spring formula. Porsche uses `k * IR² * (t/2)²` for the SINGLE
     front roll spring, NOT `2 * k * (t/2)²` (which would be for paired
     corner springs). The correct formula is in `solver/arb_solver.py`
     `_corner_spring_roll_stiffness`. See
     `feedback_roll_spring_not_corner_spring.md`.
   - Installation ratio. Porsche front_roll_spring_installation_ratio = 0.882
     (calibrated from measured LLTD 50.3%).
   - ARB stiffness manual override. Auto-cal disagrees by 170787%. The
     manual values in cars.py may or may not be right — verify against
     the actual measured roll gradient from the IBT.

7. **Brake bias / diff / TC mismatch** — check:
   - Brake bias is `compute_brake_bias() + telemetry adjustments`. Walk
     through the additions in `solver/supporting_solver.py`.
   - Master cylinder ratio computation. Compare nominal vs measured from
     IBT.
   - Diff coast/drive ramps for Porsche use SEPARATE XML IDs
     (`DiffSpec_CoastRampAngle` and `_DriveRampAngle`), not the combined
     string BMW uses. Verify both are in the .sto.
   - TC gain/slip telemetry-driven additions.

For each hypothesis, identify the file/line where the relevant computation
happens. THEN propose a fix. Do not propose changes without identifying
the root cause.

## Critical context you must absorb before doing anything

1. **The user explicitly forbids fallbacks to baselines and hardcoded values.**
   They want true physics solving and Porsche accurately calibrated from data
   and IBT files. The pattern `getattr(car, "field", bmw_default)` is BANNED.
   Read `feedback_no_silent_fallbacks.md` for the rules.

2. **The user has been burned by claims-without-verification.** Do NOT say
   anything is "fixed" or "working" without running the actual command and
   checking the actual output. Read `feedback_verify_dont_claim.md`.
   The regression test `python tests/test_setup_regression.py` MUST pass
   for both BMW/Sebring (safety net) and Porsche/Algarve (focus) before
   any change is considered complete.

3. **Lap time is NOT a quality signal for setups.** This is physics-first, not
   pattern matching. Read `feedback_no_laptime_setup_selection.md`.

4. **The strict calibration gate** classifies subsystems as
   `calibrated`/`weak`/`uncalibrated`. Weak surfaces a loud `WEAK CALIBRATION
   DETECTED` banner but still produces output (legacy code expects values).
   To make weak actually block, ~170 references to `step4`/`step5`/`step6`
   need None-handling first. Read `project_strict_gate_2026_04_07.md`.

5. **Compliance physics (1/k)** is the new default for Porsche static RH and
   deflection models. Both linear and compliance coefficient slots coexist in
   `RideHeightModel`/`DeflectionModel`. Porsche uses compliance.
   Read `project_compliance_physics_2026_04_07.md`.

## Current state of Porsche/Algarve (verified)

- Both regression tests pass: `python tests/test_setup_regression.py`
- Porsche/Algarve confidence:
  - `aero_compression`: HIGH (17 IBT sessions)
  - `ride_height_model`: HIGH R²=0.94 (front 1.00 / rear 0.94)
  - `deflection_model`: HIGH R²=0.97
  - `damper_zeta`: HIGH (79 click-sweep sessions)
  - `lltd_target`: calibrated from IBT measured (12 sessions)
  - `roll_gains`: IBT-calibrated
  - `arb_stiffness`: MANUAL_OVERRIDE — auto-cal contradicts car definition
    by 170787%. ONLY weak step. This is the priority debug target.
- 18 silent BMW fallback patterns removed from solver/ — Porsche is no longer
  silently inheriting BMW values for any subsystem.
- `damper_solver` and `pushrod_for_target_rh` now raise `ValueError` instead
  of returning baseline fallbacks (gate blocks before they're called).

## Pending work for Porsche/Algarve, in priority order

### HIGH PRIORITY

1. **Resolve the Porsche ARB calibration**. Auto-cal back-solve gives
   170787% error vs the manual override stiffness `[0, 600]` (front,
   Disconnected/Connected) and `[0, 150, 300, 450]` (rear, Disc/Soft/Med/Stiff).
   - Read `car_model/auto_calibrate.py` — find the ARB back-solve function
     (search for `arb_calibrated` or `roll_gradient`).
   - Read the Porsche ARB definition in `car_model/cars.py` near
     `is_calibrated=True` for the Porsche `arb=ARBModel(...)`.
   - Read `data/calibration/porsche/models.json` `status.arb_stiffness` for
     the actual back-solve numbers.
   - From the most recent Porsche IBT, extract the measured roll gradient
     (LatAccel vs Roll telemetry). Compute total roll stiffness directly:
     `K_roll = M_sprung * g * h_cg * lat_g / roll_rad`.
   - Compare against the model prediction: `K_roll_predicted = K_springs +
     K_arb_front + K_arb_rear`. If predicted ≠ measured, either the
     algorithm is wrong or the manual values are wrong.
   - Either fix the algorithm OR update cars.py with values that pass the
     back-solve.

2. **Verify the compliance RH model is actually being used by the solver
   for Porsche on the most recent IBT**. Check `data/calibration/porsche/
   models.json` `front_ride_height.feature_names` and `rear_ride_height.
   feature_names`. They should include `inv_front_heave`, `inv_rear_third`,
   `inv_rear_spring`. If they don't, auto-cal regressed and the model is
   using linear features (R² will be lower).

3. **Phase 2 of the overhaul: Unify the 3 RH models for Porsche**.
   `PushrodGeometry`, `RideHeightModel`, and `GarageOutputModel` are all
   consistent now (compliance physics applied to all three) but still
   separate classes. The 12 `reconcile_ride_heights()` call sites still
   exist. Goal: one `PorscheRideHeightPredictor` (per-car). See Phase 2 in
   `docs/overhaul_plan_2026_04_06.md`. 3-4 session refactor.

4. **Make `weak` status actually block for Porsche Step 4**. Currently weak
   surfaces warnings but still produces output because ~170 references to
   `step4`/`step5`/`step6` across the codebase assume those steps exist.
   Audit:
   ```
   grep -rn "base_result\.step4\|step4\." --include="*.py" output/ pipeline/ solver/
   ```
   Add `if step4 is not None:` guards everywhere. Then flip Porsche ARB
   `weak_block` to `blocked = True` in `calibration_gate.py`.

### MEDIUM PRIORITY

5. **Validate and enable m_eff rate-table lookup for Porsche**. Infrastructure
   shipped, gated off via `m_eff_rate_lookup_enabled=False`. Rear table has
   5.9x range and non-monotonic averages — too noisy. Need either:
   (a) more click-sweep IBT data with at least 10 samples per spring rate,
   or (b) smoothing/averaging in the lookup.

6. **Fix Porsche brake bias telemetry adjustments**. The pipeline output
   shows multiple compounding adjustments (trail brake, decel, lock proxy,
   asymmetry, MC ratio). Verify each is justified by physics for the
   Porsche DSSV/Multimatic chassis specifically — some may be BMW-derived.

7. **Audit the Porsche `default_df_balance_pct = 50.5`**. Is 50.5% the right
   target for Porsche, or should it be different given DSSV dampers and
   weight distribution 47.1%? Walk through the physics.

### LOW PRIORITY

8. **Add a Porsche-specific regression test that checks PHYSICS quantities**,
   not just .sto file equality. E.g., assert `dynamic_rh_front < 30mm`,
   `df_balance_pct ≈ target ± 0.5%`, `lltd ≈ measured ± 1%`.

9. **Document the Porsche setup writer mappings** — every Porsche-specific
   XML ID should be listed in a single reference, with comments on why
   Porsche's mapping differs from BMW (separate diff ramps, roll spring,
   rear 3rd damper, front heave without HS slope).

## Verification protocol (ALWAYS DO THIS — every change, no exceptions)

Before claiming any change is complete, run all FIVE checks in order. Skipping
any of them means the user will catch you and we lose more time than the check
would have taken.

### Check 1: Regression tests
```
python tests/test_setup_regression.py
```
Both BMW/Sebring (safety net) and Porsche/Algarve (focus) must pass. If they
fail, either fix the change or — only with explicit user approval — regenerate
the Porsche baseline (only if the change is an intentional, physically-justified
improvement).

### Check 2: Run pipeline on the most recent Porsche/Algarve IBT
```
python -m pipeline.produce --car porsche \
    --ibt "<MOST RECENT PORSCHE IBT>" --wing 17 --fuel 58 \
    --json /tmp/porsche_after.json --sto /tmp/porsche_after.sto
```
Read the actual output values (don't just check exit code). Verify:
- `WEAK CALIBRATION DETECTED` banner appears for ARB (Step 4) only
- `CALIBRATION CONFIDENCE` block shows expected R² and source for every
  Porsche subsystem (compare against the "Current state" list above)
- Step 1: front pushrod within (-40, +40) garage range, front static RH
  near 30mm (sim minimum), DF balance close to 50.5% target (or capped
  with a warning if unreachable)
- Step 2: front_heave_nmm in (150, 600), rear_third_nmm in (0, 800)
- Step 3: rear_spring_rate_nmm in (105, 280), front_wheel_rate_nmm = 100
  (Porsche uses front roll spring, fixed at 100 N/mm baseline)
- Step 4: Front ARB (Connected/Disconnected) and Rear ARB blade in
  (1, 16), with WEAK warning about manual override
- Step 5: front_camber_deg in (-2.9, 0.0), rear_camber_deg in (-1.9, 0.0),
  front_toe_mm and rear_toe_mm in legal ranges
- Step 6: all damper clicks within (0, 11) — the legacy warning about
  `lr_ls_rbd=12 clamped to 11` should be checked; if it appears, the
  damper solver is producing values outside the garage range
- Step 7: brake_bias_pct near 44.75 (calibrated baseline), diff/TC sensible

### Check 3: Solver vs pipeline diff (the divergence bugs surface here)
```
python -m solver.solve --car porsche --track "algarve grand prix" \
    --wing 17 --sto /tmp/porsche_solver_only.sto
python -c "
import re
def extract(p):
    return dict(re.findall(r'Id=\"(CarSetup_[^\"]+)\"\\s+Value=\"([^\"]+)\"', open(p).read()))
a = extract('/tmp/porsche_solver_only.sto')
b = extract('/tmp/porsche_after.sto')
diffs = {k: (a[k], b[k]) for k in a.keys() & b.keys() if a[k] != b[k]}
print(f'porsche solver-vs-pipeline diffs: {len(diffs)}')
for k, (sa, sb) in sorted(diffs.items()):
    print(f'  {k}: solver={sa}  pipeline={sb}')
"
```
Document any new diffs. If a diff is intentional, explain why. If it's a
regression you just introduced, fix it.

### Check 4: IBT vs output sanity check (Porsche/Algarve specific)
```
python -c "
from track_model.ibt_parser import IBTFile
import numpy as np
PATH = '<MOST RECENT PORSCHE IBT>'
f = IBTFile(PATH)
sp = f.channel('Speed') * 3.6
fast = sp > 150
print('=== Porsche/Algarve at-speed reality check ===')
for c in ['LFrideHeight','RFrideHeight','LRrideHeight','RRrideHeight']:
    if f.has_channel(c):
        rh = f.channel(c) * 1000.0
        print(f'  {c}: at>150kph mean={rh[fast].mean():.2f}mm, '
              f'p05={np.percentile(rh[fast],5):.2f}, '
              f'p95={np.percentile(rh[fast],95):.2f}')
print()
print('Solver output dynamic RH targets:')
import json
out = json.load(open('/tmp/porsche_after.json'))
s1 = out.get('step1_rake', {})
print(f'  dynamic_front_rh_mm: {s1.get(\"dynamic_front_rh_mm\")}')
print(f'  dynamic_rear_rh_mm:  {s1.get(\"dynamic_rear_rh_mm\")}')
print(f'  static_front_rh_mm:  {s1.get(\"static_front_rh_mm\")}')
print(f'  static_rear_rh_mm:   {s1.get(\"static_rear_rh_mm\")}')
print(f'  df_balance_pct:      {s1.get(\"df_balance_pct\")}')
"
```
Compare the dynamic RH targets against the IBT-derived at-speed means.
The delta should be < 2mm for a calibrated car. If it's larger:
- The aero compression at the operating speed is wrong → check
  `comp.front_at_speed(track.median_speed_kph)`.
- The garage feasibility cap is firing → look for the rake solver warning.
- The static RH model is mispredicting → check coefficients in
  `data/calibration/porsche/models.json`.
- The driver is consistently riding higher/lower than the target → that's
  a real physical mismatch and the solver should adapt.

### Check 5: Physics walkthrough
Articulate in plain language why the new output is more correct than the old
output. If you can't explain the physics, you don't understand the change.
Stop and re-read.

| Symptom | First places to look |
|---|---|
| RH off by >2mm | `RideHeightModel` coeffs (compliance vs linear), aero compression at track speed, garage feasibility cap, pushrod range exhaustion |
| Spring rate at floor/ceiling | `m_eff` value (rate-table vs scalar), `sigma_target_mm`, modifier floors, garage range |
| Damper clicks at extreme | zeta calibration sessions (should be 79 for Porsche), reference velocity p95, force-per-click for DSSV |
| Camber wrong | `roll_gain` calibration, `representative_roll_deg` from telemetry, garage step snap |
| LLTD off by >2% | roll spring formula `k*IR²*(t/2)²` (Porsche), installation ratio 0.882, ARB manual override conflict |
| DF balance error | aero map coverage at recommended RH, default DF target, `aero_axes_swapped=True` for Porsche |
| ARB recommendation suspicious | this is THE known weak step — auto-cal disagrees with hand values by 170787% |

## How to handle the user's specific frustrations

- **"It shouldn't be this hard"**: agreed. The codebase has 156+ step5/6
  references and ~58k LOC. We're slowly unwinding this, not rewriting
  from scratch. Each session ships incremental wins backed by regression tests.

- **"No fallbacks"**: take this LITERALLY. If you find a hidden default for
  Porsche somewhere, remove it. If a Porsche value can't be derived from
  data or physics, the calibration gate should block it.

- **"Verify don't claim"**: every claim of "fixed" or "working" must be
  backed by a command output you actually ran. The user has memory of being
  burned by this exact pattern many times. Run all 5 checks above.

- **"True physics solve"**: every value in the Porsche output must be either
  (a) measured from telemetry, (b) derived from first-principles physics,
  (c) per-car hand-calibration with explicit warning. Hardcoded BMW values
  for Porsche are forbidden.

## Working cycle (mandatory for every change)

1. **Hypothesis** — what's wrong, why, where (file/line), what fix you propose,
   what the expected new Porsche output is.
2. **Implementation** — make the smallest change that addresses the root cause.
   No drive-by refactors.
3. **Verification** — run all 5 checks above.
4. **Physics walkthrough** — explain why the new Porsche output is more correct
   than the old, in terms of measurable quantities (sigma, RH, force, etc.).
5. **Commit point** — only after all 5 checks AND the walkthrough.

If verification fails, GO BACK to step 1. Do not stack changes on top of an
unverified state. Do not "fix one more thing" — finish the current change
cleanly first.

## First step in this session

**Ultrathink** before doing anything.

1. Read the critical files: CLAUDE.md, docs/repo_audit.md,
   docs/overhaul_plan_2026_04_06.md, docs/calibration_guide.md, and
   especially the new memory files
   `project_compliance_physics_2026_04_07.md`,
   `project_strict_gate_2026_04_07.md`,
   `feedback_no_silent_fallbacks.md`.
2. Run STEP 0 (Steps 0a through 0g) of this prompt to establish your ground
   truth. You MUST do all of Step 0 before proposing any change.
3. Write down (in your reply to the user) the top 3 discrepancies you found
   between the Porsche IBT data and the Porsche solver/pipeline output, with
   a physics hypothesis for each and a specific file/line where the root
   cause likely lives.
4. Ask the user which discrepancy to address first, or which of the HIGH
   PRIORITY pending items above to focus on.
5. Do not dive into a multi-hour refactor without confirming the priority.

If the user says "ultrathink and implement all" or similar broad direction,
work on the discrepancies you discovered in priority order (biggest physics
violation first), then move to the HIGH PRIORITY items. Use TaskCreate to
track progress. Verify after EVERY change with all 5 checks above.
```

---

## Session prep checklist (do these BEFORE the new session)

1. ✅ Read this prompt and confirm it makes sense
2. ✅ Make sure the regression test fixtures are committed:
   - `tests/test_setup_regression.py`
   - `tests/fixtures/baselines/bmw_sebring_baseline.sto`
   - `tests/fixtures/baselines/porsche_algarve_baseline.sto`
3. ✅ Have the latest Porsche/Algarve IBT files in `ibtfiles/` (the prompt
   auto-discovers the most recent one)
4. ✅ The new session can use the existing memory files via the auto-memory
   system (`C:\Users\VYRAL\.claude\projects\C--Users-VYRAL-ioptimal\memory\`)
