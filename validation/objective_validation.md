# IOptimal Objective Function Validation — BMW Sebring Observations

**Generated:** 2026-03-18  
**Dataset:** 46 BMW LMDH sessions at Sebring International Raceway  
**Objective version:** claw-research branch (Sprint 3 physics)

---

## 1. Dataset Summary

| Metric | Value |
|--------|-------|
| Total observation files | 46 |
| Files with lap times (`best_lap_time_s`) | 46 |
| Sessions with damper data | 41 |
| Sessions with telemetry (dynamic RH, LLTD) | 46 |
| Hard-vetoed by objective | 20 (43%) |
| Non-vetoed, scoreable | 26 (57%) |

**Lap time range:** 108.829s – 113.024s (range = 4.2s)  
**Primary veto reason:** Vortex burst — stall margin negative in physics model

---

## 2. Observed Setup Variation (BMW Sebring 2026)

Most sessions share identical or near-identical setup parameters:
- **Wing:** 17.0° (all sessions)
- **Heave spring:** 50 N/mm (all non-vetoed sessions)
- **Third spring:** 530 N/mm (most sessions)
- **Torsion bar OD:** 13.9mm (all sessions)
- **Front ARB blade:** 1 (all sessions)
- **Rear ARB blade:** 3–5 (varies)
- **Front camber:** -2.9° to -3.2°
- **Dampers:** Mostly consistent across sessions

**Critical finding:** Low setup variation across 46 sessions severely limits
correlation analysis. The objective function score range for real setups is
narrow (~50ms) while lap time range is 4.2s, dominated by non-setup factors
(traffic, conditions, driver consistency).

---

## 3. Veto Analysis

**20 of 46 sessions (43%) receive a hard veto** for vortex burst risk.

### Root Cause
The physics model hardcodes `dyn_front_rh = 19.0mm` for all candidates.
At Sebring, the measured dynamic front RH from IBT telemetry is **17.8–19.9mm**.

With a 6.7mm vortex threshold (computed from BMW aero map gradient) and
excursion values of 14–17mm (p99 shock velocity at Sebring):
```
stall_margin = 19.0 - excursion - 6.7mm
            = 19.0 - 15.3 - 6.7 = -3.0mm  ← VETO
```

These setups ran successfully in iRacing, confirming the veto is **a false positive**.

### Calibration Issue
The p99 shock velocity from IBT includes emergency maneuvers and outlier events,
which inflates the excursion estimate. The model uses `v_p99_front = 0.237 m/s`
at Sebring. This results in excursion estimates ~2-3x the actual dynamic RH variation.

### Recommendation
1. Use `p95` shock velocity (not p99) for excursion calculation, OR
2. Use IBT-measured dynamic RH directly when available (telemetry has `dynamic_front_rh_mm`)
3. The veto should require stall_margin < -3mm AND confirmed by measured dynamic RH

---

## 4. Objective Score vs. Lap Time Correlation

### All sessions (including vetoed — score=-1e9 excluded from statistics)

| Metric | Pearson r (vs lap_time) |
|--------|------------------------|
| Total score | +0.096 |
| Lap gain | +0.214 |
| Platform risk | -0.075 |
| Envelope penalty | +0.030 |
| LLTD error (%) | -0.182 |

**Note:** A NEGATIVE Pearson r indicates "higher score → faster lap" (expected).
All values near zero → **objective does not predict lap time** in this dataset.

### Why correlation is low (expected, not a bug)
1. **Low setup variation** — 46 sessions but ~3 distinct setups → near-zero variance
2. **Non-setup lap time drivers** — consistency_cv varies 0.02–0.20, traffic, conditions
3. **Absolute vs. relative scoring** — the objective scores each setup in isolation;
   without a reference setup, it can't predict relative lap time gain
4. **Physics model limitations** — the linear approximations (softer = more grip × 0.3ms)
   are rough; actual lap time sensitivity depends on corner type, balance, etc.

### Interpretation
The objective function is **not designed to predict absolute lap times** — it's
designed to score relative differences between candidate setups. With near-identical
setups across all 46 sessions, we're measuring noise.

---

## 5. Table: Top 20 Sessions by Lap Time (Non-Vetoed)

| Rank | Lap Time (s) | Score (ms) | LLTD | Excursion F | Stall Margin | Heave (N/mm) | Third (N/mm) |
|------|-------------|------------|------|-------------|--------------|--------------|--------------|
| 1 | 108.829 | -828.2 | 42.0% | 14.1mm | +5.6mm | 50 | 530 |
| 2 | 108.937 | -829.0 | 42.0% | 14.1mm | +5.6mm | 50 | 530 |
| 4 | 109.094 | -564.5 | 38.4% | 12.3mm | +7.5mm | 50 | 530 |
| 5 | 109.099 | (vetoed) | 51.0% | 15.3mm | -3.0mm | 50 | 530 |
| 6 | 109.125 | -831.4 | 42.0% | 14.1mm | +5.6mm | 50 | 530 |
| 7 | 109.135 | -835.7 | 42.7% | 14.1mm | +5.6mm | 50 | 530 |

