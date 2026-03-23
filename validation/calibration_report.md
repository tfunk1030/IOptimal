# Objective Weight Calibration Report

Generated from 68 BMW Sebring observations.

## Current Model Correlation

- Pearson r (score vs lap_time):  **-0.1426**
- Spearman ρ (score vs lap_time): **-0.2697**
- Expected: negative (higher score → lower lap time)

⚠️ Weak negative correlation — weights need tuning.

## Lap Time Distribution

- Min: 108.355s
- Median: 109.340s
- Max: 110.492s
- Spread: 2.137s

## Score Distribution

- Min: -423.3ms
- Median: -110.3ms
- Max: -95.2ms
- Spread: 328.2ms

## Component-Level Correlation with Lap Time

| Component | Pearson r | Spearman ρ | Direction |
|-----------|-----------|------------|-----------|
| total_score | -0.1426 | -0.2697 | ✅ |
| platform_risk | +0.0000 | -0.0824 | ⚠️ |
| driver_mismatch | +0.0000 | -0.0824 | ⚠️ |
| telemetry_uncertainty | +0.0000 | -0.0824 | ⚠️ |
| envelope_penalty | +0.0639 | +0.0038 | ✅ |
| lltd | +0.0000 | -0.0824 | ⚠️ |
| lltd_error | +0.0000 | -0.0824 | ⚠️ |
| front_sigma_mm | +0.0000 | -0.0824 | ⚠️ |

*Penalties should correlate POSITIVELY with lap time (more penalty → slower).*
*Total score should correlate NEGATIVELY (higher score → faster).*

## Optimized Weights (Grid Search)

Best Spearman ρ achievable: **-0.1052**

| Weight | Current | Suggested |
|--------|---------|-----------|
| platform | 1.0 | 0.0 |
| driver | 0.5 | 0.0 |
| telemetry | 0.6 | 0.0 |
| envelope | 0.7 | 0.2 |

## Top 10 Best-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 1 | -95.2 | 108.355 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 2 | -95.2 | 108.473 | bmw_sebring_international_raceway_bmwlmdh_sebring% |
| 3 | -95.9 | 109.628 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 4 | -96.2 | 109.117 | bmw_sebring_international_raceway_bmw151.json |
| 5 | -96.2 | 109.233 | bmw_sebring_international_raceway_bmwaiedit.json |
| 6 | -98.3 | 109.040 | bmw_sebring_international_raceway_bmw2.json |
| 7 | -100.2 | 109.222 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 8 | -100.2 | 109.168 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 9 | -101.3 | 109.013 | bmw_sebring_international_raceway_bmw20.json |
| 10 | -102.8 | 109.118 | bmw_sebring_international_raceway_bmw170.json |

## Bottom 10 Worst-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 59 | -115.7 | 109.714 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 60 | -116.3 | 109.378 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 61 | -116.7 | 109.361 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 62 | -117.5 | 109.720 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 63 | -117.9 | 109.274 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 64 | -119.2 | 109.094 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 65 | -119.3 | 109.504 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 66 | -120.0 | 109.114 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 67 | -125.5 | 109.820 | bmw_sebring_international_raceway_bmwbad.json |
| 68 | -423.3 | 109.614 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Anomalies (Model vs Reality Disagreements)

| Score Rank | Time Rank | Δ | Score (ms) | Lap (s) | File |
|-----------|-----------|---|-----------|---------|------|
| 13 | 68 | 55 | -104.9 | 110.492 | bmw_sebring_international_raceway_bmwlmd |
| 64 | 10 | 54 | -119.2 | 109.094 | bmw_sebring_international_raceway_bmwlmd |
| 3 | 56 | 53 | -95.9 | 109.628 | bmw_sebring_international_raceway_bmwlmd |
| 66 | 14 | 52 | -120.0 | 109.114 | bmw_sebring_international_raceway_bmwlmd |
| 23 | 65 | 42 | -107.3 | 109.927 | bmw_sebring_international_raceway_bmwlmd |
| 14 | 54 | 40 | -105.3 | 109.605 | bmw_sebring_international_raceway_bmwlmd |
| 41 | 3 | 38 | -113.1 | 108.573 | bmw_sebring_international_raceway_bmwlmd |
| 49 | 13 | 36 | -113.5 | 109.100 | bmw_sebring_international_raceway_bmwlmd |
| 21 | 57 | 36 | -107.2 | 109.655 | bmw_sebring_international_raceway_bmwlmd |
| 52 | 18 | 34 | -113.9 | 109.131 | bmw_sebring_international_raceway_bmwlmd |

*Large Δ = model disagrees with reality. Investigate these setups.*

## Setup Parameter Correlations with Lap Time (Sebring)

Direct correlation of raw setup values vs observed lap time (n=68 sessions).
Negative ρ → higher value = faster lap. Positive ρ → higher value = slower.

