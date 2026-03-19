# Objective Weight Calibration Report

Generated from 63 BMW Sebring observations.

## Current Model Correlation

- Pearson r (score vs lap_time):  **+0.2162**
- Spearman ρ (score vs lap_time): **+0.2168**
- Expected: negative (higher score → lower lap time)

❌ Positive or zero correlation — model is miscalibrated.

## Lap Time Distribution

- Min: 108.829s
- Median: 109.350s
- Max: 110.492s
- Spread: 1.664s

## Score Distribution

- Min: -2205.3ms
- Median: -973.1ms
- Max: -459.4ms
- Spread: 1745.8ms

## Component-Level Correlation with Lap Time

| Component | Pearson r | Spearman ρ | Direction |
|-----------|-----------|------------|-----------|
| total_score | +0.2162 | +0.2168 | ⚠️ |
| platform_risk | -0.2271 | -0.2099 | ⚠️ |
| driver_mismatch | +0.0000 | -0.0364 | ⚠️ |
| telemetry_uncertainty | +0.0000 | -0.0364 | ⚠️ |
| envelope_penalty | -0.0097 | -0.0510 | ⚠️ |
| lltd | +0.0000 | -0.0364 | ⚠️ |
| lltd_error | +0.0000 | -0.0364 | ⚠️ |
| front_sigma_mm | +0.0000 | -0.0364 | ⚠️ |

*Penalties should correlate POSITIVELY with lap time (more penalty → slower).*
*Total score should correlate NEGATIVELY (higher score → faster).*

## Optimized Weights (Grid Search)

Best Spearman ρ achievable: **-0.0628**

| Weight | Current | Suggested |
|--------|---------|-----------|
| platform | 1.0 | 0.0 |
| driver | 0.5 | 0.0 |
| telemetry | 0.6 | 0.0 |
| envelope | 0.7 | 0.2 |

## Top 10 Best-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 1 | -459.4 | 109.350 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 2 | -618.7 | 109.094 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 3 | -625.8 | 110.492 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 4 | -646.6 | 109.100 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 5 | -660.8 | 109.290 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 6 | -665.7 | 109.381 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 7 | -686.9 | 109.720 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 8 | -693.5 | 109.372 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 9 | -693.5 | 109.834 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 10 | -714.5 | 109.279 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Bottom 10 Worst-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 54 | -1440.4 | 109.628 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 55 | -1445.7 | 109.504 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 56 | -1447.6 | 109.278 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 57 | -1463.0 | 109.361 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 58 | -1480.7 | 109.378 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 59 | -1481.8 | 109.274 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 60 | -1485.6 | 109.242 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 61 | -1490.7 | 109.222 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 62 | -1490.7 | 109.168 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 63 | -2205.3 | 109.114 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Anomalies (Model vs Reality Disagreements)

| Score Rank | Time Rank | Δ | Score (ms) | Lap (s) | File |
|-----------|-----------|---|-----------|---------|------|
| 3 | 63 | 60 | -625.8 | 110.492 | bmw_sebring_international_raceway_bmwlmd |
| 63 | 9 | 54 | -2205.3 | 109.114 | bmw_sebring_international_raceway_bmwlmd |
| 9 | 59 | 50 | -693.5 | 109.834 | bmw_sebring_international_raceway_bmwlmd |
| 62 | 14 | 48 | -1490.7 | 109.168 | bmw_sebring_international_raceway_bmwlmd |
| 7 | 55 | 48 | -686.9 | 109.720 | bmw_sebring_international_raceway_bmwlmd |
| 61 | 18 | 43 | -1490.7 | 109.222 | bmw_sebring_international_raceway_bmwlmd |
| 46 | 3 | 43 | -1273.8 | 109.013 | bmw_sebring_international_raceway_bmw20. |
| 53 | 11 | 42 | -1435.9 | 109.118 | bmw_sebring_international_raceway_bmw170 |
| 22 | 62 | 40 | -949.8 | 110.013 | bmw_sebring_international_raceway_bmwlmd |
| 21 | 61 | 40 | -949.8 | 110.013 | bmw_sebring_international_raceway_bmw_se |

*Large Δ = model disagrees with reality. Investigate these setups.*