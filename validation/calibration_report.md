# Objective Recalibration Report

Generated: 2026-03-25T23:57:24.311376+00:00

BMW/Sebring samples: `73`

## Track Aware

- Samples: `73` total, `72` non-vetoed
- Pearson: `+0.034870`
- Spearman: `-0.120522`

### Term Correlations

| Term | Pearson r | Spearman r |
|------|-----------|------------|
| lap_gain_ms | -0.090503 | -0.290437 |
| weighted_lap_gain_ms | -0.090503 | -0.290437 |
| weighted_platform_ms | +0.042996 | -0.120747 |
| total_score_ms | +0.034870 | -0.120522 |
| platform_risk_ms | -0.042996 | +0.098592 |
| driver_mismatch_ms | +nan | -0.088398 |
| telemetry_uncertainty_ms | +nan | -0.088398 |
| staleness_penalty_ms | +nan | -0.088398 |
| empirical_penalty_ms | +nan | -0.088398 |
| weighted_driver_ms | +nan | -0.088398 |
| weighted_uncertainty_ms | +nan | -0.088398 |
| weighted_staleness_ms | +nan | -0.088398 |

### Lap-Gain Components

| Component | Pearson r | Spearman r |
|-----------|-----------|------------|
| rebound_ratio_ms | +0.276961 | +0.332787 |
| damping_ms | +0.269508 | +0.192392 |
| arb_extreme_ms | -0.292777 | -0.151843 |
| camber_ms | +0.066690 | +0.118110 |
| tc_ms | +nan | -0.088398 |
| lltd_balance_ms | +0.037094 | +0.077272 |
| diff_ramp_ms | -0.176625 | -0.072802 |
| df_balance_ms | +0.053097 | +0.053733 |
| diff_clutch_ms | -0.023300 | -0.026047 |
| diff_preload_ms | +0.042728 | -0.013023 |

### Lap-Gain Component Ablations

| Component Removed | Spearman r | Holdout Mean | Holdout Worst | In-Sample Improvement | Holdout Mean Improvement |
|-------------------|------------|--------------|---------------|-----------------------|--------------------------|
| arb_extreme_ms | -0.121487 | -0.072143 | +0.428571 | +0.000965 | +0.000000 |
| tc_ms | -0.120522 | -0.072143 | +0.428571 | +0.000000 | +0.000000 |
| rebound_ratio_ms | -0.119879 | -0.072143 | +0.428571 | -0.000643 | +0.000000 |
| diff_ramp_ms | -0.119397 | -0.070714 | +0.428571 | -0.001125 | -0.001429 |
| camber_ms | -0.120426 | -0.069286 | +0.428571 | -0.000096 | -0.002857 |
| df_balance_ms | -0.119075 | -0.066429 | +0.428571 | -0.001447 | -0.005714 |
| damping_ms | -0.119236 | -0.065989 | +0.459341 | -0.001286 | -0.006154 |
| diff_preload_ms | -0.113994 | -0.060275 | +0.459341 | -0.006528 | -0.011868 |
| diff_clutch_ms | -0.111004 | -0.054560 | +0.428571 | -0.009518 | -0.017582 |
| lltd_balance_ms | -0.110489 | -0.054560 | +0.428571 | -0.010033 | -0.017582 |

### Holdout Validation

- Folds: `5`
- Current runtime mean test Spearman: `-0.072143`
- Current runtime worst test Spearman: `+0.428571`
- Train-searched mean test Spearman: `-0.091099`
- Train-searched worst test Spearman: `+0.265934`

### Ablations

| Variant | Pearson r | Spearman r |
|---------|-----------|------------|
| lap_gain_only | -0.090503 | -0.290437 |
| drop_lap_gain | +0.036078 | -0.134478 |
| penalties_only | +0.030427 | -0.131841 |
| current | +0.034870 | -0.120522 |
| drop_driver | +0.034870 | -0.120522 |
| drop_uncertainty | +0.034870 | -0.120522 |
| drop_staleness | +0.034870 | -0.120522 |
| drop_empirical | +0.034870 | -0.120522 |
| drop_envelope | +0.041836 | -0.104058 |
| drop_platform | -0.078282 | -0.074410 |

### Weight Search

- Current Spearman: `-0.120522`
- Best Spearman found: `-0.295357`
- Improvement: `+0.174834`
- Manual review recommended: `True`

