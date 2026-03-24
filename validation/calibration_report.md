# Objective Weight Calibration Report

Generated from 69 BMW Sebring observations.

## Current Model Correlation

- Pearson r (score vs lap_time):  **-0.1404**
- Spearman ρ (score vs lap_time): **-0.2119**
- Expected: negative (higher score → lower lap time)

⚠️ Weak negative correlation — weights need tuning.

## Lap Time Distribution

- Min: 108.334s
- Median: 109.333s
- Max: 110.492s
- Spread: 2.159s

## Score Distribution

- Min: -440.4ms
- Median: -94.3ms
- Max: -68.0ms
- Spread: 372.3ms

## Component-Level Correlation with Lap Time

| Component | Pearson r | Spearman ρ | Direction |
|-----------|-----------|------------|-----------|
| total_score | -0.1404 | -0.2119 | ✅ |
| lap_gain_ms | -0.1401 | -0.3023 | ✅ |
| platform_risk | +0.0000 | -0.0479 | ⚠️ |
| driver_mismatch | +0.0000 | -0.0479 | ⚠️ |
| telemetry_uncertainty | +0.0000 | -0.0479 | ⚠️ |
| envelope_penalty | +0.0370 | +0.0117 | ✅ |
| lltd | -0.1548 | -0.0622 | ⚠️ |
| lltd_error | -0.0071 | +0.1214 | ✅ |
| front_sigma_mm | +0.0000 | -0.0479 | ⚠️ |
| df_balance_pct | -0.0711 | -0.0263 | ✅ |
| df_balance_error_pct | +0.0717 | +0.0391 | ✅ |

*Penalties should correlate POSITIVELY with lap time (more penalty → slower).*
*Total score should correlate NEGATIVELY (higher score → faster).*

## Optimized Weights (Grid Search)

Best Spearman ρ achievable: **-0.0686**

| Weight | Current | Suggested |
|--------|---------|-----------|
| platform | 1.0 | 0.0 |
| driver | 0.5 | 0.0 |
| telemetry | 0.6 | 0.0 |
| envelope | 0.7 | 0.2 |

## Top 10 Best-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 1 | -68.0 | 109.264 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 2 | -74.3 | 109.257 | bmw_sebring_international_raceway_bmw2bad.json |
| 3 | -75.0 | 109.040 | bmw_sebring_international_raceway_bmw2.json |
| 4 | -78.0 | 109.013 | bmw_sebring_international_raceway_bmw20.json |
| 5 | -78.5 | 109.242 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 6 | -80.0 | 109.428 | bmw_sebring_international_raceway_bmwbad2.json |
| 7 | -83.0 | 109.418 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 8 | -83.8 | 109.655 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 9 | -83.9 | 109.927 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 10 | -83.9 | 109.535 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Bottom 10 Worst-Scored Setups vs Actual Lap Time

| Rank | Score (ms) | Lap Time (s) | File |
|------|-----------|--------------|------|
| 60 | -101.9 | 109.378 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 61 | -102.1 | 109.361 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 62 | -103.9 | 109.504 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 63 | -107.9 | 109.521 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 64 | -109.1 | 109.733 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 65 | -122.5 | 109.114 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 66 | -129.3 | 109.372 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 67 | -129.3 | 109.834 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |
| 68 | -135.3 | 109.820 | bmw_sebring_international_raceway_bmwbad.json |
| 69 | -440.4 | 109.614 | bmw_sebring_international_raceway_bmwlmdh_sebring_ |

## Anomalies (Model vs Reality Disagreements)

| Score Rank | Time Rank | Δ | Score (ms) | Lap (s) | File |
|-----------|-----------|---|-----------|---------|------|
| 9 | 66 | 57 | -83.9 | 109.927 | bmw_sebring_international_raceway_bmwlmd |
| 17 | 69 | 52 | -87.7 | 110.492 | bmw_sebring_international_raceway_bmwlmd |
| 65 | 15 | 50 | -122.5 | 109.114 | bmw_sebring_international_raceway_bmwlmd |
| 53 | 3 | 50 | -98.7 | 108.473 | bmw_sebring_international_raceway_bmwlmd |
| 8 | 58 | 50 | -83.8 | 109.655 | bmw_sebring_international_raceway_bmwlmd |
| 56 | 11 | 45 | -100.3 | 109.094 | bmw_sebring_international_raceway_bmwlmd |
| 10 | 52 | 42 | -83.9 | 109.535 | bmw_sebring_international_raceway_bmwlmd |
| 6 | 47 | 41 | -80.0 | 109.428 | bmw_sebring_international_raceway_bmwbad |
| 44 | 4 | 40 | -96.8 | 108.573 | bmw_sebring_international_raceway_bmwlmd |
| 18 | 57 | 39 | -88.2 | 109.628 | bmw_sebring_international_raceway_bmwlmd |

