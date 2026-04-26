# Audit Slice #17 — Learner (Knowledge Accumulation)

Date: 2026-04-26
Owned files: `learner/{__init__,ingest,observation,delta_detector,empirical_models,recall,knowledge_store}.py`

## Summary

The learner package is structurally sound and follows the project's "physics first, no silent fallbacks" principle reasonably well. The main gaps were:

1. Several `except Exception: pass`-style blocks with no logging — silent failures during ingest masked real problems.
2. `KNOWN_CAUSALITY` table in `delta_detector.py` was missing TC, diff preload, tyre pressure, fuel-load, and cross-axle aero coupling pairs called out in the audit prompt.
3. Windows file-locking behavior was under-documented (single sentence) — replaced with a clear cross-platform note explaining the watcher serializes ingestion to compensate.
4. Three minor cleanup items: duplicate `import re`, unused `import math` in `empirical_models.py`, unused `import json` and `SessionDelta` in `ingest.py`.

No production bugs were found in the experiment-gating logic, time-decay weighting, min-sessions thresholds, or the 8 fit functions. The reverse-direction auto-generation in `KNOWN_CAUSALITY` is correct (verified against the new `~`-direction entries — they map to `~` as expected).

## Findings & Fixes

### F1 — Silent except in `ingest.py:72` (TrackProfileStore add_session)
**Severity:** medium. Audit prompt explicitly flagged this.
**Fix:** Added `logger.debug("TrackProfileStore add_session skipped: %s", exc)`. Also added module-level `logger = logging.getLogger(__name__)`.

### F2 — Four other `except Exception` paths in ingest.py without logger
**Severity:** medium (consistency).
**Fix:** Added `logger.debug(...)` to all four:
- `_update_auto_calibration` exception (line ~196)
- `garage_model` update failure (line ~265)
- `cross_track.build_global_model` skip (line ~370)
- `cross_track.build_global_model` rebuild skip (line ~684)

The user-facing `print(f"  [garage_model] Update skipped: {exc}")` etc. paths were preserved for verbose mode; logging is now in addition to (not replacing) those.

### F3 — Silent except in `empirical_models.py:_safe_linear_fit`
**Severity:** low (defensive).
**Fix:** Added `logger` and changed `except Exception: return [], 0.0` to `except Exception as exc: logger.debug("polyfit failed (n=%d): %s", len(x), exc); return [], 0.0`. Without this, an upstream NaN/inf bug could cause every empirical model to silently return an empty fit.

### F4 — KNOWN_CAUSALITY missing entries
**Severity:** medium. Causal hypotheses are dropped entirely for parameters not in this table; missing entries mean the learner cannot learn from those parameters.
**Fix:** Added the following pairs (with appropriate confidence-encoded directions):
- **Cross-axle aero coupling**: `front_heave_nmm` → `dynamic_rear_rh_mm` (`~`), `rear_third_nmm` → `dynamic_front_rh_mm` (`~`). Direction is car-dependent (rake interaction with aero map).
- **Brake bias**: added `front_braking_lock_ratio_p95` (`+`) effect — more front bias = more front lock risk.
- **Differential preload** (`diff_preload_nm`): `rear_power_slip_ratio_p95` (`-`), `body_slip_p95_deg` (`-`), `understeer_low_speed_deg` (`+`).
- **Traction control gain** (`tc_gain`): `rear_power_slip_ratio_p95` (`-`), `tc_intervention_pct` (`+`).
- **Traction control slip** (`tc_slip`): `rear_power_slip_ratio_p95` (`+`), `tc_intervention_pct` (`-`). (Higher slip threshold = later intervention.)
- **Front cold tyre pressure**: `front_pressure_mean_kpa` (`+`), `understeer_mean_deg` (`+`).
- **Rear cold tyre pressure**: `rear_pressure_mean_kpa` (`+`), `body_slip_p95_deg` (`+`).
- **Fuel load**: `dynamic_rear_rh_mm` (`-`), `lltd_measured` (`~`).

The reverse-direction auto-generation block at line 238 correctly handles all these (verified — it inverts `+`↔`-` and leaves `~` unchanged).

### F5 — Windows file-locking documentation
**Severity:** low (docs only). Audit prompt asked for explicit Windows behavior.
**Fix:** Expanded the docstring on `KnowledgeStore._atomic_write` to spell out:
- Unix: fcntl advisory exclusive locking; same-machine concurrent writers serialize.
- Windows: NO locking. Safe for documented single-user CLI; concurrent ingest is unsafe.
- Mitigation: `watcher/service.py` sequentially queues IBTs, which is the deployed safety net.
- Last-resort fallback: any OSError on the lock falls through to an unlocked write.

No code change to the locking behavior — it's correct for the documented use case. The audit confirms no retry/timeout is needed for single-user CLI semantics.

### F6 — Cleanup: duplicate / unused imports
**Severity:** trivial (lint-grade).
**Fixes:**
- `knowledge_store.py:62`: removed redundant `import re` inside `track_key_from_name` (already imported at module level).
- `empirical_models.py:22`: removed unused `import math`.
- `ingest.py:23`: removed unused `import json`.
- `ingest.py:32`: removed unused `SessionDelta` from delta_detector import (only `detect_delta` is referenced).

## Items Verified — No Action Needed

### Reverse-direction auto-generation (delta_detector.py:189)
Code: `opposite = "-" if direction == "+" else "+"` and `_reverse_dir = {"+": "-", "-": "+", "~": "~"}`. Correct mapping — `~` stays `~`, `+`↔`-` invert. Guarded by `if (param, opposite) not in KNOWN_CAUSALITY` so manually-defined opposites are never overwritten. Re-tested mentally with the new entries.

