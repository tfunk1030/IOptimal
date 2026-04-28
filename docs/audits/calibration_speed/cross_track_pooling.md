# Cross-Track Compliance-Only Pooling — Feasibility Audit

**Status:** DIAGNOSTIC ONLY (no code changes — see "Implementation sketch" for proposed API).
**Date:** 2026-04-27.
**Author:** Unit 8 of calibration-speed batch.
**Files surveyed:** `data/calibration/<car>/{calibration_points.json, models.json, models_<track>.json}` for all 5 GTP cars; `car_model/auto_calibrate.py:_setup_key` (track-as-fingerprint policy) and `_UNIVERSAL_POOL` (compliance-term names).

---

## 1. Motivation & history

`car_model/auto_calibrate.py:99` carries the long-standing comment:

> Pooling cross-track data causes 27x-103x LOO/train overfitting ratios.

That ratio was measured against the **full** universal coefficient pool — every spring rate, every perch, every pushrod, every camber, every interaction. The selector does forward LOO-greedy feature selection, and with 23–40 unique setups it happily picks `inv_rear_spring` to fit Algarve front RH at coefficient -21934, then catastrophically extrapolates to Laguna Seca. The fix landed in 2026-04-11 (universal pool → physics-aware front/rear pools) and in 2026-04-10 (3:1 sample-to-feature ratio).

What that ban does NOT distinguish is **which terms** are actually track-dependent. Compliance terms (`1/k`) are first-principles from `defl ∝ F/k` where F is the dynamic load and k is the spring rate. The spring's compliance is a **car physical constant**, not a track property. The track only enters through F (aero downforce + cornering inertia + bump excitation), and that lives in the **intercept** and any `fuel × compliance` interaction (which carries fuel mass × 1/k → constant cancels). The compliance slope itself, in pure physics, should be track-invariant.

Whether reality matches that hope is empirical. This audit collects the evidence.

---

## 2. Multi-track car inventory

Source: `data/calibration/<car>/calibration_points.json` (counted via setup-fingerprint dedup minus the track field).

| Car | Track A | n_sessions | n_unique_setups | Track B | n_sessions | n_unique_setups | Per-track models present? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Porsche** | Autodromo Internacional do Algarve | 152 | 31 | WeatherTech Raceway Laguna Seca | 17 | 6 | yes (`models_algarve.json`, `models_laguna_seca.json`, plus stale `models_weathertech_raceway_laguna_seca.json` with n=2) |
| **Ferrari** | Hockenheimring Baden-Württemberg | 100 | 14 | Autodromo Internacional do Algarve | 19 | 4 | partial (`models_hockenheim.json` only — no `models_algarve.json`) |
| **Cadillac** | WeatherTech Raceway Laguna Seca | 20 | 4 | Silverstone Circuit | 3 | 1 | none (only unified `models.json`) |
| **Acura** | Hockenheimring Baden-Württemberg | 23 | 6 | Daytona International Speedway | 6 | 1 | partial (`models_hockenheim.json` only) |
| **BMW** | Sebring International Raceway | 79 | 26 | — | — | — | n/a (single-track) |

**Track-fingerprint policy.** `auto_calibrate.py:_setup_key()` includes the raw track string as the first tuple element (line 103), so two physically-identical setups on different tracks count as **two** unique calibration points. This means the unified `models.json` already pools cross-track data (every car with multi-track points has more `n_unique_setups` than any single-track file), but it does so **silently** — the regression itself doesn't carry track as a feature, so the slope coefficients are estimated on a mixed dataset and the intercept averages over tracks.

Verified: Ferrari `models.json` has `n_unique_setups=18 = 14 (Hocken) + 4 (Algarve)`, Acura has `8 = 6 + 1 + 1 anchor`, Cadillac has `5 = 4 + 1`, Porsche has `43 ≈ 40 (Algarve, near-Hockenheim variants in `_setup_key` differ from per-track 31 — extra 9 are likely virtual anchors from Unit 9 of an earlier batch and/or fingerprint-only differences across captured points)`. The unified files exist and the pipeline can fall back to them via `load_calibrated_models(car, track)` when the per-track file is missing or too sparse (`auto_calibrate.py:432-441`).

---

## 3. Compliance coefficient comparison

