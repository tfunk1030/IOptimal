## Objective Validation — 2026-03-25

### Workflow

`IBT -> track/analyzer -> diagnosis/driver/style -> solve_chain/legality -> report/.sto -> webapp`

### Support Tiers

| Car | Track | Samples | Confidence |
|-----|-------|---------|------------|
| bmw | Sebring International Raceway (International) | 73 | calibrated |
| cadillac | Silverstone Circuit (Arena Grand Prix) | 4 | exploratory |
| ferrari | Sebring International Raceway (International) | 9 | partial |
| porsche | Sebring International Raceway (International) | 2 | unsupported |

### BMW/Sebring Evidence

- Samples: `73` total, `72` non-vetoed
- Veto rate: `0.014`
- Score correlation (all valid): Pearson `+0.037458`, Spearman `-0.100611`
- Score correlation (non-vetoed): Pearson `+0.034870`, Spearman `-0.120522`

### Recalibration Snapshot

- Track-aware Spearman: `-0.120522`
- Trackless Spearman: `-0.064976`
- Track-aware holdout mean Spearman: `-0.072143`
- Track-aware holdout worst Spearman: `+0.428571`
- Recommended runtime evidence mode: `track_aware`
- Auto-apply enabled: `False`

### Claim Audit

- `garage_output_regressions`: **supported** — BMW/Sebring garage-output model is available for full rematerialized legality checks.
- `telemetry_extraction_proxies`: **partial** — 292 fallback signal resolutions and 170 missing signal resolutions were observed across validation metrics.
- `learned_corrections`: **supported** — Empirical and heave-calibration model files were found for BMW/Sebring.
- `predictor_directionality`: **unverified** — Directional predictor claims remain downgraded until the objective ranking and full predictor sanity metrics show stable negative correlation with lap time.
- `objective_ranking`: **unverified** — Current score-vs-lap correlation remains near zero, so objective rankings are not authoritative yet.

### Signal Usage

| Metric | Direct | Fallback | Missing |
|--------|--------|----------|---------|
| body_slip_p95_deg | 73 | 0 | 0 |
| braking_pitch_deg | 0 | 49 | 24 |
| front_excursion_mm | 0 | 49 | 24 |
| front_heave_travel_used_pct | 49 | 0 | 24 |
| front_lock_p95 | 0 | 47 | 26 |
| front_pressure_hot_kpa | 0 | 49 | 24 |
| rear_power_slip_p95 | 0 | 49 | 24 |
| rear_pressure_hot_kpa | 0 | 49 | 24 |
| rear_rh_std_mm | 73 | 0 | 0 |
| understeer_high_deg | 73 | 0 | 0 |
| understeer_low_deg | 73 | 0 | 0 |

### Top Raw Setup Correlations

| Field | Pearson r | Spearman r |
|-------|-----------|------------|
| front_ls_comp | -0.418706 | -0.472860 |
| rear_master_cyl_mm | -0.648826 | -0.459804 |
| rear_toe_mm | -0.473268 | -0.363753 |
| fuel_low_warning_l | -0.400393 | -0.357290 |
| brake_bias_pct | -0.340026 | -0.355811 |
| rear_hs_comp | -0.291575 | -0.343720 |
| front_hs_comp | -0.252394 | -0.314811 |
| front_torsion_od_mm | -0.325912 | -0.313332 |
| front_master_cyl_mm | -0.710257 | -0.300148 |
| fuel_l | +0.404621 | +0.293363 |
| front_toe_mm | +0.460510 | +0.262428 |
| rear_camber_deg | +0.231176 | +0.251978 |

### Model Freshness

| File | Exists | Modified (UTC) | Older Than Latest Observation (days) |
|------|--------|----------------|--------------------------------------|
| heave_calibration_bmw_sebring.json | True | 2026-03-22T20:36:48.244831+00:00 | 2.18 |
| bmw_sebring_empirical.json | True | 2026-03-22T23:32:06.019515+00:00 | 2.05 |
| bmw_global_empirical.json | True | 2026-03-22T23:32:06.019515+00:00 | 2.05 |