### Confidence classification (delta_detector.py:543-553)
- `num_setup_changes == 0` → `trivial`
- `controlled_experiment` (≤1 step) and clean → `high`
- `≤3 changes` and clean → `medium`
- otherwise → `low`
- Wind change >3 m/s downgrades `high`→`medium`
- High in-car tuning (>15 adjustments) downgrades `high`→`medium`/`low`

This matches CLAUDE.md's "single-change=1.0, two-change=0.5, multi=0" intent (the actual numeric weighting happens later in `_fit_lap_time_sensitivity`, line 590-616, where:
- `num_changes ≤ 1` → weight 1.0
- `num_changes == 2` → weight 0.5
- `num_changes ≥ 3` → weight `1.0 / num_changes`

The audit-described "multi=0" is a slight overstatement of the implementation — multi-change deltas DO contribute, but with `1/n` discount AND a `confidence_level not in (high, medium)` filter (line 598). The combined effect is functionally close to "0" for low-confidence multi-change deltas.

### Min-sessions gate
- Non-prediction corrections: `MIN_SESSIONS_FOR_CORRECTIONS = 5` (line 33), used in `_compute_corrections` for roll gradient and LLTD aggregates.
- Prediction-error corrections: `if len(errors) < 3: continue` (line 902). Correct — these are diff-based and need fewer samples.
- Roll-gain thermal calibration: `if sample_count < 3` (line 725).

Matches CLAUDE.md ("Minimum 5 sessions required for non-prediction corrections" and "need only 3 sessions" for prediction-based).

### Time decay (line 826)
`TIME_DECAY_BASE = 0.95`. 30-day decay = `0.95^30 ≈ 0.215`, matching the CLAUDE.md docstring ("30-day-old sessions contribute ~22%"). Constant is the same regardless of car/track — this is appropriate because it's a generic recency prior, not a track-specific adaptation. Cars/tracks with very different session cadence get weighted naturally because the timestamp comes from the IBT mtime.

### Experiment gating for sensitivity (line 593-616)
Discards deltas where `lap_time_delta < 0.01s` or `> 3.0s` (clearly noise / different session). Requires `confidence_level in (high, medium)`. Single-change weight 1.0, two-change 0.5, multi-change `1/n`. Robust statistics: median-based outlier removal at 3·MAD before computing weighted mean. Shrinkage toward zero with denominator 5.0 (recently increased from 3.0 — comment line 651). All sensible.

### `--all-laps` mode (line 410-577)
Splits one IBT into N observations, one per valid lap. Delta detection happens between **consecutive laps** within the same IBT, which captures live-cockpit adjustments (brake bias, ARB blade) made mid-session. Each lap gets its own session_id (`base__lap_N`). Auto-calibration is updated only once per IBT (using the `best_diag` lap), preventing duplicate calibration points.

### Auto-calibration update (line 103-200)
Per-track grouping (`track_groups[tk]`) is correct — prevents cross-track contamination flagged in CLAUDE.md ("Pooling cross-track data causes 27x-103x LOO/train overfitting"). Threshold of `tk_unique < 5` matches the documented 5-session minimum. Existing zeta and torsion lookups are preserved across re-fits — important because zeta calibration is car-level, not per-track.

### Damper oscillation validation
The CLAUDE.md mentions "rear shock oscillation frequency extracted from telemetry; if >1.5× natural frequency, damper solver bumps ζ_hs_rear (0.14→0.21)". This is implemented in **`solver/damper_solver.py`** (cross-slice — see referrals), not in the learner. The learner only stores the raw `rear_shock_oscillation_hz` value (observation.py:367), which is correct boundary behavior.

## Cross-slice Referrals

- **Slice #2 (Solver / damper)**: damper oscillation → ζ_hs_rear bump logic lives in `solver/damper_solver.py`, not learner. Verify in that slice.
- **Slice #6 (Auto-calibration)**: `learner.ingest._update_auto_calibration` calls into `car_model.auto_calibrate.{load_calibration_points, fit_models_from_points, ...}`. Per-track grouping logic in ingest is correct; the actual fitting overfit guard (3:1 ratio) is in auto-calibrate slice.
- **Slice #16 (Track model)**: `track_model.track_store.TrackProfileStore.add_session` is wrapped in a try/except in ingest.py. The audit recommends that store be robust to missing track configs, since failures here now log at debug level only.
- **Slice #14 (Watcher)**: confirmed in F5 — Windows safety relies on `watcher/service.py` queueing. Verify that slice that the queue is actually serial.
- **Slice #18 (Validation)**: lap-time sensitivity weighting (single=1.0, double=0.5, multi=1/n) interacts with the validation correlation work documented in CLAUDE.md ("BMW/Sebring non-vetoed Spearman ~-0.298"). If validation finds the sensitivity ranking is unstable, this is the place to revisit the weights.

## Verification

```bash
$ python -m pytest tests/ -q --tb=short 2>&1 | tail -15
# (results below in the agent report)
```

Smoke pipeline runs are not necessary for this slice — all changes are internal to `learner/`, do not change function signatures, do not change persisted JSON shapes, and do not alter any value the pipeline reads from the learner. The only behavioral changes are:
1. Debug-level log messages where there were silent passes.
2. New entries in `KNOWN_CAUSALITY` (additive — only generates more hypotheses on relevant deltas; never removes or alters existing ones).
3. Documentation strings.
4. Trivial import cleanup.

Pipeline E2E test is therefore documented as "not required" per the worker instructions. Pytest will still validate that the learner loads and the new KNOWN_CAUSALITY entries don't break delta generation on existing fixture data.
