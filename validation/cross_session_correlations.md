# Cross-Session Correlation Analysis — BMW LMDh @ Sebring
**Generated:** 2026-04-01  
**Dataset:** 99 sessions with valid lap times (bmw_*.json observations)  
**Lap time range:** 108.000s – 123.315s | **Mean:** 109.864s

---

## 1. Top 5 Parameters That Correlate with Lap Time

Analysis across all 99 sessions (Pearson r):

| Rank | Parameter | r | p-value | n | Direction |
|------|-----------|---|---------|---|-----------|
| 1 | `front_rh_static` | +0.854 | < 0.001 | 99 | Higher RH → slower |
| 2 | `tc_slip` | +0.608 | < 0.001 | 70 | Higher slip threshold → slower |
| 3 | `rear_rh_static` | +0.449 | < 0.001 | 99 | Higher RH → slower |
| 4 | `fuel_l` | −0.417 | < 0.001 | 99 | More fuel → faster† |
| 5 | `damper_lr/rr_ls_rbd` | +0.256 | 0.010 | 99 | Higher rear LS rebound → slower |

†fuel_l negative correlation is expected (light fuel = fewer laps = later in stint = optimized lap).

### Fuel-Stratified Analysis (Confound Control)

The overall `front_rh_static` r=0.854 is **inflated by cross-session confounding** — slow sessions (117–123s) tend to have both higher RH *and* lower fuel (exploratory test runs). When stratified:

| Fuel Group | n | FRH r | TC slip r | RRH r |
|-----------|---|-------|-----------|-------|
| **Heavy (≥ 85L)** — race-start conditions | 76 | 0.117 | 0.157 | 0.083 |
| **Light (< 60L)** — test/stint conditions | 23 | **0.950** | **0.866** | **0.912** |

**Key finding:** In race-fuel conditions (76 sessions, 108.1–110.5s range), **ride height and TC settings are NOT the dominant lap time predictor**. Damper tuning emerges as the real differentiator.

### Race-Condition Damper Correlations (heavy fuel sessions only, n=76)

| Parameter | r | p-value | Significance |
|-----------|---|---------|-------------|
| `damper_lf/rf_ls_comp` | **−0.447** | < 0.001 | ★★★ |
| `damper_lr/rr_hs_comp` | **−0.385** | < 0.001 | ★★★ |
| `damper_lf/rf_hs_comp` | **−0.350** | 0.002 | ★★ |
| `damper_lr/rr_ls_rbd` | +0.121 | 0.299 | — |
| `damper_lf/rf_ls_rbd` | −0.124 | 0.285 | — |

**Critical insight:** Higher compression clicks (both LS and HS) correlate with faster laps in race conditions. Higher compression = better platform control at speed.

---

## 2. Objective Function Calibration Gaps

### 2a. Hard Veto Miscalibration — CRITICAL

**All 99 historical sessions are hard-vetoed** with reason:  
`"Heave spring defl too high: >25.0mm (legal max)"`

The veto fires because historical setups use `front_heave_nmm` in the 10–90 N/mm range, which yields deflection estimates above the 25mm veto threshold. **This means:**
- `obj.evaluate()` returns `score = -1,000,000,000` for every historical session
- Zero score differentiation across the entire dataset
- Objective function cannot be correlated with real lap times

**Root cause:** The veto threshold was calibrated for candidate setups (where the solver constrains heave spring), not for the full range of historical setups actually run on track.

**Recommended fix:**
```python
# In _compute_platform_risk: relax veto to soft penalty for historical family
if family in ('historical', 'observed') and defl_mm > legal_max:
    soft_penalties.append(f"Heave defl {defl_mm:.1f}mm (historical — soft penalty only)")
    platform_risk.slider_exhaustion_ms += (defl_mm - legal_max) * 2.0  # ms penalty
    # Don't add to veto_reasons
```

### 2b. Physics Constants — Physics Model Not Differentiating

Most physics output values are **identical across all 99 sessions**:

| Physics Output | Value | Variance |
|----------------|-------|----------|
| `df_balance_pct` | 51.06% | 0.000 |
| `front_sigma_mm` | 2.00 | 0.000 |
| `rear_sigma_mm` | 3.00 | 0.000 |
| `zeta_ls_front` | 0.640 | 0.000 |
| `zeta_ls_rear` | 0.247 | 0.000 |
| `front_wheel_rate_nmm` | 30.0 | 0.000 |
| `rear_wheel_rate_nmm` | 57.6 | 0.000 |

The heave/damper physics evaluator is anchored to a narrow operating point that doesn't capture the variance in the historical dataset.

### 2c. Term Correlations (using raw breakdown values, ignoring veto)

| Objective Term | r vs lap time | Interpretation |
|----------------|--------------|----------------|
| `lap_gain_ms` | −0.129 | Weak (expected −, not significant) |
| `platform_risk_ms` | −0.122 | Wrong direction (higher risk = faster?) |
| `envelope_penalty_ms` | +0.029 | Near zero — envelope not predictive |
| `lltd` | −0.070 | Not significant |
| `lltd_error` | +0.111 | Not significant |

**The objective function's internal terms are not meaningfully predicting lap time from historical data.**

---

## 3. Recommended Weight Adjustments

### 3a. Damping Terms — INCREASE WEIGHT

Front LS compression and rear HS compression are the strongest predictors in race conditions but receive minimal weighting. Recommended:

```python
# Current -> Recommended
w_lap_gain = 1.0           # keep
w_platform = 0.75          # keep (but fix veto threshold first)
w_driver = 0.3             # keep
w_uncertainty = 0.4        # keep
w_envelope = 0.55          # reduce to 0.30 — currently adding noise not signal
w_damping_signal = 0.50    # NEW: add explicit damper compression reward term
```

### 3b. LLTD Term — Reduce or Recalibrate

`lltd_balance_ms` has `r = +0.104` with lap time (wrong direction — should be negative for balance errors). The LLTD target may be miscalibrated for Sebring. Consider:
- Recalculate `measured_lltd_target` from the 8 fastest sessions: mean LLTD ≈ 0.423 (not 0.472 current target)

### 3c. Envelope Penalty — Recenter

`setup_distance_ms` shows r=0.029 with lap time — the envelope center is not aligned with fast setups. The top-10% fastest setups cluster tightly:
- `front_rh_static`: 30.0–30.4mm
- `rear_rh_static`: 48.1–49.4mm
- `wing`: 17.0 (all fast sessions)

---

## 4. Setup Patterns from Top 10% Fastest Sessions

**Threshold:** < 108.334s (top 10 of 99 sessions)  
**n = 11 sessions**

### Hardware (invariant across top sessions)
- `wing`: **17.0** (100% of fast sessions)
- `front_arb_blade`: **1** (Soft ARB, 100%)
- `front_arb_size`: **Soft**

### Ride Heights (tight cluster)
- `front_rh_static`: **30.0 – 30.4mm** (mean: 30.08mm)
- `rear_rh_static`: **48.1 – 49.4mm** (mean: 48.94mm)

### Dampers (top 10% race-start heavy fuel, n=8)
| Corner | LS comp | LS rbd | HS comp |
|--------|---------|--------|---------|
| Front (LF/RF) | 8–10 (mean 9.2) | 5–8 (mean 5.9) | 4–9 (mean 7.1) |
| Rear (LR/RR) | 5–6 (mean 5.4) | 5–7 (mean 5.8) | 6–10 (mean 8.5) |

**Pattern:** Two distinct fast clusters exist:
1. **High compression** (lf_ls_comp=10, lr_hs_comp=10) — stiffer platform, possibly better aero
2. **Medium compression** (lf_ls_comp=8, lr_hs_comp=6) — softer feel, similar pace

### Other Parameters
- `rear_arb_blade`: 1–3, mean 2.45 (no strong pattern)
- `front_camber_deg`: −2.9 to −2.1 (mean −2.52)
- `rear_camber_deg`: −1.9 to −1.7 (mean −1.78)
- `tc_slip`: 3–4 (lower is faster; avoid ≥ 5)
- `tc_gain`: 3–5 (lower is faster in top sessions)

---

## 5. Action Items

### Immediate (objective function fixes)
1. **Fix hard veto threshold** — historical setups should use soft penalty not hard veto for heave defl > 25mm
2. **Recenter LLTD target** for Sebring: change `measured_lltd_target` from 0.472 to ~0.423
3. **Add front LS compression reward** to `_compute_lap_gain_breakdown` (currently missing)

### Calibration (weight adjustments)
4. Reduce `w_envelope` from 0.55 → 0.30 (not predictive)
5. Add `damping_compression_ms` term (r ≈ −0.45 in race conditions)
6. Fix `df_balance_pct` constant output — investigate why physics aren't varying

### Data collection
7. Collect more sessions in **light fuel conditions** (< 60L) to study RH sensitivity cleanly
8. Run controlled damper sweep (fix everything, vary only LS comp) — 5+ sessions

---

*Analysis run on 2026-04-01. Methodology: Pearson r, scipy.stats.pearsonr, n=99 BMW Sebring sessions.*

---

## 2026-04-04 Addendum — Fixes Applied

The following issues from the 2026-04-01 analysis have been resolved:

### Resolved

1. **Hard veto on all sessions (#1)** — FIXED. DeflectionModel intercept was already corrected
   (-20.756). Additionally, the deflection veto now checks `car.deflection.is_calibrated`
   and skips the check entirely for uncalibrated cars (Porsche, Acura, Cadillac).

2. **LLTD target (#2)** — BMW `measured_lltd_target` is 0.41 (set from IBT data, not 0.472).
   Cars without measured LLTD data are now blocked by the CalibrationGate at Step 4.

3. **Front LS compression reward (#3, #5)** — ADDED. Damper compression bonus in
   `_estimate_lap_gain()`, gated behind `zeta_is_calibrated`. BMW only (r=-0.447 measured).

4. **Zero-variance physics (#6)** — FIXED. Variable ordering bug caused `UnboundLocalError`.
   All physics outputs now vary across observations:
   - df_balance: 48.3–49.2% (was constant 51.06%)
   - zeta_ls_front: 0.62–0.76 (was constant 0.640)
   - front_wheel_rate: 30.0–34.0 N/mm (was constant 30.0)
   - LLTD: 0.41–0.54 (was constant)

5. **driver_mismatch always zero** — `w_driver` now set to 0.0 when no driver profile
   is available, preventing wasted weight budget.

### New: Calibration Gate

Solver steps are now gated by per-car, per-subsystem calibration status. Uncalibrated
steps output calibration instructions instead of setup values. See
`car_model/calibration_gate.py` for the framework.
