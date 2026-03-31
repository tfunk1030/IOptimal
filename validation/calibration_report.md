# Objective Recalibration Report

Generated: 2026-03-28T08:05:57.159582+00:00

BMW/Sebring samples: `75`

## Track Aware

- Samples: `75` total, `74` non-vetoed
- Pearson: `-0.095656`
- Spearman: `-0.059845`

### Term Correlations

| Term | Pearson r | Spearman r |
|------|-----------|------------|
| lap_gain_ms | -0.132466 | -0.205450 |
| weighted_lap_gain_ms | -0.132466 | -0.205450 |
| platform_risk_ms | -0.045909 | +0.107619 |
| total_score_ms | -0.095656 | -0.059845 |
| weighted_envelope_ms | -0.073587 | +0.051107 |
| driver_mismatch_ms | +nan | -0.037453 |
| telemetry_uncertainty_ms | +nan | -0.037453 |
| staleness_penalty_ms | +nan | -0.037453 |
| empirical_penalty_ms | +nan | -0.037453 |
| weighted_platform_ms | +nan | -0.037453 |
| weighted_driver_ms | +nan | -0.037453 |
| weighted_uncertainty_ms | +nan | -0.037453 |

### Lap-Gain Components

| Component | Pearson r | Spearman r |
|-----------|-----------|------------|
| damping_ms | +0.278548 | +0.245879 |
| camber_ms | +0.080775 | +0.124798 |
| diff_ramp_ms | -0.231746 | -0.123377 |
| lltd_balance_ms | +0.021833 | +0.090529 |
| df_balance_ms | +0.058692 | +0.065176 |
| rebound_ratio_ms | +0.138420 | -0.046072 |
| arb_extreme_ms | +nan | -0.037453 |
| tc_ms | +nan | -0.037453 |
| diff_clutch_ms | -0.028302 | +0.018141 |
| diff_preload_ms | +0.029714 | +0.002917 |

### Lap-Gain Component Ablations

| Component Removed | Spearman r | Holdout Mean | Holdout Worst | In-Sample Improvement | Holdout Mean Improvement |
|-------------------|------------|--------------|---------------|-----------------------|--------------------------|
| diff_preload_ms | -0.068849 | -0.096703 | +0.135714 | +0.009004 | +0.016484 |
| df_balance_ms | -0.080726 | -0.093571 | +0.121429 | +0.020881 | +0.013352 |
| diff_ramp_ms | -0.069441 | -0.088791 | +0.153571 | +0.009596 | +0.008571 |
| arb_extreme_ms | -0.059845 | -0.080220 | +0.121429 | +0.000000 | +0.000000 |
| tc_ms | -0.059845 | -0.080220 | +0.121429 | +0.000000 | +0.000000 |
| damping_ms | -0.058156 | -0.076923 | +0.132143 | -0.001688 | -0.003297 |
| diff_clutch_ms | -0.047227 | -0.071648 | +0.121429 | -0.012618 | -0.008571 |
| lltd_balance_ms | -0.015772 | -0.052363 | +0.167857 | -0.044073 | -0.027857 |
| camber_ms | -0.035883 | -0.049341 | +0.150000 | -0.023961 | -0.030879 |
| rebound_ratio_ms | -0.003184 | -0.002912 | +0.257143 | -0.056660 | -0.077308 |

### Holdout Validation

- Folds: `5`
- Current runtime mean test Spearman: `-0.080220`
- Current runtime worst test Spearman: `+0.121429`
- Train-searched mean test Spearman: `+0.010385`
- Train-searched worst test Spearman: `+0.207143`

### Ablations

| Variant | Pearson r | Spearman r |
|---------|-----------|------------|
| lap_gain_only | -0.132466 | -0.205450 |
| drop_envelope | -0.132466 | -0.205450 |
| penalties_only | +0.032697 | -0.139726 |
| current | -0.095656 | -0.059845 |
| drop_platform | -0.095656 | -0.059845 |
| drop_driver | -0.095656 | -0.059845 |
| drop_uncertainty | -0.095656 | -0.059845 |
| drop_staleness | -0.095656 | -0.059845 |
| drop_empirical | -0.095656 | -0.059845 |
| drop_lap_gain | -0.073587 | +0.051107 |

### Weight Search

- Current Spearman: `-0.059845`
- Best Spearman found: `-0.205450`
- Improvement: `+0.145605`
- Manual review recommended: `True`

| Weight | Suggested |
|--------|-----------|
| lap_gain | 0.25 |
| platform | 0.00 |
| driver | 0.00 |
| uncertainty | 0.00 |
| envelope | 0.00 |
| staleness | 0.00 |
| empirical | 0.00 |

## Trackless

