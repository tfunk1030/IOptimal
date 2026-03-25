# Cross-Session Correlation Analysis — BMW LMDh @ Sebring
**Generated:** 2026-03-25  
**Dataset:** 73 sessions (`bmw_*.json`)  
**Lap time range:** 108.017s – 110.492s (+2.475s spread)  
**Method:** Pearson r (raw + fuel-residualized) across all 73 sessions

---

## Top 5 Parameters That Correlate With Lap Time

After controlling for fuel load (r=+0.403 with lap time — significant confound from race vs practice sessions):

| Rank | Parameter | Partial r | Raw r | Interpretation |
|------|-----------|-----------|-------|----------------|
| 1 | `front_ls_comp` (damper) | −0.447 *** | −0.415 *** | Higher front LS compression → faster |
| 2 | `front_toe_mm` | +0.436 *** | +0.458 *** | More negative toe → faster (toe-out) |
| 3 | `rear_toe_mm` | −0.406 *** | −0.457 *** | More positive rear toe → faster (toe-in) |
| 4 | `tc_gain` | −0.360 ** | −0.307 ** | Lower TC intervention → faster |
| 5 | `tc_slip` | −0.337 ** | +0.422 ** | Lower slip threshold → faster |

**Additional meaningful signals (|r| > 0.3):**
- `torsion_bar_od_mm`: r=−0.325 (stiffer = faster)
- `brake_bias_pct`: r=−0.311 (more front bias = faster; range 45.4–47.2%)
- `front_hs_comp`: r=−0.299* (higher HS compression = faster)
- `rear_hs_comp`: r=−0.279* (higher rear HS compression = faster)

**Near-zero / noise:**
- `wing` (r≈0.000 — no variation, all sessions at 17°)
- `front_rh_static` (r=+0.112, p=0.345 — not meaningful)
- `rear_rh_static` (r=+0.040, p=0.734 — not meaningful)
- `rear_arb_blade` (partial r=+0.158, not significant after fuel control)

---

## Objective Function Calibration Gaps

### Critical Finding: lap_gain_ms does NOT predict actual lap time

```
Pearson r(lap_gain_ms, actual_lt) = 0.123  (p=0.300)
```

The objective function score is essentially uncorrelated with real-world lap time. Lap gain range is −95.5 to −58.6 ms (37ms internal spread), but actual performance spread is 2,475ms. This means the objective function is not ranking setups correctly against real-world data.

### Root Causes

**1. Physics model outputs are near-constant across sessions**
- `zeta_ls_front`: same value for all 73 sessions (constant — no discriminating power)
- `zeta_hs_front`, `zeta_hs_rear`: NaN or constant for most sessions
- `df_balance_pct`: constant across all evaluations

**2. LLTD signal is noisy and frequently penalized**
- 68/73 sessions receive `"LLTD outside normal range"` warning
- LLTD range: 0.293–0.518 (wide variance), target appears to be ~0.42–0.45
- Fastest sessions split: some at LLTD=0.362 (wrong direction), some at 0.472–0.518
- LLTD alone cannot distinguish fast from slow setups

**3. Universal telemetry penalty hides real signal**
- All 73 sessions receive `"No telemetry — physics-only prediction"` (+15ms uncertainty penalty)
- This 15ms floor masks smaller real differences in setup quality

**4. setup_distance_ms is anti-correlated with fast sessions**
- Top 4 sessions (108.0–108.3s) all get +15ms setup_distance penalty
- Fastest recorded sessions (actual races) appear as "envelope outliers"
- The reference envelope is centered on practice/calibration setups, not race pace

**5. Fuel load confound**
- `fuel_l` range: 40.9–89.0L across dataset
- Top 7 sessions average 75.4L vs bottom 7 average 89.0L (−13.6L delta)
- ~1.4s of real performance difference explained by fuel alone
- Objective function does not normalize for fuel load

### Score vs Actual Comparison (selective examples)

| Session | Actual lt | lap_gain_ms | Notes |
|---------|-----------|-------------|-------|
| 2ndracebmw (FASTEST) | 108.017s | −73.2 | Low "score" despite fastest real time |
| bmwrace3 | 108.043s | −74.3 | Same — races rank poorly on obj |
| 2026-03-13_12-46-55 | 109.686s | −95.5 | Best obj score, 1.7s SLOWER in real life |
| 2026-03-16_15-25-15 | 109.734s | −91.4 | 2nd best obj, 1.7s slower than fastest |

---

## Recommended Weight Adjustments

### 1. Add Toe Alignment Term (HIGH PRIORITY)
Toe is the single strongest predictor of lap time in this dataset. The objective function has no explicit toe scoring. Add:

