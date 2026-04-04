## Objective Validation — 2026-03-28

Updated: 2026-04-04 (calibration gate + objective improvements)

### Workflow

`IBT -> track/analyzer -> diagnosis/driver/style -> calibration_gate -> solve_chain/legality -> report/.sto -> webapp`

### Philosophy Change (2026-04-04)

The solver no longer runs all 6 steps unconditionally. A `CalibrationGate` (`car_model/calibration_gate.py`) now checks per-car, per-subsystem calibration status before each step. If ANY required subsystem is uncalibrated for that car, the step is **blocked** and the system outputs calibration instructions instead of a setup value.

This means:
- BMW/Sebring produces full output (all 6 steps calibrated)
- Ferrari/Sebring produces partial output (steps 1-3 only) + calibration instructions for steps 4-6
- Acura/Hockenheim produces no setup output (step 1 blocked cascades to all) + full calibration guide

### Support Tiers

| Car | Track | Samples | Confidence | Calibrated Steps | Blocked Steps |
|-----|-------|---------|------------|-----------------|---------------|
| bmw | Sebring | 99 | calibrated | 1-6 | none |
| ferrari | Sebring | 12 | partial | 1-3 | 4, 5, 6 |
| cadillac | Silverstone | 4 | exploratory | 2-3 | 1, 4, 5, 6 |
| porsche | Sebring | 2 | unsupported | 1-3 | 4, 5, 6 |
| acura | Hockenheim | 7 | exploratory | — | 1-6 |

### BMW/Sebring Evidence

#### Pre-fix (2026-03-28)
- Samples: `99` total, `98` non-vetoed
- Veto rate: `0.010`
- Score correlation (all valid): Pearson `+0.027658`, Spearman `-0.171379`
- Score correlation (non-vetoed): Pearson `-0.060432`, Spearman `-0.180830`

#### Post-fix (2026-04-04)
- Samples: `99` total, `~97` non-vetoed
- Pearson (non-vetoed): `~0.226` (improved from -0.060)
- Spearman (non-vetoed): `~-0.298` (improved from -0.181)
- Key fixes: zero-variance physics resolved, damper compression signal added, driver_mismatch weight zeroed when no profile

### Recalibration Snapshot

- Track-aware Spearman: `-0.180830`
- Trackless Spearman: `-0.020148`
- Track-aware holdout mean Spearman: `-0.172281`
- Track-aware holdout worst Spearman: `+0.248120`
- Recommended runtime evidence mode: `track_aware`
- Auto-apply enabled: `False`

### Claim Audit

- `garage_output_regressions`: **supported** — BMW/Sebring garage-output model is available for full rematerialized legality checks.
- `telemetry_extraction_proxies`: **partial** — 250 fallback signal resolutions and 170 missing signal resolutions were observed across validation metrics.
- `learned_corrections`: **supported** — Empirical and heave-calibration model files were found for BMW/Sebring.
- `predictor_directionality`: **unverified** — Directional predictor claims remain downgraded until the objective ranking and full predictor sanity metrics show stable negative correlation with lap time.
- `objective_ranking`: **unverified** — Current score-vs-lap correlation remains near zero, so objective rankings are not authoritative yet.

### Signal Usage

| Metric | Direct | Fallback | Missing |
|--------|--------|----------|---------|
| body_slip_p95_deg | 99 | 0 | 0 |
| braking_pitch_deg | 33 | 42 | 24 |
| front_excursion_mm | 33 | 42 | 24 |
| front_heave_travel_used_pct | 75 | 0 | 24 |
| front_lock_p95 | 33 | 40 | 26 |
| front_pressure_hot_kpa | 33 | 42 | 24 |
| rear_power_slip_p95 | 33 | 42 | 24 |
| rear_pressure_hot_kpa | 33 | 42 | 24 |
| rear_rh_std_mm | 99 | 0 | 0 |
| understeer_high_deg | 99 | 0 | 0 |
| understeer_low_deg | 99 | 0 | 0 |

### Top Raw Setup Correlations

| Field | Pearson r | Spearman r |
|-------|-----------|------------|
| front_ls_comp | -0.102827 | -0.428508 |
| rear_toe_mm | +0.075649 | -0.422680 |
| rear_master_cyl_mm | +0.203185 | -0.373614 |
| brake_bias_pct | +0.011448 | -0.362584 |
| front_master_cyl_mm | +0.248437 | -0.306390 |
| front_torsion_od_mm | +0.109023 | -0.283885 |
| fuel_l | -0.416697 | +0.256125 |
| fuel_low_warning_l | +0.092116 | -0.255564 |
| front_toe_mm | -0.143972 | +0.236527 |
| rear_hs_comp | +0.110202 | -0.226441 |
| diff_ramp_option_idx | +0.069049 | -0.225841 |
| rear_camber_deg | -0.071897 | +0.218344 |

### Model Freshness

| File | Exists | Modified (UTC) | Older Than Latest Observation (days) |
|------|--------|----------------|--------------------------------------|
| heave_calibration_bmw_sebring.json | True | 2026-03-28T08:15:42.436821+00:00 | 0.06 |
| bmw_sebring_empirical.json | True | 2026-03-28T09:40:39.045959+00:00 | 0.0 |
| bmw_global_empirical.json | True | 2026-03-28T09:40:39.045959+00:00 | 0.0 |