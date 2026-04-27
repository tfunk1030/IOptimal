# GT3 Phase 2 Audit — Webapp + CLI + Tests + Validation + Docs

**Audit branch:** `gt3-audit-webapp-cli-tests-docs`
**Date:** 2026-04-26
**Reviewer scope:** user-facing surface — frontend, CLI entry points, test suite, validation reports, scenario profile constants, project docs.

## Scope

This worker audited the following paths for GT3 readiness on top of the Phase 0 scaffolding (`SuspensionArchitecture`, `BMW_M4_GT3`, `ASTON_MARTIN_VANTAGE_GT3`, `PORSCHE_992_GT3R` already in `car_model/cars.py`):

- `webapp/` — FastAPI app, Jinja templates, static CSS
- `__main__.py` — top-level `ioptimal` CLI entry point with subcommands
- `pipeline/__main__.py`, `analyzer/__main__.py`, `learner/ingest.py` — module-level CLIs
- All `tests/test_*.py` — every test file enumerated via `Glob`
- `validation/run_validation.py`, `validation/objective_calibration.py` — calibration & validation reports
- `solver/scenario_profiles.py` — per-scenario weight + sanity profiles
- `CLAUDE.md` — project conventions
- `skill/per-car-quirks.md` — per-car onboarding doc
- `docs/calibration_guide.md` — calibration onboarding workflow

A separate worker is auditing `output/setup_writer.py`, `solver/`, `analyzer/`, `pipeline/produce.py`, and the per-step solver chain — those areas are explicitly out of scope here and called out only when a finding straddles the boundary.

## Summary table

