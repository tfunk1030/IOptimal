# Calibration Runtime Profile — `fit_models_from_points()` Bottleneck Analysis

**Date:** 2026-04-27
**Scope:** Diagnostic only — no code changes.
**Goal:** Identify hotspots in `car_model/auto_calibrate.py:fit_models_from_points()` and propose optimizations to reduce LOO cross-validation cost.
**Method:** `cProfile` over each calibrated car's `fit_models_from_points()` invocation, driven by `scripts/profile_calibration.py` (no IBT extraction, no JSON I/O — purely the regression fitting hot path).

---

## 1. Per-car runtime profile

Profiles produced via:

```
python scripts/profile_calibration.py
# Saves /tmp/<car>_profile.out per car.
```

CLI wall-clock (`python -m car_model.auto_calibrate --car <car> --refit`, includes JSON I/O,
per-track partition, save):

| Car      | Calibration points | Unique setups | `fit_models_from_points()` only | Full CLI `--refit` |
|----------|-------------------:|--------------:|--------------------------------:|-------------------:|
| BMW      |                 79 |            26 |                          2.34 s |             1.54 s |
| Porsche  |                169 |            46 |                          3.83 s |             4.18 s |
| Ferrari  |                119 |            18 |                          1.29 s |             1.41 s |
| Cadillac |                 23 |             5 |                          0.14 s |             0.32 s |
| Acura    |                 29 |             8 |                          0.19 s |             0.42 s |

Notes on the comparison:

- BMW's CLI is **faster** than the bare-fit profiler run because the profiler instrumentation
  itself adds non-trivial overhead at this iteration count (~99,570 `lstsq` calls captured).
  The CLI numbers are the right reference for "how slow does this feel to a user".
- Porsche's CLI (4.18 s) is meaningfully larger than its fit-only number (3.83 s) because
  Porsche has 2 tracks (Algarve + Laguna Seca) and the per-track partition runs
  `fit_models_from_points()` an additional time for each track group with ≥5 unique setups.
  Algarve dominates (44 of 46 unique setups) so per-track adds roughly +0.35 s on top of
  the pooled fit.
- Cadillac and Acura barely register; their datasets are small enough that the only
  meaningful component is the universal-pool feature scan in `_select_features`.
- JSON I/O (`load_calibration_points` + `save_calibrated_models` + per-track save) is
  **<150 ms** total even for the worst case. It is **not a bottleneck**.

### 1.1 Top-10 cumulative-time hotspots — BMW (n=79 points, 26 unique setups, 2.34 s)

```
ncalls   cumtime  percall  function
     1     2.340    2.340  fit_models_from_points (auto_calibrate.py:1184)
    12     2.331    0.194    _fit_from_pool (auto_calibrate.py:1593)
    24     2.327    0.097      _fit_with_anchor_check (auto_calibrate.py:1536)
    36     2.326    0.065        _fit_one_pool (auto_calibrate.py:1516)
    34     2.269    0.067          _select_features (auto_calibrate.py:925)
 99570     1.685    0.000            np.linalg.lstsq (linalg/_linalg.py:2417)
 99570     0.205    0.000            _commonType (linalg/_linalg.py:189)
103286     0.142    0.000            np.ones (numeric.py:170)
199140     0.098    0.000            _makearray (linalg/_linalg.py:164)
    36     0.032    0.001            _fit (auto_calibrate.py:808)
```

`tottime` ranking (where the CPU actually sits): **`np.linalg.lstsq` 1.029 s (44%) +
`_select_features` Python overhead 0.432 s (18%)** — together ~62 % of total.

### 1.2 Top-10 cumulative-time hotspots — Porsche (n=169, 46 unique setups, 3.83 s)