- Samples: `75` total, `75` non-vetoed
- Pearson: `-0.120100`
- Spearman: `-0.134794`

### Term Correlations

| Term | Pearson r | Spearman r |
|------|-----------|------------|
| lap_gain_ms | -0.111807 | -0.165861 |
| weighted_lap_gain_ms | -0.111807 | -0.165861 |
| total_score_ms | -0.120100 | -0.134794 |
| platform_risk_ms | -0.283227 | -0.115050 |
| weighted_envelope_ms | -0.071789 | +0.089246 |
| envelope_penalty_ms | +0.071789 | -0.054595 |
| driver_mismatch_ms | +nan | -0.054026 |
| telemetry_uncertainty_ms | +nan | -0.054026 |
| staleness_penalty_ms | +nan | -0.054026 |
| empirical_penalty_ms | +nan | -0.054026 |
| weighted_platform_ms | +nan | -0.054026 |
| weighted_driver_ms | +nan | -0.054026 |

### Lap-Gain Components

| Component | Pearson r | Spearman r |
|-----------|-----------|------------|
| damping_ms | +0.249259 | +0.200398 |
| camber_ms | +0.084814 | +0.139033 |
| diff_ramp_ms | -0.230314 | -0.138890 |
| lltd_balance_ms | +0.019061 | +0.072518 |
| arb_extreme_ms | +nan | -0.054026 |
| tc_ms | +nan | -0.054026 |
| df_balance_ms | +0.050742 | +0.050697 |
| rebound_ratio_ms | +0.142022 | -0.027681 |
| diff_preload_ms | +0.043555 | +0.021422 |
| diff_clutch_ms | -0.029082 | -0.000171 |

### Lap-Gain Component Ablations

| Component Removed | Spearman r | Holdout Mean | Holdout Worst | In-Sample Improvement | Holdout Mean Improvement |
|-------------------|------------|--------------|---------------|-----------------------|--------------------------|
| df_balance_ms | -0.225149 | -0.217857 | -0.021429 | +0.090356 | +0.087143 |
| diff_ramp_ms | -0.168592 | -0.169286 | +0.121429 | +0.033798 | +0.038571 |
| arb_extreme_ms | -0.134794 | -0.130714 | +0.171429 | +0.000000 | +0.000000 |
| tc_ms | -0.134794 | -0.130714 | +0.171429 | +0.000000 | +0.000000 |
| damping_ms | -0.117098 | -0.125714 | +0.139286 | -0.017696 | -0.005000 |
| diff_clutch_ms | -0.108364 | -0.117143 | +0.232143 | -0.026430 | -0.013571 |
| rebound_ratio_ms | -0.108620 | -0.112143 | +0.314286 | -0.026174 | -0.018571 |
| diff_preload_ms | -0.117183 | -0.099286 | +0.182143 | -0.017610 | -0.031429 |
| camber_ms | -0.099744 | -0.074286 | +0.189286 | -0.035050 | -0.056429 |
| lltd_balance_ms | -0.020284 | -0.022857 | +0.228571 | -0.114509 | -0.107857 |

### Holdout Validation

- Folds: `5`
- Current runtime mean test Spearman: `-0.130714`
- Current runtime worst test Spearman: `+0.171429`
- Train-searched mean test Spearman: `-0.177143`
- Train-searched worst test Spearman: `+0.157143`

### Ablations

| Variant | Pearson r | Spearman r |
|---------|-----------|------------|
| lap_gain_only | -0.111807 | -0.165861 |
| drop_envelope | -0.111807 | -0.165861 |
| current | -0.120100 | -0.134794 |
| drop_platform | -0.120100 | -0.134794 |
| drop_driver | -0.120100 | -0.134794 |
| drop_uncertainty | -0.120100 | -0.134794 |
| drop_staleness | -0.120100 | -0.134794 |
| drop_empirical | -0.120100 | -0.134794 |
| drop_lap_gain | -0.071789 | +0.089246 |
| penalties_only | -0.036809 | +0.183471 |

### Weight Search

- Current Spearman: `-0.134794`
- Best Spearman found: `-0.165861`
- Improvement: `+0.031067`
- Manual review recommended: `True`

| Weight | Suggested |
|--------|-----------|
| lap_gain | 0.25 |
| platform | 0.00 |
| driver | 0.00 |
| uncertainty | 0.00 |
| envelope | 0.00 |
| staleness | 0.00 |
| empirical | 0.00 |

## Runtime Recommendation

- Preferred evidence mode today: `track_aware`
- Auto-apply: `False`
- Manual review required: `True`
- Reason: Calibration tooling is implemented, but runtime auto-application stays disabled until track-aware correlation is materially negative and stable under stronger validation.
