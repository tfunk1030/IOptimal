# Objective Weight Calibration Report

Generated from 63 BMW Sebring observations.

## Current Model Correlation

- Pearson r (score vs lap_time):  **+0.1127**
- Spearman ρ (score vs lap_time): **+0.0022**
- Expected: negative (higher score → lower lap time)

❌ Positive or zero correlation — model is miscalibrated.

## Lap Time Distribution

- Min: 108.829s
- Median: 109.350s
- Max: 110.492s
- Spread: 1.664s

## Score Distribution

- Min: -2238.7ms
- Median: -1008.5ms
- Max: -576.4ms
- Spread: 1662.4ms

## Component-Level Correlation with Lap Time

| Component | Pearson r | Spearman ρ | Direction |
|-----------|-----------|------------|-----------|
| total_score | +0.1127 | +0.0022 | ⚠️ |
| platform_risk | -0.1129 | +0.0050 | ✅ |
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
| 1 | -576.4 | 109.350 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 2 | -678.0 | 109.340 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 3 | -688.5 | 109.279 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 4 | -711.4 | 109.094 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 5 | -725.0 | 109.605 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 6 | -734.4 | 109.100 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 7 | -745.2 | 110.492 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 8 | -749.5 | 109.290 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 9 | -752.8 | 109.390 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 10 | -754.4 | 109.381 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Bottom 10 Worst-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 54 | -1130.4 | 109.168 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 55 | -1141.5 | 109.278 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 56 | -1142.9 | 109.504 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 57 | -1163.6 | 109.361 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 58 | -1175.6 | 109.242 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 59 | -1181.2 | 109.378 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 60 | -1182.4 | 109.274 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 61 | -1219.9 | 109.628 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 62 | -1306.9 | 109.820 | bmw_sebring_international_raceway_bmwbad.json |
| 63 | -2238.7 | 109.114 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Anomalies (Model vs Reality Disagreements)

| Score Rank | Time Rank | Δ | Score (ms) | Lap (s) | File |
|-----------|-----------|---|-----------|---------|------|
| 7 | 63 | 56 | -745.2 | 110.492 | bmw_sebring_international_raceway_bmwlmd |
| 63 | 9 | 54 | -2238.7 | 109.114 | bmw_sebring_international_raceway_bmwlmd |
| 14 | 59 | 45 | -780.8 | 109.834 | bmw_sebring_international_raceway_bmwlmd |
| 5 | 49 | 44 | -725.0 | 109.605 | bmw_sebring_international_raceway_bmwlmd |
| 12 | 55 | 43 | -780.0 | 109.720 | bmw_sebring_international_raceway_bmwlmd |
| 16 | 57 | 41 | -895.6 | 109.734 | bmw_sebring_international_raceway_bmwlmd |
| 54 | 14 | 40 | -1130.4 | 109.168 | bmw_sebring_international_raceway_bmwlmd |
| 58 | 21 | 37 | -1175.6 | 109.242 | bmw_sebring_international_raceway_bmwlmd |
| 60 | 25 | 35 | -1182.4 | 109.274 | bmw_sebring_international_raceway_bmwlmd |
| 53 | 18 | 35 | -1130.4 | 109.222 | bmw_sebring_international_raceway_bmwlmd |

*Large Δ = model disagrees with reality. Investigate these setups.*