```
ncalls   cumtime  percall  function
     1     3.828    3.828  fit_models_from_points
    12     3.820    0.318    _fit_from_pool
    24     3.811    0.159      _fit_with_anchor_check
    38     3.811    0.100        _fit_one_pool
    36     3.719    0.103          _select_features
138638     2.816    0.000            np.linalg.lstsq
138638     0.302    0.000            _commonType
141605     0.223    0.000            np.ones
277276     0.142    0.000            _makearray
    37     0.070    0.002            _fit
```

`tottime`: **`lstsq` 1.826 s (48%), `_select_features` Python overhead 0.705 s (18%)**.

### 1.3 Top-10 cumulative-time hotspots — Ferrari (n=119, 18 unique setups, 1.29 s)

```
ncalls   cumtime  percall  function
     1     1.288    1.288  fit_models_from_points
    13     1.281    0.099    _fit_from_pool
    25     1.277    0.051      _fit_with_anchor_check
    39     1.276    0.033        _fit_one_pool
    36     1.236    0.034          _select_features
 56048     0.907    0.000            np.linalg.lstsq
 56048     0.115    0.000            _commonType
 59012     0.083    0.000            np.ones
112096     0.055    0.000            _makearray
    38     0.024    0.001            _fit
```

### 1.4 Top-10 cumulative-time hotspots — Cadillac (n=23, 5 unique setups, 0.14 s)

```
ncalls   cumtime  percall  function
     1     0.141    0.141  fit_models_from_points
    12     0.137    0.011    _fit_from_pool
    24     0.135    0.006      _fit_with_anchor_check
    36     0.135    0.004        _fit_one_pool
    36     0.107    0.003          _select_features
  4790     0.076    0.000            np.linalg.lstsq
    36     0.016    0.000            _pool_to_matrix (auto_calibrate.py:1453)
    38     0.009    0.000            _fit
```

### 1.5 Top-10 cumulative-time hotspots — Acura (n=29, 8 unique setups, 0.19 s)

```
ncalls   cumtime  percall  function
     1     0.188    0.188  fit_models_from_points
    12     0.180    0.015    _fit_from_pool
    21     0.178    0.008      _fit_with_anchor_check
    33     0.178    0.005        _fit_one_pool
    24     0.156    0.007          _select_features
  7764     0.114    0.000            np.linalg.lstsq
    24     0.012    0.000            _pool_to_matrix
    26     0.009    0.000            _fit
```

### 1.6 LOO loop iteration counts

| Car       | unique n | `_select_features` calls | `lstsq` calls | `lstsq`/select call | Tot `lstsq` time | `lstsq` per-call (µs) |
|-----------|---------:|-------------------------:|--------------:|--------------------:|-----------------:|----------------------:|
| BMW       |       26 |                       34 |        99 570 |               2 928 |          1.029 s |                    10 |
| Porsche   |       46 |                       36 |       138 638 |               3 851 |          1.826 s |                    13 |
| Ferrari   |       18 |                       36 |        56 048 |               1 557 |          0.538 s |                    10 |
| Cadillac  |        5 |                       36 |         4 790 |                 133 |          0.041 s |                     9 |
| Acura     |        8 |                       24 |         7 764 |                 324 |          0.063 s |                     8 |

Two distinct loops produce these `lstsq` calls:

1. **Inner LOO inside `_select_features` (`auto_calibrate.py:992`)** — for each greedy
   forward-selection iteration, for each remaining feature trial, run `n_samples`
   leave-one-out solves. This is the **dominant** call site by 2–3 orders of magnitude
   (e.g. BMW: 34 selects × ~22 candidate features × ~26 LOO iters × ~5 outer steps ≈
   the bulk of the 99k count, with early-stop `if best_loo > best_overall_loo * 1.05`
   pruning roughly half).

2. **Outer LOO inside `_fit` (`auto_calibrate.py:845`)** — once the feature subset is
   chosen, `_fit` does an additional `n_samples` LOO solves on the final subset. With
   ~36 outputs per car × n samples each, this is at most ~1 200 calls — under 2 % of
   the inner count.

The fact that **`_select_features` is responsible for >97 % of all `lstsq` calls** is
the central observation of this audit.