| ID | Severity | Sub-area | File:line | Title |
|----|----------|----------|-----------|-------|
| F1 | BLOCKER | webapp | `webapp/templates/runs_new.html:48-56` | Static `<select>` whitelists 5 GTP cars only |
| F2 | BLOCKER | webapp | `webapp/services.py:69-126` | `SETUP_GROUP_SPECS` hardcodes Front heave / Rear third / Front torsion rows that don't exist for GT3 |
| F3 | DEGRADED | webapp | `webapp/app.py:80,168` | `car: str = Form("bmw")` and `car or "bmw"` default to BMW (GTP) instead of an explicit-required field |
| F4 | DEGRADED | webapp | `webapp/services.py:130-157` | `PARAM_EXPLANATIONS` describes GTP-only physics ("torsion bar", "third spring") with no GT3 variant copy |
| F5 | BLOCKER | CLI | `__main__.py:583` | `calibrate` subcommand `choices=["bmw","cadillac","ferrari","acura","porsche"]` rejects GT3 canonical names |
| F6 | DEGRADED | CLI | `__main__.py:489-490, 542, 556, 583, 605, 622, 794-795` | All `--car` help text says "(bmw \| ferrari \| porsche \| cadillac \| acura)" — never mentions GT3 |
| F7 | DEGRADED | CLI | `__main__.py:467-468, 783` | Top-level CLI description "GTP setup solver" — class-locked |
| F8 | DEGRADED | CLI | `analyzer/__main__.py:35` | `--car` help locks to GTP names |
| F9 | DEGRADED | CLI | `learner/ingest.py:807` | `--car` argparse help is empty, but docstring example only shows `bmw` |
| F10 | BLOCKER | tests | `tests/test_registry.py:114-125` | `test_returns_all_display_names` hard-asserts `len(names) == 5` — fails the moment GT3 cars enter the registry |
| F11 | BLOCKER | tests | `tests/test_setup_regression.py:1-80` | Regression baseline only covers BMW/Sebring + Porsche/Algarve `.sto` — no GT3 baseline; pipeline cannot be regression-locked for GT3 |
| F12 | DEGRADED | tests | `tests/test_registry.py:19-69` | All `parametrize` lists are GTP-only — no GT3 canonical-name resolution coverage |
| F13 | DEGRADED | tests | `tests/test_aero_ld_validation.py`, `tests/test_optimize.py` | Tests reference GTP cars only; no parametrize for GT3 wing-angle ranges |
| F14 | DEGRADED | tests | `tests/test_all_cars_garage_truth.py:98-101` | `_TOLERANCES` dict keyed by 4 GTP cars (bmw/porsche/ferrari/acura). GT3 cars will silently fall through |
| F15 | DEGRADED | tests | `tests/test_webapp_routes.py:36-43` | E2E test posts `car=bmw` only; no GT3 form submission exercise |
| F16 | BLOCKER | validation | `validation/run_validation.py:147-156` | `_confidence_tier()` hardcodes 3 GTP car/track pairs; every GT3 row will be tagged "unsupported" |
| F17 | BLOCKER | validation | `validation/run_validation.py:172-174,186-188` | `_target_samples` filters to BMW/Sebring; full validation report is GTP-only |
| F18 | BLOCKER | validation | `validation/objective_calibration.py:127-169` | `load_observations()` filters to `car == "bmw"` and `track == sebring_international_raceway`; calibration weights only ever fit BMW |
| F19 | DEGRADED | validation | `validation/run_validation.py:333-340` | `workflow_map` and report header list GTP workflow only |
| F20 | BLOCKER | scenario_profiles | `solver/scenario_profiles.py:1-294` | No GT3-specific scenario; sanity windows for `front_heave_travel_used_pct`, `front_excursion_mm`, `rear_rh_std_mm` are tuned to GTP ride-height telemetry. GT3 has no heave channel and runs different RH magnitudes (BMW M4 GT3 dynamic F=68/R=70, Porsche 992 F=69/R=61 *reverse rake*) |
| F21 | DEGRADED | scenario_profiles | adjacent — `solver/corner_spring_solver.py:309,487,628`, `solver/damper_solver.py:444,1004`, `solver/diff_solver.py:302`, `solver/explorer.py:191` | Hardcoded `89.0` L fuel default (GTP BMW M Hybrid V8 capacity). GT3 cars range 100 (BMW/Porsche) to 106 L (Aston/Mercedes) |
| F22 | BLOCKER | docs (CLAUDE.md) | `CLAUDE.md` (entire) | Header reads "Physics-Based Setup Calculator for iRacing GTP/Hypercar"; "Project Goal" pins authority to GTP cars. No GT3 section. Heave/third documented as universal physics |
| F23 | BLOCKER | docs (per-car-quirks) | `skill/per-car-quirks.md:1-461` | "Per-Car Setup Quirks" Table of Contents lists only 5 GTP cars. "Critical Architecture Differences" lays out LMDh / Multimatic / ORECA / Ferrari LMH chassis frames as if exhaustive. No GT3 section |
| F24 | BLOCKER | docs (calibration_guide) | `docs/calibration_guide.md:1-2` | Title "iOptimal **GTP** Calibration Guide". 21 references to "GTP cars" / "all GTP cars" assumed universal. No GT3 onboarding workflow |

**Counts:** 9 BLOCKER, 15 DEGRADED, 0 COSMETIC.

## Findings — webapp

### F1 (BLOCKER) — `webapp/templates/runs_new.html:48-56`
Hardcoded car list:
```html
<select id="car" name="car">
  <option value="bmw">BMW M Hybrid V8</option>
  <option value="cadillac">Cadillac V-Series.R</option>
  <option value="ferrari">Ferrari 499P</option>
  <option value="porsche">Porsche 963</option>
  <option value="acura">Acura ARX-06</option>
</select>
```
A user cannot run a session for `bmw_m4_gt3`, `aston_martin_vantage_gt3`, or `porsche_992_gt3r` even though those car models exist in `car_model/cars.py` at lines 3196, 3325, 3450. The template should either:
- iterate `car_model.registry.supported_car_names()` (currently hardcoded to GTP — see F10), OR
- introduce `webapp/services.py::list_supported_cars(class_filter=None)` that returns `(canonical, display_name)` pairs and group GTP / GT3 in `<optgroup>` elements.

