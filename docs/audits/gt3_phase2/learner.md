# GT3 Phase 2 Audit — Learner Unit

## Scope

Files audited:

- `learner/empirical_models.py` — empirical model fitting (heave/third variance, m_eff, prediction error feedback)
- `learner/observation.py` — Observation dataclass + `build_observation()` schema
- `learner/delta_detector.py` — `STEP_GROUPS`, `KNOWN_CAUSALITY`, `_classify_setup_significance`, `_classify_effect_significance`
- `learner/knowledge_store.py` — JSON persistence, indexing
- `learner/recall.py` — query interface
- `learner/ingest.py` — CLI orchestrator + `_run_analyzer`, `ingest_ibt`, `ingest_all_laps`, `rebuild_track_learnings`
- `learner/setup_clusters.py` — cluster centers / spreads using a fixed parameter list
- `learner/cross_track.py` — cross-track aggregator (drive-by check; m_eff fit references heave)
- `learner/sanity.py` — lap-time gating (no heave coupling; mentioned for completeness)

Out of scope (referenced only): `learner/__main__.py`, `learner/envelope.py`, `learner/report_section.py`.

## Summary table

| # | File:Line | Severity | One-line summary |
|---|-----------|----------|---------------------|
| 1 | `learner/delta_detector.py:25-44` | BLOCKER | `STEP_GROUPS["step2_heave"]` will never trip for GT3; corner-spring deltas land in `step3_springs` against fields a GT3 car will never set (`torsion_bar_od_mm`, `rear_spring_nmm`); GT3 per-corner spring rates are unattributed. |
| 2 | `learner/delta_detector.py:79-185` | BLOCKER | `KNOWN_CAUSALITY` has zero entries for GT3 corner springs / bump rubber gap / splitter height. All GT3 spring deltas drop to "no known causal link → skip entirely" (line 478) and produce zero hypotheses. |
| 3 | `learner/empirical_models.py:284-337` | BLOCKER | `_fit_heave_to_variance()` and `_fit_third_to_variance()` will silently produce zero-sample fits for GT3 (no `front_heave_nmm` / `rear_third_nmm` in setup). The two corresponding GT3 relationships (front/rear corner spring → RH variance) are never fitted. |
| 4 | `learner/empirical_models.py:794-848` | BLOCKER | `_compute_corrections()` m_eff calculation reads only `front_heave_nmm`. For GT3 the call chain returns 0 every iteration, contributing nothing. There is no GT3 m_eff path using `front_corner_spring_nmm` (parallel-corner-spring stiffness ≈ 2 × per-corner). |
| 5 | `learner/observation.py:163-249` | BLOCKER | `setup` dict in `build_observation()` hard-codes `front_heave_nmm`, `rear_third_nmm`, `torsion_bar_od_mm`, `rear_spring_nmm` from `CurrentSetup`. There is no GT3 path that populates `front_corner_spring_nmm`, `rear_corner_spring_nmm`, `front_bump_rubber_gap_mm`, `rear_bump_rubber_gap_mm`, `splitter_height_mm`. Downstream consumers (delta_detector, empirical_models, setup_clusters) read those exact keys. |
| 6 | `learner/empirical_models.py:944-961` | BLOCKER | `PREDICTION_METRICS` has no entries for GT3-only signals (e.g. `splitter_scrape_events`, `front_bump_rubber_contact_pct`). Cars whose primary RH-floor failure mode is splitter scrape will accumulate no prediction-correction history. |
| 7 | `learner/setup_clusters.py:24-39` | BLOCKER | `DEFAULT_SETUP_PARAMETERS` includes `front_heave_nmm`, `rear_third_nmm`, `front_torsion_od_mm`, `rear_spring_nmm`. For GT3 cars these will all read `None` via `_extract_value`, leaving the cluster center built from only `pushrod`, `arb_blade`, `camber`, `toe`, `brake_bias`, `diff_preload` — silently degenerate cluster geometry. No GT3 coverage of corner springs, bump rubber, splitter height. |
| 8 | `learner/cross_track.py:160-165` | DEGRADED | Cross-track m_eff aggregation reads `setup["front_heave_nmm"]` and skips when zero. GT3 cars will produce zero-sample cross-track m_eff with no warning, masking a calibration deficit. |
| 9 | `learner/delta_detector.py:46-73` | DEGRADED | `EFFECT_METRICS["platform"]` references `front_heave_defl_p99_mm`, `front_heave_travel_used_pct`, `heave_bottoming_events_*`. For GT3 these telemetry keys are physically meaningless (no heave damper). They will read 0 from observation defaults and contribute "noise" classification — harmless functionally but confuses the per-axle attribution story. |
| 10 | `learner/delta_detector.py:272-298` | DEGRADED | `_classify_setup_significance` only knows GTP-era keys (`front_heave_nmm`, `rear_third_nmm`, `torsion_bar_od_mm`, `rear_spring_nmm`). GT3 corner-spring changes fall through to `return "minor"` for every delta — both 5 N/mm and 50 N/mm changes are treated identically. |
| 11 | `learner/delta_detector.py:301-320` | DEGRADED | `_classify_effect_significance` thresholds dictionary lacks `splitter_scrape_events`, `front_bump_rubber_contact_pct`. New GT3 bottoming proxies fall back to percentage-only classification. |
| 12 | `learner/ingest.py:758-759` | DEGRADED | `_generate_insights` `params_to_track` list hardcodes `front_heave_nmm`, `rear_third_nmm`. For GT3 these never change → no setup trends ever surface. No GT3 corner-spring or bump-rubber trend tracking. |
| 13 | `learner/observation.py:50-66` | DEGRADED | Telemetry-key documentation comment lists heave-only fields (`front_heave_defl_p99_mm`, `front_heave_travel_used_pct`, etc). Documentation drift — no documented GT3-architecture telemetry. |
| 14 | `learner/empirical_models.py:289` | DEGRADED | The `var > 0` check in `_fit_heave_to_variance` (and `_fit_third_to_variance`) means GT3 cars with `front_heave_nmm = 0/None` are silently dropped. The function emits no log. A new car class producing zero samples should at least warn once per fit. |
| 15 | `learner/observation.py:213-249` | DEGRADED | Damper struct populates 4 corners (lf/rf/lr/rr) by duplicating per-axle values (front/rear ls_comp). For GTP per-corner cars this preserves identity; for GT3 cars (per-axle exposure per `docs/gt3_session_info_schema.md`) the lf/rf duplication is correct but the downstream `_extract_damper_flat` would still produce sensible per-axle averages. Worth confirming when GT3 dampers are wired through `CurrentSetup`. Not a current-day bug because GT3 `CurrentSetup` fields have not been populated yet. |
| 16 | `learner/recall.py:189-203` | COSMETIC | `most_impactful_parameters` only ever surfaces what `_fit_lap_time_sensitivity` discovered. With GT3 corner-spring keys never appearing in deltas (because of #1/#2), the most-impactful list will systematically under-represent springs. Documentation note for recall consumers. |

## Findings

### BLOCKER #1 — STEP_GROUPS leaks GTP-only step names

`learner/delta_detector.py:25-44`:

```python
STEP_GROUPS = {
    "step1_rake": ["front_rh_static", "rear_rh_static", "front_pushrod", "rear_pushrod"],
    "step2_heave": ["front_heave_nmm", "rear_third_nmm"],
    "step3_springs": ["torsion_bar_od_mm", "rear_spring_nmm"],
    ...
}
```

For a GT3 car (`SuspensionArchitecture.GT3_COIL_4WHEEL`):
- `step2_heave` is N/A (per `solver/heave_solver.py:HeaveSolution.null()`).
- `step3_springs` keys (`torsion_bar_od_mm`, `rear_spring_nmm`) are **not present** in the GT3 setup. GT3 uses per-corner coil springs at all 4 wheels — see `docs/gt3_per_car_spec.md` and `docs/gt3_session_info_schema.md` (each corner has `SpringRate`).
- `_find_step_group()` (line 339) will return `"other"` for every GT3 spring delta, miscategorizing all spring changes.

**Fix shape (concrete):** make `STEP_GROUPS` car-architecture-aware:

```python
def step_groups_for_arch(arch: SuspensionArchitecture) -> dict[str, list[str]]:
    base = {
        "step1_rake": ["front_rh_static", "rear_rh_static",
                       "front_pushrod", "rear_pushrod"],
        "step4_arb":  ["front_arb_size", "front_arb_blade",
                       "rear_arb_size", "rear_arb_blade"],
        "step5_geometry": ["front_camber_deg", "rear_camber_deg",
                            "front_toe_mm", "rear_toe_mm"],
        "step6_dampers": [],
        "aero":  ["wing"],
        "other": ["fuel_l", "brake_bias_pct"],
    }
    if arch.has_heave_third:  # GTP
        base["step2_heave"]   = ["front_heave_nmm", "rear_third_nmm"]
        base["step3_springs"] = ["torsion_bar_od_mm", "rear_spring_nmm"]
    elif arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
        base["step3_corner_combined"] = [
            "front_corner_spring_nmm", "rear_corner_spring_nmm",
            "front_bump_rubber_gap_mm", "rear_bump_rubber_gap_mm",
            "splitter_height_mm",
        ]
    return base
```

`detect_delta()` already takes both observations — pass the architecture from `CarModel.suspension_arch` (look up via `car_model.cars.get_car(obs.car).suspension_arch`).

### BLOCKER #2 — KNOWN_CAUSALITY missing every GT3 spring-step entry

`learner/delta_detector.py:79-185` covers GTP causality only. The detector at line 478 (`if not known: continue`) **drops every delta where the parameter has no entry**, so GT3 corner-spring deltas produce zero hypotheses → `delta_result.hypotheses` is empty → empirical learning loop never receives positive signal for GT3 spring tuning.

**Required new entries** (key tuple + effect list + physical justification):

| Key | Effect entry (metric, direction) | Physical justification |
|---|---|---|
| `("front_corner_spring_nmm", "+")` | `("front_rh_std_mm", "-")` | Stiffer corner spring → smaller front-RH oscillation under aero pulse and bumps. |
| `("front_corner_spring_nmm", "+")` | `("front_dominant_freq_hz", "+")` | f_n ∝ √(k/m); stiffer raises natural frequency. |
| `("front_corner_spring_nmm", "+")` | `("front_rh_settle_time_ms", "-")` | Stiffer spring at fixed ζ settles faster (1/ω_n damped). |
| `("front_corner_spring_nmm", "+")` | `("front_shock_vel_p95_mps", "-")` | Less compliance → less travel velocity through bumps. |
| `("front_corner_spring_nmm", "+")` | `("roll_gradient_deg_per_g", "-")` | Stiffer front corner spring contributes to front roll stiffness; total chassis roll decreases. |
| `("front_corner_spring_nmm", "+")` | `("understeer_mean_deg", "+")` | Front roll-stiffness up, more LLTD to front, front loses peak grip first → more understeer (steady-state). |
| `("front_corner_spring_nmm", "+")` | `("front_bump_rubber_contact_pct", "-")` | Stiffer spring keeps the corner off the bump rubber for the same vertical load. |
| `("front_corner_spring_nmm", "+")` | `("splitter_scrape_events", "-")` | Less front travel → splitter scrapes less. |
| `("rear_corner_spring_nmm", "+")` | `("rear_rh_std_mm", "-")` | Same physics, rear axle. |
| `("rear_corner_spring_nmm", "+")` | `("rear_dominant_freq_hz", "+")` | Rear natural frequency rises. |
| `("rear_corner_spring_nmm", "+")` | `("rear_rh_settle_time_ms", "-")` | Stiffer rear settles faster. |
| `("rear_corner_spring_nmm", "+")` | `("rear_shock_vel_p95_mps", "-")` | Less rear compliance → less travel velocity. |
| `("rear_corner_spring_nmm", "+")` | `("roll_gradient_deg_per_g", "-")` | Rear roll stiffness contributes to total roll resistance. |
| `("rear_corner_spring_nmm", "+")` | `("body_slip_p95_deg", "-")` | Stiffer rear → less rear roll compliance → less terminal oversteer. |
| `("rear_corner_spring_nmm", "+")` | `("understeer_mean_deg", "-")` | Stiffer rear shifts roll bias forward → less understeer (relative). |
| `("rear_corner_spring_nmm", "+")` | `("rear_bump_rubber_contact_pct", "-")` | Same as front. |
| `("front_bump_rubber_gap_mm", "+")` | `("front_bump_rubber_contact_pct", "-")` | Larger gap → bump rubber engaged less often. |
| `("front_bump_rubber_gap_mm", "+")` | `("front_rh_std_mm", "+")` | Removing the secondary spring stiffness raises platform variance under high aero. |
| `("front_bump_rubber_gap_mm", "+")` | `("splitter_scrape_events", "+")` | Less progressive bump support → more splitter floor strikes. |
| `("rear_bump_rubber_gap_mm", "+")` | `("rear_bump_rubber_contact_pct", "-")` | Same physics, rear. |
| `("rear_bump_rubber_gap_mm", "+")` | `("rear_rh_std_mm", "+")` | Larger gap removes secondary stiffness; rear RH varies more. |
| `("splitter_height_mm", "+")` | `("splitter_scrape_events", "-")` | Higher splitter perch → more clearance to floor. |
| `("splitter_height_mm", "+")` | `("front_downforce_pct", "-")` | Splitter higher off ground reduces underbody seal → less front DF (so balance shifts rearward; complex, mark "~" if unsure). Use `~` for the balance-coupling effect. |
| `("splitter_height_mm", "-")` | `("splitter_scrape_events", "+")` | Lower splitter scrapes more (auto-generated reverse via existing `_reverse_dir` block at line 189). |

Reverse-direction entries for `+` keys are auto-generated by the existing block at line 189-197 — implementer only needs to define the `+` entries listed.

### BLOCKER #3 — Heave→variance fitters silently no-op for GT3

`learner/empirical_models.py:284-337`:

```python
def _fit_heave_to_variance(obs_list, models):
    for obs in obs_list:
        heave = obs.get("setup", {}).get("front_heave_nmm")
        var   = obs.get("telemetry", {}).get("front_rh_std_mm", 0)
        if heave and heave > 0 and var > 0: ...
```

For GT3 observations `front_heave_nmm` will be `None` or `0` → loop never appends → no relationship fitted. The user-facing `EmpiricalModelSet` is missing the GT3 equivalent: `front_corner_spring_to_rh_var` and `rear_corner_spring_to_rh_var`.

**Fix shape:**

```python
def _fit_corner_spring_to_variance(obs_list, models, axle: str):
    setup_key = f"{axle}_corner_spring_nmm"
    tel_key   = f"{axle}_rh_std_mm"
    rel_key   = f"{axle}_rh_var_vs_corner_spring"
    # Note: parallel rate seen by the chassis ≈ 2 × per-corner spring (left+right
    # in parallel for heave motion). Empirically we just want the slope, so feeding
    # per-corner k is fine; absorb the factor of 2 into the regression intercept.
    ...
```

Then call both axles inside `fit_models()` on the GT3 dispatch path. Gating via `car.suspension_arch` in `fit_models()` (or by feature presence) avoids fitting empty arrays.

### BLOCKER #4 — m_eff calibration ignores GT3 corner springs

`learner/empirical_models.py:809-833`. The `m_eff_front_values` correction reads `front_heave_nmm` directly. For GT3 cars no observations contribute, so:
- `m_eff_front_empirical_mean` is never written.
- The solver feedback loop has no GT3 calibration of effective sprung mass per axle.
- `_decode_front_heave_nmm()` (line 865) handles indexed-control GTP cars but has no notion of GT3.

The physics translation for GT3: the parallel front spring rate is `2 × front_corner_spring_nmm` (two corners share heave motion). Then the same `m_eff = k * (exc/v)²` formula applies to per-axle compliance.

**Fix shape:**

```python
if car_for_decode and car_for_decode.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    corner_k = obs.get("setup", {}).get("front_corner_spring_nmm", 0) or 0.0
    if corner_k <= 0:
        continue
    k_nm = 2.0 * corner_k * 1000.0   # parallel L+R for heave motion
else:
    k_nm = heave_nmm * 1000.0   # existing GTP path
```

### BLOCKER #5 — Observation schema has no GT3 setup fields

`learner/observation.py:163-249` builds the `setup` dict directly from `CurrentSetup` attributes. Today there is no GT3 reader path, but when Phase 2 wires `CurrentSetup.from_ibt()` to GT3 IBTs, the observation builder needs new keys:

```python
setup.update({
    "front_corner_spring_nmm":   getattr(s, "front_corner_spring_nmm", 0.0),
    "rear_corner_spring_nmm":    getattr(s, "rear_corner_spring_nmm", 0.0),
    "front_bump_rubber_gap_mm":  getattr(s, "front_bump_rubber_gap_mm", 0.0),
    "rear_bump_rubber_gap_mm":   getattr(s, "rear_bump_rubber_gap_mm", 0.0),
    "splitter_height_mm":        getattr(s, "splitter_height_mm", 0.0),
    "front_brake_pad":           getattr(s, "front_brake_pad", ""),
    "rear_brake_pad":            getattr(s, "rear_brake_pad", ""),
})
```

Telemetry side (`build_observation()` lines 261-387) needs `splitter_scrape_events`, `front_bump_rubber_contact_pct`, `rear_bump_rubber_contact_pct` plumbed from `MeasuredState`.

Documentation block (lines 38-66) listing schema keys needs a GT3 alternatives section so future readers can audit field provenance.

### BLOCKER #6 — Prediction error feedback misses GT3 metrics

`learner/empirical_models.py:944-961` has no entries for GT3-specific bottoming or RH-floor proxies:

```python
PREDICTION_METRICS = {
    ...
    # Add for GT3:
    "splitter_scrape_events":           "splitter_scrape_events",
    "front_bump_rubber_contact_pct":    "front_bump_rubber_contact_pct",
    "rear_bump_rubber_contact_pct":     "rear_bump_rubber_contact_pct",
}
```

Without these, the GT3 solver's `predict→measure→correct` loop has no signal for splitter or bump-rubber predictions, which are the GT3 equivalents of `front_bottoming_events` / `front_heave_travel_used_pct`.

### BLOCKER #7 — DEFAULT_SETUP_PARAMETERS in clusters list

`learner/setup_clusters.py:24-39`. The fixed list is GTP-shaped. For GT3, the cluster's `center` and `spreads` will only cover `pushrod`, `arb`, `camber`, `toe`, `brake_bias`, `diff_preload` — missing the springs that distinguish GT3 setups most.

**Fix shape:**

```python
def default_parameters_for_arch(arch) -> list[str]:
    base = ["front_pushrod_mm", "rear_pushrod_mm",
            "front_arb_blade", "rear_arb_blade",
            "front_camber_deg", "rear_camber_deg",
            "front_toe_mm",     "rear_toe_mm",
            "brake_bias_pct",   "diff_preload_nm"]
    if arch.has_heave_third:
        base += ["front_heave_nmm", "rear_third_nmm",
                 "front_torsion_od_mm", "rear_spring_nmm"]
    elif arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
        base += ["front_corner_spring_nmm", "rear_corner_spring_nmm",
                 "front_bump_rubber_gap_mm", "rear_bump_rubber_gap_mm",
                 "splitter_height_mm"]
    return base
```

`build_setup_cluster()` already accepts an explicit `parameters` arg — every caller needs to pass the arch-aware list, defaulting to the helper.

### DEGRADED #8 — cross_track.py heave-only m_eff aggregator

`learner/cross_track.py:160-165`:

```python
heave = obs.get("setup", {}).get("front_heave_nmm", 0)
...
if heave > 0 and var > 0 and sv_p99 > 0:
    k_nm = heave * 1000
```

Same pattern as BLOCKER #4 but in the cross-track aggregator. Mirror the dispatch.

### DEGRADED #9 — EFFECT_METRICS["platform"] heave-only fields

`learner/delta_detector.py:46-73`. Add GT3 platform proxies:

```python
"platform": [
    ...,
    "splitter_scrape_events",
    "front_bump_rubber_contact_pct",
    "rear_bump_rubber_contact_pct",
],
```

The existing heave fields can stay — for GT3 they'll always be 0 → "noise" classification — which is acceptable but verbose.

### DEGRADED #10 — _classify_setup_significance lacks GT3 thresholds

`learner/delta_detector.py:272-298`. Add:

```python
"front_corner_spring_nmm":  (5.0, 20.0),   # N/mm; matches typical GT3 step (5-10 N/mm)
"rear_corner_spring_nmm":   (5.0, 20.0),
"front_bump_rubber_gap_mm": (1.0, 3.0),
"rear_bump_rubber_gap_mm":  (1.0, 3.0),
"splitter_height_mm":       (1.0, 3.0),
```

Step values from `docs/gt3_per_car_spec.md` — BMW M4 step 10 N/mm, Mercedes step 25 N/mm, Lambo step 30 N/mm. The 5/20 thresholds work for all of them in "minor/major" classification.

### DEGRADED #11 — _classify_effect_significance thresholds dict

`learner/delta_detector.py:301-320`. Add:

```python
"splitter_scrape_events":         (2, 10),
"front_bump_rubber_contact_pct":  (5.0, 15.0),
"rear_bump_rubber_contact_pct":   (5.0, 15.0),
```

### DEGRADED #12 — _generate_insights params_to_track

`learner/ingest.py:758-759`. Currently:

```python
params_to_track = ["front_heave_nmm", "rear_third_nmm", "rear_arb_blade",
                    "front_camber_deg", "rear_camber_deg"]
```

Make car-architecture-aware:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    params_to_track = ["front_corner_spring_nmm", "rear_corner_spring_nmm",
                       "rear_arb_blade", "front_camber_deg", "rear_camber_deg",
                       "splitter_height_mm"]
else:
    params_to_track = ["front_heave_nmm", "rear_third_nmm", "rear_arb_blade",
                       "front_camber_deg", "rear_camber_deg"]
```

`_generate_insights()` receives `car: str` — needs to call `get_car(car)` lazily, or take the architecture as a parameter from `ingest_ibt()`.

### DEGRADED #13 — Observation docstring drift

`learner/observation.py:50-66`. The schema-key comment block lists `front_heave_defl_p99_mm`, `front_heave_travel_used_pct`, `heave_bottoming_events_*` as exemplars — all GTP-specific. After Phase 2, this block should split into `# GTP architecture` and `# GT3 architecture` sub-blocks, or be replaced with a reference to a typed schema (e.g. a TypedDict). Cosmetic code-smell, but will cause confusion when other agents read the file.

### DEGRADED #14 — Silent zero-sample fits

`learner/empirical_models.py:289`. `if heave and heave > 0 and var > 0:` causes every GT3 observation to be silently skipped. After fitting, the `models.relationships` dict has no entry, and `recall.predict(..., "front_rh_var_vs_heave", x)` returns `RecallResult(answer=None, confidence="no_data")`. There's no log line saying "GT3 car: 12 observations skipped because front_heave_nmm not present — did you mean front_rh_var_vs_corner_spring?".

Suggest: when `len(obs_list) > 0` but appended sample count is 0, emit a one-time `logger.warning()` per (car, fit_name).

### DEGRADED #15 — Damper struct duplication

`learner/observation.py:213-228` writes `lf` and `rf` from the same per-axle `front_*` fields, and `lr`/`rr` from the same per-axle `rear_*` fields. This is the GT3 reality (per `docs/gt3_session_info_schema.md`: "GT3 dampers are PER-AXLE, not per-corner"), so the duplication is structurally correct — but `_extract_damper_flat()` will average two identical numbers, hiding any bug where left vs right were genuinely different. Minor: prefer a per-axle struct shape on the GT3 path:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    setup["dampers"] = {
        "front": {"ls_comp": s.front_ls_comp, ...},
        "rear":  {"ls_comp": s.rear_ls_comp,  ...},
    }
```

`_extract_damper_flat()` and `KNOWN_CAUSALITY` damper-key references (`damper_lf_*` etc.) would need a parallel update.

### COSMETIC #16 — most_impactful_parameters under-represents springs

`learner/recall.py:189-203`. As a downstream consequence of #1+#2+#10, the lap-time-sensitivity ranking will under-rank GT3 corner springs (both because their deltas don't accumulate weight in `_fit_lap_time_sensitivity` and because their "minor" classification means smaller `delta_val` is treated as significant only at very large changes). Note in docstring after fixing the upstream blockers.

## Risk summary

- **Silent zero-fit risk** is the single biggest hazard: every GT3 ingest will produce an empirical model file with 0 spring relationships and no warning. Cars will appear "calibrated" because `EmpiricalModelSet` exists; downstream solvers will silently use no-data from corrections. **Mitigation:** BLOCKER #1-#5 must land together; #14 (warn on zero-sample fits) should land first as a defense-in-depth.
- **Hypothesis blackhole:** with the existing line 478 `if not known: continue`, GT3 deltas produce zero learning signal. There is no fallback "log unknown for later analysis" path. **Mitigation:** BLOCKER #2 (KNOWN_CAUSALITY entries) is a one-shot fix once entries are landed.
- **Cluster-vetoes for GT3 may behave erratically:** `setup_fingerprint` and the cluster-veto logic in `pipeline/produce.py` consume `setup_clusters.build_setup_cluster()` (BLOCKER #7). With degenerate spring axes, the veto fingerprints will collide more often → false-positive vetoes against good GT3 setups. **Mitigation:** BLOCKER #7 + audit the pipeline/produce.py cluster consumers for arch-awareness in their own audit.
- **Cross-track contamination low:** `learner/cross_track.py` already filters by car. The heave-blind m_eff aggregator (DEGRADED #8) just produces no GT3 signal — no false data.

No data-corruption risks identified — every blocker is a "no learning happens" failure mode, not a "wrong learning happens" failure mode.

## Effort estimate

- BLOCKER #1 (`STEP_GROUPS` arch-aware): 1.5 hrs (touch `learner/delta_detector.py`, plumb `arch` through `detect_delta()` from `Observation.car`).
- BLOCKER #2 (KNOWN_CAUSALITY entries): 1 hr (data entry + 1 unit test asserting all 23 entries fire on a synthetic delta).
- BLOCKER #3 + #4 (`_fit_corner_spring_to_variance`, GT3 m_eff path): 2.5 hrs.
- BLOCKER #5 (Observation schema GT3 fields): 1 hr (depends on `analyzer.setup_reader.CurrentSetup` exposing GT3 attributes — coordinate with the analyzer audit).
- BLOCKER #6 (PREDICTION_METRICS GT3 entries): 0.5 hrs.
- BLOCKER #7 (`setup_clusters` arch-aware default list): 1 hr (touch all callers).
- DEGRADED batch (#8-#14): 2 hrs total.
- COSMETIC #16: 15 min docstring update.

Total: **~9.5 hrs** for full GT3 learner support, assuming `CarModel.suspension_arch` is reliably accessible from observation dicts (it is via `obs["car"] → get_car(name)`).

## Dependencies

- **Upstream:** `analyzer/setup_reader.py` must expose GT3 fields on `CurrentSetup` (`front_corner_spring_nmm`, `rear_corner_spring_nmm`, `front_bump_rubber_gap_mm`, etc.). See the `analyzer` audit for that worker's findings. Without that upstream, the BLOCKER #5 attribute reads return 0 from `getattr(s, ..., 0.0)` and observations remain empty.
- **Upstream:** `analyzer/extract.py` (`MeasuredState`) must expose `splitter_scrape_events`, `front_bump_rubber_contact_pct`, `rear_bump_rubber_contact_pct`. See `analyzer` audit.
- **Upstream:** `car_model/cars.py` GT3 stubs need `suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL` (already done for BMW M4 GT3, Aston Vantage, Porsche 992 GT3 R per existing commits). Other 8 GT3 cars pending Phase 0/1 work.
- **Downstream:** `pipeline/produce.py` cluster-veto logic consumes `setup_clusters.build_setup_cluster()` — needs to pass the arch-aware parameters list once #7 lands. See the `pipeline` audit.
- **Downstream:** Solver `legality` / `candidate_search` for GT3 will read `KnowledgeRecall.get_corrections()` — once empirical fits exist for GT3 corner springs, those callers should reference the new keys (`front_rh_var_vs_corner_spring` etc.). See the `solver` audit.
- **Cross-cut:** auditing the `update-config` skill not relevant; this is pure code change.