### 1.7 JSON I/O time

Negligible. `load_calibration_points` and `save_calibrated_models` together appear at
< 60 ms per car in the CLI wall-clock numbers. They are not in the top-30 of any cProfile
run. Per-track save (Porsche) adds another ~20–30 ms. Not worth optimising.

---

## 2. Bottleneck identification

Two hotspots dominate runtime; together they explain >80 % of `fit_models_from_points()`
wall-clock for every car.

### Hotspot 1 — `_select_features` LOO inner loop (~50–65 % of runtime)

**Location:** `car_model/auto_calibrate.py:925-1022`, specifically the triple-nested
loop at lines 976 → 979 → 989:

```python
for _ in range(budget_remaining):           # outer: greedy add up to budget_remaining
    for idx in remaining:                   # mid:   try each candidate feature
        ...
        for i in range(n_samples):          # inner: LOO leave-one-out
            mask = np.ones(n_samples, dtype=bool)
            mask[i] = False
            b, *_ = np.linalg.lstsq(X_aug[mask], y[mask], rcond=None)
            loo_sq += (y[i] - X_aug[i] @ b) ** 2
```

For BMW (n=26, budget≈10, ~22 features): naive complexity is
`budget × remaining × n × O(p² · n)`. The early-stop reduces this in practice but the
profile shows **99 570 `lstsq` calls** for one `fit_models_from_points()` invocation —
each call rebuilds an (n−1)-row matrix and calls LAPACK from Python.

Per-call overhead matters: `lstsq` itself averages 10 µs, and Python-side wrappers
(`_makearray`, `_commonType`, `np.ones`, `astype`) add another ~7 µs per call. The
actual LAPACK SVD on a ~25×10 matrix takes < 5 µs; the rest is interpretive overhead.

### Hotspot 2 — `_fit` outer LOO + R²/RMSE bookkeeping (~15–25 % of runtime, mostly `lstsq` again)

**Location:** `car_model/auto_calibrate.py:808-918`, lines 844-850:

```python
for i in range(n):
    mask = np.ones(n, dtype=bool)
    mask[i] = False
    X_train, y_train = X_aug[mask], y[mask]
    b, *_ = np.linalg.lstsq(X_train, y_train, rcond=None)
    loo_errors[i] = y[i] - X_aug[i] @ b
```

This duplicates the work `_select_features` already did when it picked the final subset
— if `_select_features` ran the LOO inner loop on the chosen subset (it did, as part
of greedy evaluation), `_fit` immediately recomputes the same LOO RMSE on the same
matrix. The data is in the inner loop's last-iteration state but is thrown away.

This isn't the biggest cost (~36 calls × ~26 LOO ≈ 1 000 `lstsq`) but it is **pure
duplicated work** and a clean win to remove.

### Hotspot 3 — `np.ones`/`hstack`/`mask` allocation per LOO iteration (~5 %)

The `mask = np.ones(n, dtype=bool); mask[i] = False; X_aug[mask]` pattern allocates a
new boolean array and a new `(n-1) × p` matrix on every LOO iteration. For 99 k inner
iterations on BMW this is ~99 k mask allocations + ~99 k slicing copies.
`np.ones` alone is the third-largest cost line (103 286 calls, 0.142 s cumtime on BMW).

---

## 3. Optimization proposals

For each proposal: **change**, **estimated speedup**, **difficulty**, **risk**.
"Speedup" is for the pooled `fit_models_from_points()` runtime on a typical car (BMW).
Risks are explicitly called out where they affect calibration accuracy.

### Proposal A — Closed-form LOO via the hat matrix (PRESS statistic)

**Change.** Replace the inner LOO loop in both `_select_features` and `_fit` with the
PRESS / hat-matrix shortcut. For ordinary least squares with design matrix `X` and
fit `y_hat = X β`, the leave-one-out residual is