### F2 (BLOCKER) — `webapp/services.py:69-126`
`SETUP_GROUP_SPECS["Platform"]` hardcodes rows that don't apply to GT3:
- `RowSpec("Front heave", ("current_setup.front_heave_nmm",), ("step2.front_heave_nmm",), "N/mm", 0)`
- `RowSpec("Rear third",  ("current_setup.rear_third_nmm",),  ("step2.rear_third_nmm",),  "N/mm", 0)`
- `RowSpec("Front torsion", ("current_setup.front_torsion_od_mm",), ("step3.front_torsion_od_mm",), "mm", 2)`

GT3 cars have `heave_spring=None` and `front_torsion_c=0.0` (verified in `tests/test_suspension_architecture.py` at lines 38-43, 119-124). These three rows will render `'-'` for GT3 values and waste UI real-estate. Worse, the "Rear spring" row references `step3.rear_spring_rate_nmm` which is the GTP-conventional SPRING rate — for GT3 it must be the **per-corner front and rear coil rates** (BMW M4 GT3 driver-loaded F=252, R=179 N/mm; Porsche 992 F=220, R=260).

Recommended shape:
- Add `arch_filter: SuspensionArchitecture | tuple | None` to `RowSpec` and skip rows where `not car.suspension_arch in arch_filter`.
- Add new GT3 rows: `Front spring`, `Rear spring`, `Front bump rubber gap`, `Rear bump rubber gap`, `Splitter height`.
- Move `Front heave`, `Rear third`, `Front torsion` into a GTP-only filter.

### F3 (DEGRADED) — `webapp/app.py:80, 168`
```python
car: str = Form("bmw"),    # line 80
...
car=car or "bmw",          # line 168
```
Default to BMW silently if missing. With GT3 cars added, an unspecified car can mis-route to a GTP solver path. Fix by making `car` required and validating against `car_model.cars._REGISTRY`.

### F4 (DEGRADED) — `webapp/services.py:130-157`
`PARAM_EXPLANATIONS["Front torsion"]`, `["Rear third"]`, `["Front heave"]` describe GTP-only physics. For GT3 the same labels won't render (per F2 fix), but the dict should also gain GT3-specific entries: `Front spring`, `Rear spring`, `Bump rubber gap`, `Splitter height`. Splitter height is a NEW GT3 garage parameter (`docs/gt3_session_info_schema.md:34`) and should explain the trade-off (more splitter height = lower front DF, less stall risk).

## Findings — CLI entry points

### F5 (BLOCKER) — `__main__.py:583`
```python
calibrate_parser.add_argument("--car", required=True,
                    choices=["bmw", "cadillac", "ferrari", "acura", "porsche"],
                    help="Car to calibrate")
```
Hard-rejects `bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`. Same bug at line 3228 of the auto-calibrate module (out of scope, but flagged for the calibration audit worker).

Replace with dynamic enumeration:
```python
from car_model.cars import _REGISTRY
calibrate_parser.add_argument("--car", required=True,
                    choices=sorted(_REGISTRY.keys()),
                    help="Car canonical name")
```

### F6 (DEGRADED) — `__main__.py` repeated `--car` help strings
Lines 489-490, 542, 556, 605, 622, 794-795 all carry the help text:
```
"Car canonical name (bmw | ferrari | porsche | cadillac | acura)"
```
Bare strings drift from the registry. Suggest: build the help text once from `_REGISTRY.keys()` and reuse.

### F7 (DEGRADED) — `__main__.py:467-468, 783`
Top-level CLI description: `"IOptimal — GTP setup solver (pipeline + physics)"`. Reads as class-locked. Replace with `"IOptimal — physics-based setup solver for iRacing GTP and GT3 classes"`.

### F8 (DEGRADED) — `analyzer/__main__.py:35`
```python
help="Car name (bmw, ferrari, porsche, cadillac, acura)",
```
Same drift; same fix.

### F9 (DEGRADED) — `learner/ingest.py:807`
`--car` argparse help is empty; the docstring example at `learner/ingest.py:13-18` shows only `bmw`. Add a GT3 example: `python -m learner.ingest --car bmw_m4_gt3 --ibt session.ibt`.

## Findings — tests