*Large Δ = model disagrees with reality. Investigate these setups.*

## Setup Parameter Correlations with Lap Time (Sebring)

Direct correlation of raw setup values vs observed lap time (n=68 sessions).
Negative ρ → higher value = faster lap. Positive ρ → higher value = slower.

| Parameter | Spearman ρ | Direction | Signal |
|-----------|-----------|-----------|--------|
| front_ls_comp | -0.425 | faster ↑ | 🟢 strong |
| front_hs_comp | -0.295 | faster ↑ | 🟡 moderate |
| brake_bias_pct | -0.295 | faster ↑ | 🟡 moderate |
| rear_hs_comp | -0.293 | faster ↑ | 🟡 moderate |
| rear_camber_deg | +0.249 | slower ↑ | 🟡 moderate |
| rear_third_spring_nmm | +0.240 | slower ↑ | 🟡 moderate |
| front_torsion_od_mm | -0.233 | faster ↑ | 🟡 moderate |
| rear_spring_rate_nmm | -0.167 | faster ↑ | 🟡 moderate |
| front_ls_rbd | -0.150 | faster ↑ | ⚫ noise |
| rear_pushrod_offset_mm | -0.146 | faster ↑ | ⚫ noise |
| front_heave_spring_nmm | +0.142 | slower ↑ | ⚫ noise |
| rear_arb_blade | +0.134 | slower ↑ | ⚫ noise |
| front_hs_rbd | +0.099 | weak | ⚫ noise |
| front_pushrod_offset_mm | +0.052 | weak | ⚫ noise |
| front_arb_blade | -0.049 | weak | ⚫ noise |
| rear_ls_rbd | +0.048 | weak | ⚫ noise |
| front_camber_deg | +0.044 | weak | ⚫ noise |
| front_rh_static_mm | +0.037 | weak | ⚫ noise |
| rear_ls_comp | -0.036 | weak | ⚫ noise |
| rear_hs_rbd | -0.009 | weak | ⚫ noise |
| rear_rh_static_mm | -0.005 | weak | ⚫ noise |

## Telemetry Correlations with Lap Time (Sebring)

IBT-measured telemetry vs observed lap time. Shows what physical states predict pace.

| Telemetry Field | Spearman ρ | Direction | Signal |
|----------------|-----------|-----------|--------|
| roll_gradient_deg_per_g | +0.370 | slower ↑ | 🟢 strong |
| rear_bottoming_events | +0.351 | slower ↑ | 🟢 strong |
| body_roll_p95_deg | -0.343 | faster ↑ | 🟢 strong |
| body_roll_max_deg | +0.307 | slower ↑ | 🟢 strong |
| lltd_measured | -0.271 | faster ↑ | 🟡 moderate |
| rear_rh_std_mm | +0.238 | slower ↑ | 🟡 moderate |
| dynamic_front_rh_mm | +0.230 | slower ↑ | 🟡 moderate |
| front_dominant_freq_hz | -0.179 | faster ↑ | 🟡 moderate |
| dynamic_rear_rh_mm | +0.152 | slower ↑ | 🟡 moderate |
| rear_shock_vel_p99_mps | +0.135 | slower ↑ | ⚫ noise |
| body_slip_p95_deg | +0.110 | slower ↑ | ⚫ noise |
| front_shock_vel_p95_mps | -0.096 | weak | ⚫ noise |
| front_rh_std_mm | +0.093 | weak | ⚫ noise |
| front_shock_vel_p99_mps | -0.091 | weak | ⚫ noise |
| front_bottoming_events | +0.053 | weak | ⚫ noise |
| understeer_mean_deg | +0.034 | weak | ⚫ noise |
| front_rh_settle_time_ms | -0.030 | weak | ⚫ noise |
| understeer_low_speed_deg | +0.018 | weak | ⚫ noise |
| rear_dominant_freq_hz | -0.011 | weak | ⚫ noise |
| understeer_high_speed_deg | +0.011 | weak | ⚫ noise |

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