```
e_i^LOO = e_i / (1 − h_ii)        where  H = X (X^T X)^{-1} X^T
PRESS   = Σ (e_i^LOO)^2
LOO_RMSE = sqrt(PRESS / n)
```

This requires **one** `lstsq` and one diagonal-of-hat-matrix computation per feature
subset, replacing `n` `lstsq` calls. With `n=26` BMW samples, **that is a ~26× speedup
for the inner loop** — and `_select_features` is currently >50 % of total time.

Implementation sketch (per feature subset):
```python
beta, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
y_pred  = X_aug @ beta
resid   = y - y_pred
# Hat diagonal: h_ii = X_i (X^T X)^{-1} X_i^T
XtX_inv = np.linalg.pinv(X_aug.T @ X_aug)
h_diag  = np.einsum('ij,jk,ik->i', X_aug, XtX_inv, X_aug)
loo_resid = resid / np.clip(1.0 - h_diag, 1e-9, None)
loo_rmse  = float(np.sqrt(np.mean(loo_resid ** 2)))
```

**Estimated speedup:** **5–10× on BMW/Porsche/Ferrari** total `fit_models_from_points()`
wall-clock (driven by replacing the >97 % of `lstsq` calls inside `_select_features`).
At BMW's scale this turns 2.34 s into ~0.3–0.5 s. For Cadillac/Acura the absolute time
is already small.

**Difficulty:** **Low–medium.** The math is textbook; numpy already exposes
`np.linalg.pinv` and broadcast einsum. The function-shape change is small (replace
inner `for i in range(n_samples)` block in two places).

**Risk to result quality:**
- **Numerical:** the closed-form PRESS is *mathematically equivalent* to the loop
  result for OLS, *provided* `X^T X` is well-conditioned. For nearly-singular design
  matrices `1 − h_ii` can be ~0 and divide-by-near-zero produces astronomical "LOO
  residuals". This is the same pathology that triggers our existing
  "LOO/train > 10×" guard in `_fit`, but the closed form will surface it more
  aggressively.
- **Mitigation:** clip `1 − h_ii` to ≥ 1e-9 (snippet above), and short-circuit to the
  iterative loop when `cond(X^T X) > 1e10` or `min(1 − h_ii) < 1e-6`. The fallback path
  preserves bit-exact behaviour for borderline cases. With these guards and the
  existing R²/LOO-ratio guards in `_fit`, calibrated outputs should agree to within
  floating-point noise on the existing fixtures.
- **Required validation before shipping:** rerun all 5 cars and check that
  `loo_rmse`, `r_squared`, and `is_calibrated` flags match the iterative loop within
  1e-6 on every fitted model. The setup-regression fixtures
  (`tests/fixtures/baselines/*_baseline.sto`) provide a mechanical end-to-end check
  that the produced setups don't drift.

### Proposal B — Block-LU rank-1 LOO update (alternative to A)

**Change.** Rather than recompute `(X^T X)^{-1}` from scratch for each LOO row, use
the Sherman–Morrison rank-1 update to back out one row's contribution.

**Estimated speedup:** Similar order-of-magnitude to Proposal A (~5–10×). Can be
slightly faster than A for very small matrices because it avoids the einsum.

**Difficulty:** **Medium.** More complex to implement correctly; failure modes are
subtler than A's hat-matrix approach. Probably not worth pursuing if A ships first —
A is already adequate.

**Risk:** Same numerical concerns as A, with the additional risk of accumulating
floating-point drift across rank-1 updates if not refactored carefully. **Recommend
skipping in favour of A.**

### Proposal C — Cache the design matrix per feature subset and avoid re-`np.ones` allocation

**Change.** Inside `_select_features`, the line
`X_aug = np.hstack([ones, X[:, trial]])` rebuilds a `(n × p+1)` array on every
candidate-feature trial. For a fixed `selected` prefix we can pre-build
`X_aug_selected` once per outer iteration and append a single column per trial via
`np.column_stack`.