### F10 (BLOCKER) — `tests/test_registry.py:114-125`
```python
def test_returns_all_display_names(self):
    names = supported_car_names()
    assert len(names) == 5
    assert "BMW M Hybrid V8" in names
    assert "Porsche 963" in names
```
This test will FAIL the moment GT3 entries land in the canonical-names registry (`car_model/registry.py` `_CARS`). The Phase 0 work added GT3 cars to `_REGISTRY` in `car_model/cars.py` but hasn't yet propagated to `car_model/registry.py` (out of scope here — flagged for the registry audit worker). When that lands, this test must be updated to: assert `>= 8` (5 GTP + 3 named GT3 stubs) AND assert presence of each known GT3 display name.

### F11 (BLOCKER) — `tests/test_setup_regression.py`
Only baselines on disk: `tests/fixtures/baselines/bmw_sebring_baseline.sto` and `tests/fixtures/baselines/porsche_algarve_baseline.sto`. There is no GT3 regression lock.

**Proposed GT3 regression baseline plan:**

1. Generate `.ibt` from real Spielberg GT3 sessions referenced in `docs/gt3_session_info_schema.md`. Files exist at:
   - `docs/gt3_session_info_bmw_m4_gt3_spielberg_2026-04-26.yaml` (verify the source `.ibt` is in `data/telemetry/` or LFS)
   - `docs/gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml`
   - `docs/gt3_session_info_aston_vantage_spielberg_2026-04-26.yaml`
2. Add 3 new baseline files (paths to create):
   - tests/fixtures/baselines/bmw_m4_gt3_spielberg_baseline.sto
   - tests/fixtures/baselines/porsche_992_gt3r_spielberg_baseline.sto
   - tests/fixtures/baselines/aston_martin_vantage_gt3_spielberg_baseline.sto
3. Add three pytest cases in `tests/test_setup_regression.py` mirroring the BMW/Sebring + Porsche/Algarve pattern. Mark them `@pytest.mark.skipif(not BASELINE.exists(), reason="GT3 baseline pending")` until the IBT files are checked into LFS.
4. Update `tests/fixtures/baselines/README.md` with three new regeneration commands.

Risk: Phase 0 GT3 stubs may not produce a stable `.sto` end-to-end yet (Step 1 may run, Steps 2-6 will block on calibration gate). The test should explicitly assert "Step 1 succeeds, Steps 2-6 emit calibration-instruction blocks" rather than full setup output. Once Phase 2 calibration lands, swap to full-setup baselines.

### F12 (DEGRADED) — `tests/test_registry.py:19-69`
4 `parametrize` blocks each list 5 GTP cars. None test GT3 canonical name resolution (`"bmw_m4_gt3"`, screen names `"BMW M4 GT3 EVO"`, `iracing_car_path` strings `"bmwm4gt3"` / `"amvantageevogt3"` / `"porsche992rgt3"`). Add at least one parametrize block per test class for GT3.

### F13 (DEGRADED) — `tests/test_aero_ld_validation.py`, `tests/test_optimize.py`
GTP-only `parametrize` lists. The aero validation should expand to GT3 wing ranges:
- BMW M4 GT3: -2..+6 (9 angles)
- Aston Vantage: 5..13 (9)
- Porsche 992 GT3R: 5.7..12.7 (8, 0.7° offset)

These wing values are already pinned in `tests/test_suspension_architecture.py:147-148, 195, 239`, so existence is verified. The downstream aero map / L/D validation is not.

### F14 (DEGRADED) — `tests/test_all_cars_garage_truth.py:98-101`
```python
_TOLERANCES = {
    "bmw": {...},
    "porsche": {...},
    "ferrari": {...},
    "acura": {...},
}
```
Keyed by canonical car names; GT3 entries fall through. Add empty dicts for GT3 with status `"pending_calibration"` so the test skips rather than KeyErrors.

### F15 (DEGRADED) — `tests/test_webapp_routes.py:36-43`
Form POST exercises `car=bmw` only. Add a GT3 case so the create-run path is exercised end-to-end after F1/F5 fixes.

## Findings — validation