```python
# Suggested toe scoring (Sebring BMW)
front_toe_optimal = -0.55  # mm (from top 7 mean = -0.63, cluster center)
rear_toe_optimal = +0.28   # mm (top 7 mean = +0.286)
toe_penalty_ms_per_mm = 150  # ~150ms per 1mm deviation (based on r≈0.44, spread)
```

### 2. Increase TC Sensitivity Weight
TC gain/slip shows r=0.34–0.36 signal that is currently absent from the objective function. Lower TC settings correlate with faster laps — likely driver skill assumption is off.

- Suggested: penalize `tc_gain > 4` by ~30ms, `tc_slip > 4` by ~25ms for BMW at Sebring

### 3. Fuel-Normalize Lap Times
Before any correlation or score comparison, normalize:
```python
lap_time_normalized = best_lap_time_s - (fuel_l * 0.043)  # ~43ms/L estimate
```
This removes ~57% of the variance explained by fuel load.

### 4. Fix LLTD Normal Range
Current LLTD "normal range" rejects 93% of real sessions. Recalibrate:
- Current target: appears to be ~0.40–0.45 (too narrow)
- Observed range across all sessions: 0.293–0.518
- Fastest sessions use 0.362–0.518 — the range is intentional, not a setup fault
- **Recommendation:** Widen LLTD normal band to 0.30–0.55 or remove penalty entirely

### 5. Recalibrate setup_distance_ms Reference Envelope
The fastest real-world setups (race pace) are being penalized as "outliers." The reference envelope should be seeded from the top 10% of sessions by lap time, not from all sessions or a default center.

### 6. w_empirical Should Be Primary Weight
`w_empirical=0.4` is too low. Given that physics terms are near-constant and don't discriminate, empirical signal should be dominant:
```python
w_empirical = 0.9   # was 0.4
w_platform = 0.5    # was 1.0 (currently adding noise, not signal)
w_envelope = 0.2    # was 0.7 (reference envelope is poorly calibrated)
```

---

## Setup Patterns from Top 10% Sessions (7 Fastest)

**Lap time range: 108.017 – 108.573s** (avg: 108.40s vs overall avg: 109.26s)

| Parameter | Top 7 Mean | Full Dataset Mean | Delta |
|-----------|-----------|-------------------|-------|
| `front_toe_mm` | −0.700 | −0.307 | **−0.393** |
| `rear_toe_mm` | +0.286 | −0.109 | **+0.395** |
| `front_heave_nmm` | 40.0 | 47.6 | **−7.6 (softer)** |
| `rear_third_nmm` | 414.3 | 434.0 | **−19.7 (softer)** |
| `rear_arb_blade` | 2.1 | 2.7 | **−0.6 (softer)** |
| `tc_gain` | 4.0 | 4.6 | **−0.6 (lower)** |
| `torsion_bar_od_mm` | 14.28 | 14.15 | +0.13 |
| `fuel_l` | 75.4 | 80.4 | −5.0 |

**Damper profile of top 7:**
| Corner + Channel | Top 7 | Bottom 7 | Delta |
|-----------------|-------|----------|-------|
| Front LS Comp | **9.0** | 7.3 | +1.7 |
| Front HS Comp | **6.6** | 4.7 | +1.9 |
| Rear HS Comp | **7.7** | 5.6 | +2.1 |
| Front HS Slope | **8.4** | 10.9 | −2.4 |
| Rear HS RBD | **8.9** | 10.1 | −1.3 |

**Key takeaway:** Fastest sessions run stiffer front/rear HS compression, softer HS slope, more front toe-out (−0.7mm), positive rear toe (+0.3mm), softer heave/third springs, lower rear ARB blade, and lower TC settings.

---

## Summary

**What actually predicts lap time (in order of signal strength):**
1. Toe alignment (front toe-out + rear toe-in) — strongest signal, missing from obj func
2. Front LS/HS compression damper stiffness — higher = faster
3. TC gain/slip settings — lower = faster (driver skill or track-specific)
4. Torsion bar stiffness — stiffer = faster
5. Fuel load — major confound, must normalize

**What does NOT predict lap time:**
- Wing angle (all sessions: 17°, no variance)
- Ride height (near-zero correlation even without fuel control)
- LLTD error / df_balance (constant or noise in current model)
- Objective function lap_gain_ms score (r=0.12, not useful)

**Next steps:**
1. Add toe scoring term to `ObjectiveFunction`
2. Fuel-normalize lap times before correlation/scoring
3. Widen LLTD normal range
4. Re-seed envelope reference from top-10% sessions
5. Re-run `budget='standard'` on BMW Sebring with corrected weights