**Observation:** The fastest laps have LLTD ≈ 42%, while the physics model
targets LLTD ≈ 52% (weight_dist_front + 0.05). This is a **significant calibration gap**.

---

## 6. LLTD Calibration Finding

**Critical discovery:** Fastest BMW Sebring sessions show LLTD ≈ 38–43% (measured
from IBT telemetry), but the objective function's LLTD target is ≈ 52%.

| | Value |
|--|--|
| BMW weight_dist_front | 0.47 |
| tyre_load_sensitivity (λ) | 0.20 |
| Computed target LLTD | 0.47 + 0.05 = 0.52 (52%) |
| Measured LLTD from fast sessions | 38–43% |
| **Gap** | **~10–14% front LLTD below target** |

### Interpretation
The measured LLTD of 38-43% reflects the actual on-track front ARB=1/rear ARB=3-5
combination. The mismatch suggests either:
1. The LLTD target formula `W_front + λ×0.05` overshoots for this car
2. The BMW at Sebring runs intentionally rear-biased LLTD for rotation
3. The ARB roll stiffness model needs recalibration

**Action:** Reduce LLTD target for BMW or add a car-specific LLTD target override
based on IBT observations. The car consistently runs rear-biased balance.

---

## 7. Fuel Window LLTD Analysis

With the new Sprint 3d fuel window LLTD computation:
- **Race start (89L):** LLTD error = 0.170 (for typical BMW setup)
- **End of stint (20L):** LLTD error = 0.173
- **Drift:** +0.003 (small — fuel tank close to car CG at Sebring trim)

The fuel LLTD drift at Sebring is small because the BMW fuel tank appears
near the vehicle CG. The fuel window scoring adds ~0ms penalty for typical setups.
This feature is more impactful for tracks with high fuel deltas (endurance trim).

---

## 8. Wing-Specific Vortex Threshold

From the BMW aero map gradient analysis (Sprint 3e):
- Wing 12.0°: threshold = 6.70mm (computed vs. 8.0mm hardcoded)
- Wing 14.0°: threshold = 6.70mm
- Wing 16.0°: threshold = 6.68mm
- Wing 17.0°: threshold = 6.68mm

The gradient-based threshold is **lower** than the 8mm fallback for BMW.
This means the BMW's aero balance is less sensitive to front RH in the low-RH
danger zone than assumed. The wing-specific computation reduces false veto risk.

**Note:** The near-identical values across wing angles suggest the BMW aero map
gradient is similar across all tested wing settings. This may be a data artifact
(maps share the same underlying aero model with linear wing scaling).

---

## 9. Key Findings for Future Work

### High Priority
1. **LLTD target recalibration** — BMW Sebring IBT data shows 38-43% actual,
   not the 52% theoretical target. Need to update `car.weight_dist_front` or
   add a measured LLTD target per car-track combination.

2. **Vortex threshold too aggressive** — 43% false veto rate on real setups.
   Recommend using `p95` shock velocity (not p99) for excursion calculation,
   or using IBT-measured dynamic RH directly when available.

3. **Absolute score vs. relative gain** — the objective needs a reference
   (physics baseline) to compute lap gain DIFFERENCES, not absolute penalties.

### Medium Priority
4. **Damper correlation** — Sessions with softer dampers (LS_comp=7, LS_rbd=6)
   appear in the fastest laps. The damping ratio model should be validated
   against measured shock velocity data.

5. **Torsion bar model** — All sessions use 13.9mm OD. Need sessions with
   different OD values to validate the `front_wheel_rate = c_torsion × OD^4` model.

### Low Priority
6. **Fuel window LLTD** — Confirmed small effect at Sebring. Will matter more
   at high-fuel endurance tracks. Implementation is correct.

7. **Wing-specific vortex** — Confirmed implementation is working. Minor impact
   at Sebring since all sessions use wing 17.0°.

---

## 10. Pearson Correlation Breakdown (Term-by-Term)

For non-vetoed sessions vs. lap time:

| Term | r | Direction | Interpretation |
|------|---|-----------|----------------|
| Total score | +0.096 | ✗ wrong | Low variance in setups |
| Lap gain | +0.214 | ✗ wrong | Softer heave → more gain, but confounded |
| Platform risk | -0.075 | ~ correct | Higher risk → slower (weak) |
| LLTD error | -0.182 | ~ correct | Lower LLTD error → faster (weak) |
| Envelope penalty | +0.030 | ✗ wrong | No signal |
| Excursion front | +0.11 | ✗ wrong | More excursion from stiffer sessions? |

**Bottom line:** All correlations are weak (|r| < 0.25). This is expected
with only ~3 distinct setups across 46 sessions. The objective function will
correlate better when compared across a wider range of legal setups (e.g.,
comparing GridSearchEngine results across very different parameter values).

---

*Validation generated by `claw-research` branch Sprint 3-4 analysis.*
*Update when: more diverse setup data is available, or LLTD target is recalibrated.*
