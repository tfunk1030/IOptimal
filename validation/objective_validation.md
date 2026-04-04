## Objective Validation — 2026-04-04

### Workflow

`IBT -> track/analyzer -> diagnosis/driver/style -> solve_chain/legality -> report/.sto -> webapp`

### Support Tiers

| Car | Track | Samples | Confidence |
|-----|-------|---------|------------|
| acura | Hockenheimring Baden-Württemberg (Grand Prix) | 16 | unsupported |
| bmw | Sebring International Raceway (International) | 83 | calibrated |
| cadillac | Silverstone Circuit (Arena Grand Prix) | 4 | exploratory |
| ferrari | Sebring International Raceway (International) | 2 | partial |
| porsche | Sebring International Raceway (International) | 2 | unsupported |

### BMW/Sebring Evidence

- Samples: `83` total, `82` non-vetoed
- Veto rate: `0.012`
- Score correlation (all valid): Pearson `+0.034124`, Spearman `-0.333319`
- Score correlation (non-vetoed): Pearson `-0.253512`, Spearman `-0.352619`

### Recalibration Snapshot

- Track-aware Spearman: `-0.352619`
- Trackless Spearman: `-0.336573`
- Track-aware holdout mean Spearman: `-0.350392`
- Track-aware holdout worst Spearman: `-0.181373`
- Recommended runtime evidence mode: `track_aware`
- Auto-apply enabled: `False`

### Claim Audit

- `garage_output_regressions`: **supported** — BMW/Sebring garage-output model is available for full rematerialized legality checks.
- `telemetry_extraction_proxies`: **partial** — 228 fallback signal resolutions and 144 missing signal resolutions were observed across validation metrics.
- `learned_corrections`: **supported** — Empirical and heave-calibration model files were found for BMW/Sebring.
- `predictor_directionality`: **unverified** — Directional predictor claims remain downgraded until the objective ranking and full predictor sanity metrics show stable negative correlation with lap time.
- `objective_ranking`: **unverified** — Current score-vs-lap correlation remains near zero, so objective rankings are not authoritative yet.

### Signal Usage

| Metric | Direct | Fallback | Missing |
|--------|--------|----------|---------|
| body_slip_p95_deg | 83 | 0 | 0 |
| braking_pitch_deg | 25 | 34 | 24 |
| front_excursion_mm | 25 | 58 | 0 |
| front_heave_travel_used_pct | 59 | 0 | 24 |
| front_lock_p95 | 25 | 34 | 24 |
| front_pressure_hot_kpa | 25 | 34 | 24 |
| rear_power_slip_p95 | 25 | 34 | 24 |
| rear_pressure_hot_kpa | 25 | 34 | 24 |
| rear_rh_std_mm | 83 | 0 | 0 |
| understeer_high_deg | 83 | 0 | 0 |
| understeer_low_deg | 83 | 0 | 0 |

### Top Raw Setup Correlations

| Field | Pearson r | Spearman r |
|-------|-----------|------------|
| front_ls_comp | -0.057742 | -0.413459 |
| rear_toe_mm | +0.132830 | -0.400290 |
| rear_master_cyl_mm | +0.244145 | -0.396458 |
| front_ls_rbd | +0.138843 | -0.380895 |
| brake_bias_pct | +0.044466 | -0.372580 |
| front_master_cyl_mm | +0.293470 | -0.357451 |
| front_hs_rbd | -0.028431 | -0.342084 |
| rear_hs_rbd | -0.046828 | -0.333856 |
| fuel_low_warning_l | +0.119852 | -0.315854 |
| front_torsion_od_mm | +0.160574 | -0.264266 |
| front_hs_slope | -0.056044 | -0.262546 |
| diff_preload_nm | -0.011473 | -0.252272 |

### Model Freshness

| File | Exists | Modified (UTC) | Older Than Latest Observation (days) |
|------|--------|----------------|--------------------------------------|
| heave_calibration_bmw_sebring.json | True | 2026-04-02T04:21:47.297786+00:00 | 0.0 |
| bmw_sebring_empirical.json | True | 2026-04-02T04:21:47.299786+00:00 | 0.0 |
| bmw_global_empirical.json | True | 2026-04-02T04:21:47.299786+00:00 | 0.0 |