Source: per-track `models_<track>.json` files. Compliance terms in `_UNIVERSAL_POOL`:

```
inv_front_heave, inv_rear_third, inv_rear_spring, inv_od4,
fuel_x_inv_spring, fuel_x_inv_third,
inv_front_corner_spring, inv_rear_corner_spring,
fuel_x_inv_front_corner_spring, fuel_x_inv_rear_corner_spring,
front_pushrod_sq, rear_pushrod_sq
```

Only Porsche (Algarve vs Laguna Seca) and Ferrari (unified vs Hockenheim — using the unified file as the de-facto cross-track fit) have apples-to-apples pairs. Coefficients extracted directly from the JSON `coefficients` arrays (offset 0 = intercept, offset 1+ = `feature_names[i-1]`).

### 3.1 Porsche — Algarve (40 setups, R²=0.999 front RH) vs Laguna Seca (6 setups, R²=1.0 front RH)

Only 8 compliance coefficients appear in BOTH track-fitted models. Most compliance features were dropped by feature selection on Laguna Seca because n=6 forces the 3:1 ratio cap to ≤2 features.

| Target | Compliance feature | Algarve | Laguna Seca | % diff | Verdict |
| --- | --- | --- | --- | --- | --- |
| `rear_ride_height` | `inv_rear_spring` | 0.473 | 164.4 | **34,632%** | track-sensitive (catastrophic; small-sample noise on Laguna) |
| `rear_ride_height` | `inv_rear_third` | -896 | +63.4 | **107%** with sign flip | track-sensitive (sign flip — physics-implausible, but Laguna only has 6 setups) |
| `front_shock_defl_static` | `fuel_x_inv_third` | -2.5 | -130 | **5,123%** | **noisy** — both models flagged `is_calibrated=False` on Algarve (R²=0.07!), Laguna is 6-sample fit |
| `heave_spring_defl_static` | `inv_front_heave` | 2,531 | 2,923 | **15.8%** | **borderline track-sensitive but physically reasonable** |
| `heave_spring_defl_static` | `rear_pushrod_sq` | 2.3e-05 | 7.1e-04 | 2,964% | spurious / noise (orders-of-magnitude smaller than `inv_front_heave` term) |
| `heave_spring_defl_max` | `inv_front_heave` | 1,611 | 3,369 | **109%** | track-sensitive (max compression IS track-bump-driven, expected) |
| `rear_spring_defl_max` | `fuel_x_inv_spring` | 26.3 | 72.0 | **174%** | track-sensitive |
| `rear_spring_defl_max` | `inv_rear_spring` | 0.95 | 1.24 | **30.2%** | borderline |

**Intercepts** (track-dependent by definition — aero + bump signature):

| Target | Algarve | Laguna | % diff |
| --- | --- | --- | --- |
| `front_ride_height` | 91.8 | 30.3 | 67% |
| `rear_ride_height` | 149 | 43.2 | 71% |
| `rear_shock_defl_static` | -67 | -477 | 610% |
| `rear_spring_defl_static` | -2.4 | 123 | 5,280% |
| `third_spring_defl_static` | 4.7 | 145 | 3,012% |
| `heave_spring_defl_static` | 8.1 | 2.4 | 70% |

The intercept variance is exactly what you'd expect — different aero loads, different surface signatures, different cornering speeds give different baselines. **That's why pooling intercepts is a non-starter.**

### 3.2 Ferrari — Unified (18 setups, includes Algarve+Hockenheim) vs Hockenheim-only (14 setups)

This is a softer comparison: the "unified" model is what you get when you pool tracks today (silently, via the fallback path) and the Hockenheim-only model is the quasi-correct per-track fit.

| Target | Compliance feature | Unified (cross-track) | Hockenheim-only | Notes |
| --- | --- | --- | --- | --- |
| `rear_ride_height` | `inv_rear_spring` | -5,727 | -8,655 | -34% magnitude. Same sign. Both R²>0.98. |
| `rear_ride_height` | `inv_rear_third` | -1,743 | -6,357 | -73% magnitude. Same sign. Same regime. |
| `front_shock_defl_static` | `inv_rear_spring` | +395 | -1,291 | **sign flip — pooling is contaminating the fit** |
| `front_shock_defl_static` | `inv_rear_third` | +654 | absent | term silently re-engaged by pooling — overfit risk |
| `heave_spring_defl_static` | `inv_front_heave` | 138 | absent | feature only fired in pooled mode (Algarve adds the variance) |
| `heave_spring_defl_max` | `inv_front_heave` | -132 | absent (dropped) | pooled model's compliance is ~0; Hockenheim alone uses `inv_rear_spring` and `inv_rear_third` instead |
| `third_spring_defl_static` | `inv_rear_spring` | 6,002 | absent | similarly track-mixed |
| `heave_slider_defl_static` | `inv_front_heave` | 127 | 153 | **only ~17% diff — physics-stable** |