| Parameter | Spearman ρ | Direction | Signal |
|-----------|-----------|-----------|--------|
| front_ls_comp | -0.421 | faster ↑ | 🟢 strong |
| front_hs_comp | -0.350 | faster ↑ | 🟢 strong |
| rear_hs_comp | -0.285 | faster ↑ | 🟡 moderate |
| front_torsion_od_mm | -0.285 | faster ↑ | 🟡 moderate |
| brake_bias_pct | -0.273 | faster ↑ | 🟡 moderate |
| rear_camber_deg | +0.229 | slower ↑ | 🟡 moderate |
| rear_third_spring_nmm | +0.222 | slower ↑ | 🟡 moderate |
| rear_spring_rate_nmm | -0.202 | faster ↑ | 🟡 moderate |
| front_ls_rbd | -0.132 | faster ↑ | ⚫ noise |
| front_heave_spring_nmm | +0.131 | slower ↑ | ⚫ noise |
| rear_arb_blade | +0.129 | slower ↑ | ⚫ noise |
| front_hs_rbd | +0.118 | slower ↑ | ⚫ noise |
| front_arb_blade | -0.083 | weak | ⚫ noise |
| rear_ls_rbd | +0.053 | weak | ⚫ noise |
| front_camber_deg | +0.043 | weak | ⚫ noise |
| rear_ls_comp | -0.035 | weak | ⚫ noise |
| rear_hs_rbd | -0.010 | weak | ⚫ noise |

## Telemetry Correlations with Lap Time (Sebring)

IBT-measured telemetry vs observed lap time. Shows what physical states predict pace.

| Telemetry Field | Spearman ρ | Direction | Signal |
|----------------|-----------|-----------|--------|
| body_roll_p95_deg | -0.356 | faster ↑ | 🟢 strong |
| roll_gradient_deg_per_g | +0.348 | slower ↑ | 🟢 strong |
| rear_bottoming_events | +0.328 | slower ↑ | 🟢 strong |
| lltd_measured | -0.286 | faster ↑ | 🟡 moderate |
| body_roll_max_deg | +0.281 | slower ↑ | 🟡 moderate |
| rear_rh_std_mm | +0.238 | slower ↑ | 🟡 moderate |
| dynamic_front_rh_mm | +0.217 | slower ↑ | 🟡 moderate |
| front_dominant_freq_hz | -0.167 | faster ↑ | 🟡 moderate |
| dynamic_rear_rh_mm | +0.131 | slower ↑ | ⚫ noise |
| rear_shock_vel_p99_mps | +0.109 | slower ↑ | ⚫ noise |
| front_shock_vel_p95_mps | -0.101 | faster ↑ | ⚫ noise |
| body_slip_p95_deg | +0.098 | weak | ⚫ noise |
| front_shock_vel_p99_mps | -0.093 | weak | ⚫ noise |
| front_rh_std_mm | +0.091 | weak | ⚫ noise |
| front_rh_settle_time_ms | -0.064 | weak | ⚫ noise |
| peak_lat_g | -0.037 | weak | ⚫ noise |
| rear_dominant_freq_hz | +0.033 | weak | ⚫ noise |
| understeer_low_speed_deg | -0.023 | weak | ⚫ noise |
| understeer_high_speed_deg | +0.022 | weak | ⚫ noise |
| understeer_mean_deg | +0.020 | weak | ⚫ noise |

## Sebring Setup Recommendations (Data-Driven)

Based on 68 real BMW sessions at Sebring:

| Finding | Setup Direction | Strength |
|---------|----------------|---------|
| Front LS compression (clicks) | Higher = faster (ρ=-0.36) | 🟢 strong |
| Front HS compression (clicks) | Higher = faster (ρ=-0.27) | 🟢 strong |
| Front torsion bar OD | Thicker = faster (ρ=-0.26) | 🟢 strong |
| Brake bias % | Higher (more fwd) = faster (ρ=-0.25) | 🟢 strong |
| Rear 3rd spring | Softer = faster (ρ=+0.24) | 🟡 moderate |
| Rear camber | Less negative = faster (ρ=+0.23) | 🟡 moderate |
| Body roll (p95) | Less roll = faster (ρ=-0.32) | 🟢 strong |
| LLTD measured | Higher = faster (ρ=-0.29) | 🟢 strong |
| Roll gradient | Steeper = slower (ρ=+0.35) | 🟢 strong |
| Rear bottoming events | More = slower (ρ=+0.34) | 🟢 strong |

**Key Sebring insight:** The track rewards front-end stiffness (high LS comp,
thick torsion bar) and rear compliance (soft 3rd spring). Rear bottoming is
a significant pace killer. Higher LLTD (rear weight transfer) is correlated
with pace — the car prefers a rear-biased balance at this track.