### F16 (BLOCKER) — `validation/run_validation.py:147-156`
```python
def _confidence_tier(row: ObservationSample, count: int) -> str:
    track_slug = slugify(row.track)
    if row.car == "bmw" and track_slug == "sebring_international_raceway":
        return "calibrated"
    if row.car == "ferrari" and track_slug == "sebring_international_raceway":
        return "partial"
    if row.car == "cadillac" and track_slug == "silverstone_circuit":
        return "exploratory"
    return "unsupported"
```
Every GT3 / Spielberg row tagged "unsupported" forever, regardless of calibration progress.

Suggested replacement: drive the tier from a new registry/JSON file (e.g. data/calibration/support_tiers.json — to be created) and read with the per-track slug as the key. New rows expected:
- `("bmw_m4_gt3", "red_bull_ring") → "exploratory"` (1 IBT verified)
- `("aston_martin_vantage_gt3", "red_bull_ring") → "exploratory"`
- `("porsche_992_gt3r", "red_bull_ring") → "exploratory"`

### F17 (BLOCKER) — `validation/run_validation.py:172-174, 186-188`
`_target_samples` filters to `car == "bmw"` and `slugify(track) == "sebring_international_raceway"`. The whole correlation/recalibration analysis is BMW/Sebring-only. To add GT3 support, parameterize `_target_samples` over a list of (car, track) tuples and emit one section per tuple. Keep BMW/Sebring as the headline pair until GT3 has ≥30 observations.

### F18 (BLOCKER) — `validation/objective_calibration.py:127-169`
`OBS_DIR.glob("bmw_*.json")` and `slugify(track) != "sebring_international_raceway"` filter excludes everything else. Calibration weight search will never touch GT3 data. Same parameterization fix as F17. Until GT3 has enough observations to fit, gate behind `if observations: ...` and skip with a printed message.

### F19 (DEGRADED) — `validation/run_validation.py:333-340`
The `workflow_map` and the report header are GTP-flavored. Cosmetic; update once F16-F18 land.

## Findings — scenario_profiles

### F20 (BLOCKER) — `solver/scenario_profiles.py` (entire file)

Three of the four sanity limits in `PredictionSanityProfile` are GTP-tuned:
- `max_front_heave_travel_used_pct` — GT3 has no heave spring; this metric should be `None` for GT3
- `max_front_excursion_mm = 18.0` — GT3 RH magnitudes differ from GTP. Need re-tuning from GT3 telemetry
- `max_rear_rh_std_mm = 9.0` — same

Recommendation: introduce per-class scenario profiles, e.g., `single_lap_safe_gt3`, `quali_gt3`, etc., or add a `class_filter` field on `ScenarioProfile` and resolve at runtime via `car.class_id`. The fuel/race-length constants (F21) belong here too.

### F21 (DEGRADED, adjacent) — hardcoded `89.0` L fuel default
Sites:
- `solver/corner_spring_solver.py:309, 487, 628`
- `solver/damper_solver.py:444, 1004`
- `solver/diff_solver.py:302`
- `solver/explorer.py:191`

89 L is the BMW M Hybrid V8 GTP capacity. GT3 capacities (verified from IBT):
- BMW M4 GT3 / Porsche 992 GT3R: 100.0 L
- Aston Vantage / Mercedes-AMG: 106.0 L

These solvers fall outside this audit's primary scope (covered by the solver-chain worker), but are the natural target for "scenario_profiles owns the fuel default". Recommendation: have `ScenarioProfile` carry a fuel-fraction multiplier (1.0 for race start, 0.5 for sprint, 0.1 for quali) and let solvers multiply against `car.fuel_capacity_l` rather than against `89.0`.

## Findings — docs

### F22 (BLOCKER) — `CLAUDE.md`

Header line 1: `"# GTP Setup Builder — Physics-Based Setup Calculator for iRacing GTP/Hypercar"`. The "Project Goal" section (line ~5) anchors authority to BMW M Hybrid V8 / Sebring and lists Porsche/Ferrari/Cadillac/Acura as the exhaustive car list.