| Weight | Suggested |
|--------|-----------|
| lap_gain | 1.25 |
| platform | 0.00 |
| driver | 0.00 |
| uncertainty | 0.00 |
| envelope | 0.20 |
| staleness | 0.00 |
| empirical | 0.00 |

## Trackless

- Samples: `73` total, `73` non-vetoed
- Pearson: `-0.057583`
- Spearman: `-0.064976`

### Term Correlations

| Term | Pearson r | Spearman r |
|------|-----------|------------|
| lap_gain_ms | -0.084623 | -0.227601 |
| weighted_lap_gain_ms | -0.084623 | -0.227601 |
| platform_risk_ms | -0.291774 | -0.168826 |
| driver_mismatch_ms | +nan | -0.106041 |
| telemetry_uncertainty_ms | +nan | -0.106041 |
| staleness_penalty_ms | +nan | -0.106041 |
| empirical_penalty_ms | +nan | -0.106041 |
| weighted_driver_ms | +nan | -0.106041 |
| weighted_uncertainty_ms | +nan | -0.106041 |
| weighted_staleness_ms | +nan | -0.106041 |
| weighted_empirical_ms | +nan | -0.106041 |
| envelope_penalty_ms | +0.067975 | -0.077410 |

### Lap-Gain Components

| Component | Pearson r | Spearman r |
|-----------|-----------|------------|
| rebound_ratio_ms | +0.271019 | +0.346076 |
| damping_ms | +0.256107 | +0.204060 |
| arb_extreme_ms | -0.291774 | -0.168826 |
| camber_ms | +0.071464 | +0.134271 |
| tc_ms | +nan | -0.106041 |
| diff_ramp_ms | -0.175367 | -0.090090 |
| lltd_balance_ms | +0.033908 | +0.057448 |
| diff_clutch_ms | -0.024190 | -0.045631 |
| df_balance_ms | +0.044584 | +0.037671 |
| diff_preload_ms | +0.056325 | +0.007898 |

### Lap-Gain Component Ablations

| Component Removed | Spearman r | Holdout Mean | Holdout Worst | In-Sample Improvement | Holdout Mean Improvement |
|-------------------|------------|--------------|---------------|-----------------------|--------------------------|
| diff_ramp_ms | -0.083642 | -0.101593 | +0.121429 | +0.018666 | +0.010385 |
| arb_extreme_ms | -0.075003 | -0.098242 | +0.121429 | +0.010027 | +0.007033 |
| tc_ms | -0.064976 | -0.091209 | +0.121429 | +0.000000 | +0.000000 |
| camber_ms | -0.037671 | -0.076429 | +0.250000 | -0.027305 | -0.014780 |
| diff_clutch_ms | -0.047544 | -0.070495 | +0.217857 | -0.017432 | -0.020714 |
| df_balance_ms | -0.093145 | -0.063022 | +0.239286 | +0.028169 | -0.028187 |
| damping_ms | -0.034709 | -0.054176 | +0.217857 | -0.030267 | -0.037033 |
| diff_preload_ms | -0.045014 | -0.050110 | +0.178571 | -0.019962 | -0.041099 |
| rebound_ratio_ms | +0.006602 | -0.029286 | +0.232143 | -0.071578 | -0.061923 |
| lltd_balance_ms | -0.011971 | -0.001209 | +0.267857 | -0.053005 | -0.090000 |

### Holdout Validation

- Folds: `5`
- Current runtime mean test Spearman: `-0.091209`
- Current runtime worst test Spearman: `+0.121429`
- Train-searched mean test Spearman: `-0.259890`
- Train-searched worst test Spearman: `+0.072527`

### Ablations

| Variant | Pearson r | Spearman r |
|---------|-----------|------------|
| lap_gain_only | -0.084623 | -0.227601 |
| drop_envelope | -0.004177 | -0.214272 |
| drop_platform | -0.088460 | -0.075003 |
| current | -0.057583 | -0.064976 |
| drop_driver | -0.057583 | -0.064976 |
| drop_uncertainty | -0.057583 | -0.064976 |
| drop_staleness | -0.057583 | -0.064976 |
| drop_empirical | -0.057583 | -0.064976 |
| penalties_only | -0.031938 | +0.165803 |
| drop_lap_gain | -0.031938 | +0.165803 |

### Weight Search

- Current Spearman: `-0.064976`
- Best Spearman found: `-0.227601`
- Improvement: `+0.162625`
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