**Pattern:** Compliance terms are *roughly* same-sign and same-order-of-magnitude across tracks for the SAME car when the underlying spring varies enough on both tracks; but feature-selection instability dominates the comparison because Hockenheim has 14 setups vs Laguna 6 and the LOO selector picks different terms each time. Real cross-track invariance can only be assessed by *forcing* the same compliance basis into both fits and reading the slope — which today's selector doesn't do.

### 3.3 Compliance coefficient comparison — minimum 5 cases (audit checklist item)

Direct evidence (Porsche only — Ferrari is unified-vs-Hockenheim, see §3.2 for the soft comparison):

| # | Target | Coefficient | Track A (Algarve) | Track B (Laguna Seca) | % diff | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `heave_spring_defl_static` | `inv_front_heave` | **2,531** | **2,923** | **15.8%** | **physics-invariant (<20%) — POOLABLE** |
| 2 | `rear_spring_defl_max` | `inv_rear_spring` | 0.95 | 1.24 | 30.2% | **borderline** (small-sample noise dominant; both Algarve and Laguna have valid R² but different supporting features) |
| 3 | `heave_spring_defl_max` | `inv_front_heave` | 1,611 | 3,369 | 109% | **track-sensitive** (max compression is event-driven by track bumps; pooling would corrupt) |
| 4 | `rear_spring_defl_max` | `fuel_x_inv_spring` | 26.3 | 72.0 | 174% | **track-sensitive** (mass × compliance × dynamic load — load is track-dependent) |
| 5 | `rear_ride_height` | `inv_rear_third` | -896 | +63.4 | sign flip | **noisy** (Laguna n=6 with 4 features → underdetermined) |
| 6 | `rear_ride_height` | `inv_rear_spring` | 0.47 | 164.4 | 34,632% | **noisy** (small-sample contamination on Laguna) |
| 7 | `front_shock_defl_static` | `fuel_x_inv_third` | -2.5 | -130 | 5,123% | **noisy** (Algarve model `is_calibrated=False`, R²=0.07) |
| 8 | `heave_spring_defl_static` | `rear_pushrod_sq` | 2.3e-05 | 7.1e-04 | 2,964% | **noisy** (term is irrelevant on this output — both coefficients near zero) |

Of 8 paired compliance comparisons:
- 1 physics-invariant (`inv_front_heave` for `heave_spring_defl_static`).
- 1 borderline.
- 2 genuinely track-sensitive (max-deflection terms).
- 4 swamped by small-sample noise (Laguna n=6).

The signal exists but is thin. Without more multi-track data, pooling assertions remain underpowered.

---

## 4. Pooling feasibility per term

Per compliance term in `_UNIVERSAL_POOL`, what does the evidence say?