Required additions:
1. Rename project line: `"Physics-based setup solver for iRacing GTP/Hypercar AND GT3 classes"`.
2. Add a new section `## GT3 Architecture Notes` explaining:
   - GT3 cars use coil springs at all 4 corners (`SuspensionArchitecture.GT3_COIL_4WHEEL`).
   - **No heave / no third spring** — Step 2 returns `HeaveSolution.null()` and the calibration gate marks `heave_third` as `not_applicable` (already wired per `tests/test_suspension_architecture.py` lines 37-38, 251-258, 285-300).
   - **No front torsion bar** — Step 3 uses the front coil spring directly.
   - **Per-axle dampers** (8 channels not 16) — see `docs/gt3_session_info_schema.md:74-86`.
   - **Damper polarity varies** — Audi/McLaren/Corvette inverted; per-car `click_polarity` field needed (`docs/gt3_per_car_spec.md:18-34`).
   - **ARB encoding varies** — paired/single/binary stages; per-car blade-count and label scheme.
3. Update Key Principle 7 ("Calibrated or instruct, never guess") wording from "GTP" to "GTP/GT3".
4. Update "Current calibration status" to add a GT3 row family (Spielberg as the de-risk track from existing IBTs).

### F23 (BLOCKER) — `skill/per-car-quirks.md`

Title V2 reads "Per-Car Setup Quirks & Parameter Reference". TOC lists 5 GTP cars only. Section 1 "Critical Architecture Differences" describes only LMDh/Multimatic/ORECA/Ferrari LMH chassis as if exhaustive.

Required additions:
1. Add to TOC: `10. GT3 Architecture (cross-cutting)`, `11. BMW M4 GT3 EVO`, `12. Porsche 911 GT3 R (992)`, `13. Aston Martin Vantage GT3 EVO`, …
2. Add a "GT3 Architecture (cross-cutting)" section with the ~9 cross-cutting facts from `docs/gt3_per_car_spec.md:7-17`.
3. Per-car GT3 sections should include the verified IBT-derived values from `docs/gt3_session_info_schema.md:115-180` (mass, fuel, front weight, brake bias, defaults). For the 8 GT3 cars without yet-verified IBTs (Mercedes, Ferrari 296, Lambo, McLaren, Acura NSX, Audi, Mustang, Corvette), pull from `docs/gt3_per_car_spec.md` and tag every field `[MANUAL]`, `[REAL-WORLD]`, `[COMMUNITY]`, or `PENDING_IBT` to match that doc's convention.
4. Important specifically for Acura: there are now TWO Acura cars in iRacing (`acura` = Acura ARX-06 GTP, vs `acura_nsx_gt3`). The doc must disambiguate.

### F24 (BLOCKER) — `docs/calibration_guide.md`

Title is "iOptimal **GTP** Calibration Guide". 21 places say "GTP cars" or "all GTP cars". Examples assume heave/third/torsion vocabulary that doesn't apply to GT3 (`docs/calibration_guide.md:568, 896, 1081`).

Required:
1. Rename to "iOptimal Setup Calibration Guide" (drop GTP).
2. Add a `## GT3 Calibration Workflow` section that:
   - Explains the calibration cascade differs for GT3 (no Step 2 = heave/third).
   - Notes Acura/Mustang/Corvette have **spring-perch auto-adjust** which decouples Step 1 ↔ Step 3 — different from any GTP car.
   - Notes bump rubber gap is a NEW garage parameter for GT3 cars.
   - Notes ARB encoding diverges per-car; provides the per-car table from `docs/gt3_per_car_spec.md:53-67`.
3. Each GTP-specific paragraph should be tagged with a leading "[GTP only]" badge so a GT3 reader doesn't follow it by mistake.

## Risk summary

