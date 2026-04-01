# Objective Calibration Log

---

## 2026-04-01 — Bi-weekly run (n=99 BMW Sebring sessions)

**Run time:** 2026-04-01 16:00 UTC  
**Branch:** claw-research  
**Sessions loaded:** 102 BMW observation files, 99 with valid lap_time + setup  
**Track:** Sebring International Raceway (all sessions)  
**Lap time range:** 108.000s – 123.315s

---

### ⚠️ Critical Finding: Hard Veto Blocks All Calibration Sessions

**Issue:** 100% of 99 sessions hard-vetoed — heave spring deflection constraint fires universally.

**Root cause (two compounding bugs):**

1. **Parameter key name mismatch** — `objective.py` veto check (line 1619) calls:
   ```python
   _k_front = params.get("front_heave_spring_nmm", 50.0)
   _od_mm = params.get("front_torsion_od_mm", 14.34)
   ```
   But observation files store: `front_heave_nmm` and `torsion_bar_od_mm`.
   Result: the check always falls back to defaults (k=50 N/mm, od=14.34mm).

2. **DeflectionModel returns ~34mm at all realistic k values** — with the BMW
   perch baseline of −13.0mm and od=14.34mm, the formula decomposes as:
   ```
   intercept:      6.54 mm
   inv_heave:      0.14 mm   (negligible — barely varies with k)
   perch_term:    +11.89 mm  (-0.9146 × -13.0)
   inv_od4_term:  +15.76 mm  (666311 / 14.34^4)
   Total:         34.33 mm   → always > 25.0mm legal max → hard veto
   ```
   Even with correct key names, every k value (10–900 N/mm) produces 34.2–34.9mm.
   The legal max is 25.0mm. The model is over-predicting deflection by ~37%.

**Action required:** The heave veto check must be fixed before calibration can use real
observations. Options: (a) fix the DeflectionModel intercept/perch baseline, (b) use
actual setup key names in the veto lookup, or (c) skip the heave hard-veto when evaluating
in `family="calibration"` mode since these are already-raced setups that passed iRacing legality.

---

### Calibration Analysis (Veto Bypassed — Breakdown Scores)

Correlations computed using `breakdown.total_score_ms` directly (bypassing `-1e9` sentinel).
This is the only viable path until the heave veto bug is resolved.

| Term | Pearson r | Notes |
|------|-----------|-------|
| lap_gain_ms | −0.123 | Weak negative — correct direction |
| lltd_balance_ms | +0.115 | 6 unique values, low variance |
| platform_risk.total | −0.114 | Below noise threshold |
| diff_preload_ms | +0.259 | **Moderate** — but only 6 unique values |
| diff_clutch_ms | +0.101 | 2 unique values (binary split) |
| camber_ms | −0.048 | Noise |
| damping_ms | −0.030 | Noise |
| envelope_penalty.total | −0.006 | Noise |
| total_score_ms | +0.003 | Noise — score has no predictive power |
| driver_mismatch | NaN | All zeros — no driver telemetry in any session |
| rebound_ratio_ms | NaN | Single unique value across all 99 sessions |
| df_balance_ms | NaN | Single unique value across all 99 sessions |
| platform_bottoming | NaN | All zero — no bottoming events |
| platform_vortex | NaN | All zero — no vortex events |
| telemetry_uncertainty | NaN | All sessions missing signals (uniform) |

---

### Step 3 — Weight Adjustment Recommendations

**Thresholds per calibration spec:**
- `platform_risk r < 0.1` → reduce w_platform by 20%
- `lltd_error r > 0.3` → increase w_lltd by 20%

| Weight | Current | Threshold Met? | Recommendation |
|--------|---------|----------------|----------------|
| w_platform (1.0) | 1.0 | No — r=−0.114, not < 0.1 | No change |
| w_lltd (embedded in w_lap_gain) | — | No — r=+0.115, not > 0.3 | No change |

**Result: No weight changes triggered this cycle.**

---

### Step 4 — Code Constants

No auto-update applied (neither threshold met). Current weights remain:

```python
w_lap_gain: float = 1.0
w_platform: float = 1.0   # last changed: 2026-03 (raised from 0.9)
w_driver:   float = 0.5
w_uncertainty: float = 0.6
w_envelope: float = 0.7
w_staleness: float = 0.3
w_empirical: float = 0.40
```

---

### Context: Prior Calibration Run (2026-03-28, 75 sessions)

From `calibration_report.md`:
- lltd_balance_ms showed highest holdout degradation when removed (ablation: Spearman −0.114 → −0.020)
- rebound_ratio_ms also high holdout impact
- Weight search suggested: lap_gain=0.25, all others=0.0 — **manual review declined** (auto-apply=False)
- Overall Spearman: −0.059 to −0.134 depending on mode (weak, expected for noisy iRacing lap data)

---

### Structural Recommendations (non-blocking for next cycle)

1. **Fix heave veto key names** in `solver/objective.py` lines 1619–1620:
   ```python
   # Current (wrong):
   _k_front = params.get("front_heave_spring_nmm", 50.0)
   _od_mm   = params.get("front_torsion_od_mm", 14.34)
   # Fix:
   _k_front = params.get("front_heave_spring_nmm") or params.get("front_heave_nmm", 50.0)
   _od_mm   = params.get("front_torsion_od_mm") or params.get("torsion_bar_od_mm", 14.34)
   ```

2. **Investigate DeflectionModel** — inv_od4_coeff=666311 and perch_baseline=−13.0mm
   combine to produce ~34mm regardless of k. Either the perch sign is wrong in baseline
   or the inv_od4_coeff needs revisiting against calibration data.

3. **Add driver_mismatch telemetry** — all 99 sessions report zero driver_mismatch because
   trail_brake/throttle style signals are absent. This weight (0.5) is non-zero but scoring
   nothing in practice.

4. **Track-segment diversity** — all 99 sessions are Sebring-only. Correlations will be
   more meaningful when multi-track data is available (Lime Rock, Road Atlanta, etc.).

---

*Next scheduled run: ~2026-04-15*