| Compliance term | Static-defl pooling | Max-defl pooling | RH pooling | Recommended approach |
| --- | --- | --- | --- | --- |
| `inv_front_heave` | **OK** (Porsche 16% diff on `heave_spring_defl_static`; physics: 1/k slope of compliance under fuel + aero is car-property-only when aero is at static reference speed) | **NOT OK** (109% diff on `heave_spring_defl_max` — max events dominated by track bumps) | INSUFFICIENT data | **opt-in pool for static-only, never max** |
| `inv_rear_third` | INSUFFICIENT | INSUFFICIENT | UNSAFE (Porsche sign flip in §3.1) | **never pool today; revisit when more tracks land** |
| `inv_rear_spring` | UNSAFE (Ferrari sign flip §3.2) | borderline (Porsche 30% diff) | UNSAFE (sign flip on Porsche RH) | **never pool today** |
| `inv_od4` (front torsion) | not measurable cross-track today (Ferrari per-track only fits Hockenheim with this feature; Acura/Cadillac don't fit it stably) | n/a | n/a | **never pool — Ferrari/Acura have at most 1 multi-track point** |
| `fuel_x_inv_spring` | UNSAFE (174% diff on Porsche `rear_spring_defl_max`) | UNSAFE | mixed | **never pool** |
| `fuel_x_inv_third` | UNSAFE (5,123% diff on Porsche `front_shock_defl_static`, both fits flagged uncalibrated) | INSUFFICIENT | INSUFFICIENT | **never pool** |
| `inv_front_corner_spring` (GT3) | NO DATA | NO DATA | NO DATA | **n/a** — gated on W7.2 IBT capture |
| `inv_rear_corner_spring` (GT3) | NO DATA | NO DATA | NO DATA | **n/a** — gated on W7.2 IBT capture |
| `fuel_x_inv_front_corner_spring` (GT3) | NO DATA | NO DATA | NO DATA | **n/a** |
| `fuel_x_inv_rear_corner_spring` (GT3) | NO DATA | NO DATA | NO DATA | **n/a** |
| `front_pushrod_sq` | inconclusive (terms dropped by selector on most cars) | inconclusive | inconclusive | **never pool** (linkage geometry depends on pushrod offset, which is car-property — but the data is too sparse to assert invariance) |
| `rear_pushrod_sq` | spurious (Porsche values are ~zero on both tracks → pooling adds noise without signal) | inconclusive | inconclusive | **never pool** |

**Single supportable pooling case today:** `inv_front_heave` for the **static** heave-spring deflection on Porsche, and only because we have 40+6 setups across 2 tracks and the slope agreed within 16%. Even there, the safer move is to NOT pool but instead use the more under-calibrated track's intercept-only fit and borrow the SLOPE from the better-calibrated track. That's the "compliance-only pooling" idea in its cleanest form.

---

## 5. Implementation sketch — `--cross-track-compliance` CLI flag

NOT IMPLEMENTED. This is a design proposal for a future change.

### 5.1 Use case

A user runs `python -m pipeline.produce --car porsche --ibt session_at_imola.ibt …` (or any track NOT in `data/calibration/porsche/`). Today the pipeline either:

1. Falls back to the unified `models.json` (which silently mixes Algarve+Laguna), or
2. Errors out with "no calibration for this track."

The proposed flag turns this implicit pooling into an explicit, **whitelisted** compliance-only pool: borrow the slope coefficients (only on the whitelist of physics-invariant compliance terms) from the best-calibrated peer track for that car, and refit the intercept + non-compliance terms locally on whatever sparse data the new track has.

### 5.2 Proposed CLI surface

```bash
python -m pipeline.produce --car porsche --ibt imola.ibt \
    --cross-track-compliance \
    --pool-source-track algarve     # optional; defaults to highest-n_unique_setups peer track
```

For `auto_calibrate` directly:

```bash
python -m car_model.auto_calibrate --car porsche --track imola \
    --cross-track-compliance \
    --pool-whitelist inv_front_heave   # repeatable; default = the audit's "POOLABLE" list
```

### 5.3 Proposed config key

In `cars.py:CarModel`:

```python
@dataclass
class CalibrationPolicy:
    cross_track_compliance: bool = False
    pool_whitelist: tuple[str, ...] = ("inv_front_heave",)  # static-only, audit §3.3 result
    pool_max_track_age_days: int = 90  # don't borrow from stale peer fits
```

### 5.4 Algorithm sketch

```
def fit_with_cross_track_pool(car, track, points, peer_models):
    # 1. Fit the local model normally (per-track), getting local features F_local.
    local_model = _fit(target, X_local, y_local, ...)

    # 2. For each whitelisted compliance term in F_local that the peer model
    #    also has fitted with R²>0.85 and LOO/train < 5x:
    #    borrow the peer slope, refit only intercept + non-whitelisted terms locally.
    pool_slopes = {
        name: peer_models.coef[name]
        for name in WHITELIST
        if peer_models.has(name)
        and peer_models.r2 >= 0.85
        and peer_models.loo_train_ratio < 5
    }

    # 3. Subtract the borrowed contribution, refit the residual.
    y_residual = y_local - sum(pool_slopes[n] * X_local[n] for n in pool_slopes)
    local_residual_model = _fit(target, X_local_minus_pool, y_residual, ...)

    # 4. Compose: borrowed slopes + locally-fit intercept + locally-fit other terms.
    # 5. Compare LOO of (composed) vs (purely local).  Keep whichever is lower.
    #    This is the same "fallback_pool" comparison pattern from 2026-04-11.
```

Key safety: the **comparison gate** at step 5. If the composed (cross-track-pooled) model has WORSE LOO than the purely local fit — even the noisy single-track 6-setup one — keep local. Pooling is opt-in AND has to earn its keep numerically. This is what the existing `_FRONT_POOL` / `_UNIVERSAL_POOL` `fallback_pool` machinery in `auto_calibrate.py:_fit_from_pool` already does for axis pollution; the pattern is proven to not regress single-track behaviour.

### 5.5 What this does NOT do

- Does NOT pool intercepts (track-dependent by definition; see §3.1 intercept table).
- Does NOT pool max-deflection compliance terms (track-bump-driven).
- Does NOT touch BMW/Sebring (single-track; flag is a no-op).
- Does NOT cross GTP↔GT3 architectures (different feature pools).
- Does NOT replace the calibration gate — gate still requires per-track sufficient data; flag only makes "sufficient" easier to reach by borrowing one slope at a time.

### 5.6 Required new tests

Pre-implementation TDD:

1. `test_cross_track_pool_disabled_by_default` — running without the flag produces today's exact output.
2. `test_cross_track_pool_porsche_imola_borrowing_inv_front_heave` — with the flag and a 3-setup synthetic Imola dataset, the heave-spring slope matches Algarve's within 5%.
3. `test_cross_track_pool_falls_back_when_loo_worse` — when the borrowed peer slope makes LOO worse on local data, the pool is rejected and local model wins.
4. `test_cross_track_pool_never_borrows_max_terms` — `heave_spring_defl_max:inv_front_heave` is never borrowed even when whitelisted (we documented its 109% variance).
5. `test_cross_track_pool_provenance_recorded` — the pooled model's status dict carries `pool_source_track` and the list of borrowed terms for audit.

---

## 6. Bottom line

**Don't pool yet.** The audit shows ONE genuinely promising case (`inv_front_heave` for `heave_spring_defl_static`) and a lot of small-sample noise. Before flipping a flag, what we actually need is:

1. **More multi-track points per car.** Cadillac's Silverstone has 1 unique setup; Acura's Daytona has 1; Ferrari's Algarve has 4. Even Porsche/Laguna at 6 is too sparse to confirm physics invariance on the slopes, since the LOO selector at n=6 caps features at ~2.
2. **Forced common basis.** Run a side-by-side fit where BOTH track datasets are forced to use the same compliance basis (no LOO selection). Then the slopes are directly comparable rather than apples-to-oranges feature-selected fits.

When (1) lands (e.g. a Porsche/Spa varied-spring sweep, or Cadillac Silverstone 5-setup capture), this audit can be re-run and the whitelist promoted from speculative ("`inv_front_heave` only") to evidence-based.

In the meantime, the current pipeline has ALL the safety nets needed if pooling is enabled later: physics-aware front/rear pools (so cross-axis pollution can't return), 3:1 sample-to-feature ratio, LOO/train >10x guard, fallback_pool comparison. The infrastructure to make `--cross-track-compliance` safe is already in place; what's missing is the data to prove it adds value.

---

## 7. References

- `car_model/auto_calibrate.py:99` — original ban comment.
- `car_model/auto_calibrate.py:1340-1408` — `_UNIVERSAL_POOL` definition.
- `car_model/auto_calibrate.py:1418-1451` — front/rear physics-aware pools.
- `car_model/auto_calibrate.py:_fit_from_pool` — fallback comparison gate (the pattern this proposal would extend).
- `data/calibration/porsche/models_algarve.json` & `models_laguna_seca.json` — Porsche cross-track evidence.
- `data/calibration/ferrari/models_hockenheim.json` & `models.json` — Ferrari pooled-vs-per-track evidence.
- `CLAUDE.md` — "Physics-aware feature pools with universal-pool fallback" (2026-04-11) for the proven fallback-comparison pattern.
- `project_inverse_fix_2026_04_11.md` (memory) — bisection/inverse mismatch fix; same comparison pattern.