| Risk | Likelihood | Impact |
|------|------------|--------|
| Webapp users select "Porsche 963" when they mean "Porsche 911 GT3 R (992)" — different physics, wrong setup output | HIGH | Wrong setup loaded into iRacing, potentially unsafe at speed (vortex burst, RH floor violation) |
| `test_returns_all_display_names` (F10) blocks any registry update to add GT3 cars | HIGH (CI fail) | Phase 2 PRs cannot land |
| Hardcoded `--car choices` in `__main__.py:583` and `car_model/auto_calibrate.py:3228` rejects GT3 calibrate runs silently with argparse error | HIGH | Calibration phase blocked end-to-end |
| Sanity profile heave-travel limits applied to GT3 cars where the field is `None` will silently pass everything (no penalty) — false-positive sanity OK | MEDIUM | Solver picks bad GT3 setups without the safety net catching them |
| Validation reports drop "unsupported" tier on every GT3 row, hiding calibration progress | LOW | Cosmetic but blocks status tracking |
| Hardcoded `89.0` L fuel default propagates GTP weight-on-tyre into GT3 corner-spring solver, leading to under-estimated wheel rates | MEDIUM | GT3 setups too soft, platform sigma high |
| Documentation drift — new contributors assume heave/third is universal physics | LOW | Time waste, eventual incorrect commits |

## Effort estimate

| Group | Items | Estimated effort |
|-------|-------|------------------|
| Webapp template + select dynamic | F1, F3 | 2 hours |
| Webapp `SETUP_GROUP_SPECS` arch-aware | F2, F4 | 4 hours |
| CLI `--car choices` from registry | F5, F6, F7, F8, F9 | 2 hours |
| Test parametrize widening | F10, F12, F13, F14, F15 | 4 hours |
| GT3 regression baselines | F11 | 6 hours (includes IBT staging in LFS, baseline gen, skipif gating) |
| Validation report support-tier table | F16, F17, F18, F19 | 6 hours |
| `ScenarioProfile` per-class | F20 | 4 hours |
| Adjacent fuel-default refactor | F21 | 2 hours |
| `CLAUDE.md` GT3 section | F22 | 2 hours |
| `skill/per-car-quirks.md` GT3 sections | F23 | 6 hours |
| `docs/calibration_guide.md` GT3 workflow | F24 | 4 hours |
| **Total** | 24 findings | **~42 hours** |

## Dependencies

- **Upstream — must land before this audit's BLOCKERs:**
  - `car_model/registry.py` registry must include GT3 entries (separate audit worker — registry/canonical-names).
  - `output/setup_writer.py` must emit valid GT3 `.sto` per-car (separate audit — setup-writer).
  - `solver/heave_solver.py:HeaveSolution.null()` integration into Step 2 cascade (Phase 0 — appears done per `tests/test_suspension_architecture.py:251-258`; needs end-to-end smoke).
- **Concurrent — coordinate with:**
  - Calibration-gate audit worker (F16-F18 fixes touch the same calibration tier registry).
  - Solver-chain audit worker (F21 — fuel default + `corner_spring_solver` per-car wiring).
  - Aero-map audit worker (F13 — wing range parametrization).
- **Downstream — unblocked by this audit's fixes:**
  - End-to-end Step 1 GT3 smoke test on Spielberg.
  - Calibration sweep CLI for `bmw_m4_gt3` / `porsche_992_gt3r`.
  - Webapp UAT with GT3 testers.

## Test files reviewed (full list)

All 51 `tests/test_*.py` files were enumerated via `Glob`. The files explicitly cited above (F10-F15) are the ones requiring direct GT3 changes. The remainder are GTP-physics-only and either:
- Pass through unchanged for GT3 (e.g., `tests/test_brake_solver.py`, `tests/test_diff_solver_extended.py`, `tests/test_run_trace.py`).
- Already cover GT3 scaffolding (`tests/test_suspension_architecture.py` — Phase 0 contract is well-pinned and should not be touched without coordinated changes).
- Are car-specific GTP tests that must NOT be parameterized for GT3 because they pin GTP-specific output (`tests/test_ferrari_setup_schema.py`, `tests/test_ferrari_setup_writer.py`, `tests/test_bmw_rotation_search.py`, `tests/test_bmw_sebring_garage_truth.py`, `tests/test_bmw_setup_coverage.py`, `tests/test_acura_hockenheim.py`). These will need GT3 sibling files (proposed name: tests/test_bmw_m4_gt3_spielberg.py) once calibration is operational.