Bigger win: hoist the `ones = np.ones((n_samples, 1))` outside both loops — currently
allocated 103 286 times for BMW.

```python
# at top of _select_features after computing n_samples:
ones_col = np.ones((n_samples, 1))    # ALLOCATE ONCE
...
X_aug = np.hstack([ones_col, X_trial])   # uses cached ones_col
```

**Estimated speedup:** **5–10 %** on its own (the `np.ones` line is 6 % of BMW total
time, and hstack/`_makearray` overhead is another 4 %). Compounds with A but doesn't
require it.

**Difficulty:** **Low.** Pure mechanical change, ~10 lines.

**Risk:** Very low. Pure allocation refactor, no numerical change.

### Proposal D — Skip duplicated outer LOO in `_fit` after `_select_features`

**Change.** When `_fit` is called from `_fit_one_pool` (i.e. immediately after
`_select_features` already evaluated this exact subset), pass the already-computed
LOO RMSE in instead of recomputing it. Add an optional `loo_rmse_known: float | None`
kwarg to `_fit`.

**Estimated speedup:** **3–5 %** on BMW/Porsche. `_fit`'s outer LOO is ~36 calls × n
samples, ≈ 1 000–1 700 `lstsq` per car. Real money is small but the change is
trivially safe.

**Difficulty:** **Low.** Add a kwarg, skip the loop when provided. Direct callers of
`_fit` that aren't inside `_fit_one_pool` (e.g. `models.torsion_bar_turns =
_fit(...)` at line 1688) keep the existing behaviour by passing `loo_rmse_known=None`.

**Risk:** Very low. Pure de-duplication. The LOO RMSE is computed identically; we
just don't redo it.

### Proposal E — Tighten the early-stop in `_select_features`

**Change.** Today's loop continues for `budget_remaining` iterations and only stops
when `best_loo > best_overall_loo * 1.05`. Tighten that gate to `* 1.02` and add a
no-improvement counter that breaks after 2 successive iterations of degrading LOO.

**Estimated speedup:** **5–15 %**, depending on car. Highly dataset-dependent: cars
where the first 3–4 features capture all the signal benefit most.

**Difficulty:** **Low.**

**Risk:** **Medium.** Risks under-fitting models that genuinely benefit from a 5th or
6th feature. **Should be validated against the calibration_report.md numbers** —
specifically the LOO/train ratios for each fitted model. The existing 1.05 threshold
was tuned (presumably empirically) against the current dataset; tightening it is a
quality knob, not a free win. **Lower priority than A / D.**

### Proposal F — Cache `cars.get_car()` and feature-pool construction across `_fit_from_pool` calls

**Change.** Every `_fit_from_pool` call rebuilds the full `_UNIVERSAL_POOL` list and
re-extracts columns. The pool is dataset-wide and identical across all 12 outputs;
build it once per `fit_models_from_points` invocation. Already mostly done in current
code — but `_pool_to_matrix` is called once per output × per fit attempt
(augmented + real-only + fallback variants). For BMW this is ~36 calls each rebuilding
a Python list of arrays.

**Estimated speedup:** **2–5 %.** Marginal.

**Difficulty:** **Low.**

**Risk:** Very low.

### Proposal G — Run per-output fits in parallel (`concurrent.futures.ProcessPoolExecutor`)

**Change.** The 12 output-model fits are mutually independent. Dispatch them across
processes.

**Estimated speedup:** **3–4×** on a 4-core machine, in theory. In practice subject to
pickling overhead and numpy's internal threading already saturating cores at certain
LAPACK calls.

**Difficulty:** **Medium.** Need to pickle car objects, virtual anchors, and X
matrices; need to keep determinism (LOO ordering matters for selected feature sets in
edge cases — verify this).

**Risk:** **Medium.** Determinism risk if any RNG path is involved (currently I see
none, but worth auditing). Also adds operational complexity (process pool startup
~100–200 ms — eats into the win for Cadillac/Acura).

**Lower priority than A.** Multiprocessing is a much bigger code change than the
hat-matrix shortcut, and at BMW/Porsche scale (2–4 s wall-clock), the absolute
savings are modest. **Re-evaluate after A is shipped** — if total runtime is already
sub-second, this proposal becomes premature optimisation.

---

## 4. Recommendations — prioritized

The two cars where calibration time is user-visible are Porsche (3.83 s) and BMW
(2.34 s). Cadillac, Acura, and Ferrari are already fast enough that nobody is waiting
on them. The recommendations below are ordered by `value × risk_inverse` and are
designed to compose:

### Tier 1 — ship first (single PR, ~1 day)

1. **Proposal A — Hat-matrix LOO in `_select_features` and `_fit`.** Single biggest
   win (~5–10× on hot cars), well-understood math, easy to validate against the
   existing fixtures. **Add the conditioning guard** so we fall back to the iterative
   loop when `min(1 − h_ii) < 1e-6` or `cond(X^T X) > 1e10`. This preserves correctness
   on borderline-degenerate fits.
2. **Proposal D — pass `loo_rmse_known` from `_select_features` into `_fit`.** Free
   3–5 % on top of A, trivially safe. Ships in the same PR as A because both touch
   `_fit`.
3. **Proposal C — hoist `np.ones((n_samples, 1))` outside the `_select_features`
   loops; cache `X_aug_selected` for greedy prefixes.** 5–10 % on top, very low
   risk, ~20 lines.

After Tier 1: expected BMW `fit_models_from_points()` ~0.3–0.5 s; Porsche ~0.5–0.8 s.

### Tier 2 — only if Tier 1 isn't enough

4. **Proposal F — pool/`get_car` caching cleanup.** Marginal 2–5 % win, low risk.
   Worth bundling into a follow-up cleanup PR but not urgent.
5. **Proposal E — tighten the `_select_features` early-stop threshold.** Only after
   running an offline study showing it doesn't degrade `r_squared` or LOO/train
   ratios on the existing 5 cars. **Treat as a quality knob, not a perf knob.**

### Tier 3 — defer indefinitely

6. **Proposal B — Sherman–Morrison rank-1 LOO updates.** Same speedup as A with more
   complexity. Skip unless A turns out to be numerically unstable on real data.
7. **Proposal G — per-output multiprocessing.** Material code change with operational
   cost; revisit only if Tier 1 fails to bring `fit_models_from_points()` under 1 s on
   the worst car.

### Validation gate (before merging Tier 1)

Before shipping Proposal A:

- [ ] Re-fit all 5 calibrated cars; for each fitted model compare
  `(r_squared, rmse, loo_rmse, is_calibrated)` against the previous-version
  `models.json` to within 1e-6 absolute / 1e-3 relative.
- [ ] Run `pytest tests/test_setup_regression.py` (the BMW + Porsche end-to-end
  fixtures) and confirm produced `.sto` files match the baselines bit-for-bit OR
  document the diff and bless it as expected.
- [ ] Run `python -m car_model.auto_calibrate --car porsche --refit` and check the
  per-track files (`models_algarve.json`, `models_laguna_seca.json`) regenerate with
  the same model selections.

If any of those fail, the conditioning guard needs widening or we fall back to
Proposal D + C alone (still ~10–15 % win, no numerical risk).

---

## Reference data

- Profiles saved to `/tmp/{bmw,porsche,ferrari,cadillac,acura}_profile.out` during the
  audit run. Re-generate with `python scripts/profile_calibration.py`.
- Profiling harness: `scripts/profile_calibration.py` (added by this audit).
- Code under review: `car_model/auto_calibrate.py` lines **808–918** (`_fit`),
  **925–1022** (`_select_features`), **1184+** (`fit_models_from_points`),
  **1453–1591** (helpers `_pool_to_matrix`, `_fit_one_pool`, `_fit_with_anchor_check`),
  **1593–1640** (`_fit_from_pool`).
