---
date: 2026-04-12
topic: calibration-ibt-garage-solver-parity
focus: IBT setup truth, garage correlations, per-car calibration, iRacing output parity, physics+telemetry-driven solve
---

# Ideation: IBT-Truth Garage Calibration and iRacing Parity

## Codebase Context

**Project shape:** Python monorepo — `analyzer` (IBT → `setup_reader` / `extract`), `car_model` (`garage.py`, `auto_calibrate.py`, `setup_registry.py`, `calibration_gate.py`), `solver` (6-step chain), `pipeline/produce.py`, `output/setup_writer.py` + `garage_validator.py`, `data/calibration/<car>/`.

**Conventions:** “Calibrated or instruct” gate; garage display is learned via regressions from **many** IBT-derived `(setup_vector → measured display)` points, with physics-motivated feature pools and 3:1 sample:feature caps; compliance (`1/k`) terms where data supports it; STO/session YAML as setup carrier (IBT embeds `CarSetup`).

**Past learnings (no `docs/solutions/` in repo):** `CALIBRATION_GUIDE.md`, `docs/calibration_guide.md`, `skill/ibt-parsing-guide.md`, `research/physics-notes.md` — IBT session YAML is authoritative over raw STO for “what was loaded”; indexed cars need `GarageSetupState.from_current_setup(..., car=car)`; LLTD from IBT proxy is **not** wheel-load LLTD; `garage_validator` order matters (e.g. Ferrari index conversion); universal calibration sweep and regression tests guard parity.

**Why one IBT does not “instantly” produce a perfect garage model:** A single file gives **one point** in a **high-dimensional, coupled** space (pushrods, perches, heave/third/coil, torsion/indices, camber, fuel, wing). iRacing’s **display** outputs (RH, deflections, slider-equivalent channels) are **emergent** from the full state vector and car-specific internal geometry. The codebase **does** read setup parameters from that IBT immediately; what it cannot do from one sample is **identify** all cross-parameter coefficients (ill-conditioned regression). Calibration = **fit a model** over **many diverse** setups. That is a data/statistics limit, not a parser limitation.

**Pain signals:** Partial car coverage; weak rear-RH on sparse data; discrete encodings (Ferrari/Acura indices, perch steps); optional XML ID drift risk; dynamic telemetry (σ, bottoming) vs static garage display are related but not the same layer.

---

## Ranked Ideas

### 1. IBT session YAML as contract of record + per-car golden vectors
**Description:** Treat `setup_reader` → `GarageSetupState` as a versioned contract. For each car, maintain a small set of **golden IBTs** (or frozen YAML snippets) and assert decode → canonical fields → round-trip expectations in CI.

**Rationale:** Makes “the program knows parameter names and values” testable; catches registry/XML drift early.

**Downsides:** Maintenance cost when iRacing patches garage schema; need fixture discipline.

**Confidence:** 85%

**Complexity:** Medium

**Status:** Unexplored

### 2. Data-first bootstrap: existing IBTs + learnings, then gap-targeted DOE
**Description:** Before asking for new track time, **inventory and ingest** all IBTs already on disk (team folders, `ibtfiles/`, historical exports), merge into `data/calibration/<car>/` via `auto_calibrate`, `--refit`, and `validation.universal_calibration_sweep`. Parse **`data/learnings/`** (observations, deltas, empirical models) for **prediction-error corrections** and session metadata — not as a substitute for garage display regressions, but to compound telemetry-grounded adjustments after the base fit. Only then generate a **short gap list** (which knobs lack span or which outputs stay weak/ high LOO) and run a **minimal orthogonal DOE** for those axes only.

**Rationale:** Matches operator intent (“use current available data, IBT files, and learnings”): maximum reuse, least redundant laps, fastest path to honest R²/LOO and iRacing parity.

**Downsides:** Old IBTs must be same car + consistent `setup_registry` era; contaminated or wrong-car files need culling; learnings JSON has no file locking for multi-writer teams.

**Confidence:** 88%

**Complexity:** Medium (ingest automation + gap report); Low incremental process for solo users

**Status:** Unexplored

### 3. Split “display garage law” vs “dynamic telemetry constraints” in the mental model and UI
**Description:** Explicitly label two layers: (A) regressions predicting **garage panel** consistency; (B) IBT-measured **dynamics** (RH std, bottoming proxies, shock spectra) feeding solver targets and gates. The solve uses both, but never confuses proxy “LLTD” with wheel loads.

**Rationale:** Matches user expectation of “physics and data” without smuggling lap-time leaderboard optimization; clarifies why some targets are anchored to measurements.

**Downsides:** More concepts for newcomers; needs clear report copy.

**Confidence:** 80%

**Complexity:** Medium

**Status:** Unexplored

### 4. Closed-loop iRacing parity: `.sto` → load → IBT → diff
**Description:** After `pipeline.produce`, user loads `.sto`, exports a short IBT, run a `diff_garage_vs_ibt` tool comparing predicted `GarageOutputModel` vs extracted session setup + key telemetry-derived checks.

**Rationale:** Ground truth for “output matches iRacing” is the game, not the solver’s internal wish.

**Downsides:** Manual step unless desktop app automates file pick-up; game patches can shift displays slightly.

**Confidence:** 85%

**Complexity:** Medium

**Status:** Unexplored

### 5. Discrete inverse layer for indexed / quantized garage knobs
**Description:** After continuous solve, run a car-specific **snap + reconcile** pass: map target N/mm or mm to legal indices/perch clicks; re-run `garage_validator` correlation fixes in a fixed order (documented per car).

**Rationale:** Addresses “torsion OD and turns way off” — often a **discretization** and **ordering** problem, not only regression RMSE.

**Downsides:** Car-specific tables must stay aligned with `setup_registry`.

**Confidence:** 75%

**Complexity:** High

**Status:** Unexplored

### 6. Registry completeness audit tooling
**Description:** Script that lists every YAML path in `setup_registry` for a car and verifies presence in IBT `CarSetup`, plus STO XML IDs used by `setup_writer`.

**Rationale:** Prevents silent wrong-field writes that look like “correlation is broken.”

**Downsides:** One-time deep effort per car; ongoing on iRacing updates.

**Confidence:** 80%

**Complexity:** Medium

**Status:** Unexplored

---

## Appendix: User + Program Steps for Strong Calibration (Playbook)

*Condensed from `CALIBRATION_GUIDE.md` and aligned to repo commands. “Perfect” here means “within honest model limits + iRacing parity checks” — not omniscient without wheel-load telemetry.*

### A0. Use data you already have (program-first)

1. **Collect every IBT** for the target car (any track) into one folder.
2. **Ingest:** `python -m car_model.auto_calibrate --car <car> --ibt-dir <path>` (repeat with `--ibt` for stragglers).
3. **Refit + sweep:** `--refit` then `python -m validation.universal_calibration_sweep --car <car> --verbose`.
4. **Read weak outputs** (rear RH, specific deflections, high LOO): those name the **missing excitation**, not a broken parser.
5. **Optional — learnings:** run `python -m learner.ingest` on the same IBTs (or use existing `data/learnings/`) so `recall` / team sync can supply **prediction corrections** atop calibrated physics — still not a replacement for garage display fits.
6. **Only then** schedule **targeted** iRacing sessions (Appendix A) for the gaps.

### A. In iRacing (targeted data collection)

1. **Pick one car** and **one reference track** for garage sweeps (any practice track is fine for *garage* calibration; aero compression and track-specific validation may use your target circuit later).
2. **Baseline setup loaded** in garage; note wing/fuel if you need them held constant across a subset.
3. **Orthogonal sweeps:** change **one primary parameter at a time**, drive **2–3 clean laps**, ensure IBT saves.
4. **Minimum coverage (garage model):** aim for **8+ unique setups** with several values each for **front/rear pushrod**, **rear third**, **rear corner spring** (or torsion where applicable), **front heave**; add **2 fuel levels** (low ~10L, full) and **2–3 torsion OD / index** points where the car uses them.
5. **Better coverage (stable LOO, fewer “weak” models):** **20–30+ unique setups** following the priority table in `CALibration_GUIDE.md` (pushrods → third → rear spring → heave → perches → torsion → camber → fuel).
6. **Do not waste time varying** ARB blade/size or TC/diff for **garage display** models — documented as zero effect on those outputs.
7. **Optional but high value for solver trust:** repeat a subset at **your real race track** so aero compression and speed-spectrum telemetry match competition.

### B. In the program (ingest → fit → verify → run pipeline)

1. **Ingest IBTs into calibration points:**
   `python -m car_model.auto_calibrate --car <car> --ibt-dir <path>`
   or list explicit `.ibt` files.
2. **Refit models from accumulated points:**
   `python -m car_model.auto_calibrate --car <car> --refit`
3. **Check status and R² / LOO story:**
   `python -m car_model.auto_calibrate --car <car> --status`
4. **Global prediction vs recorded ground truth:**
   `python -m validation.universal_calibration_sweep --car <car> --verbose`
5. **Run the full pipeline on a held-out IBT** (not in the training merge duplicates):
   `python -m pipeline.produce --car <car> --ibt session.ibt ... --sto out.sto`
6. **Inspect CLI `CALIBRATION CONFIDENCE` / JSON `calibration_provenance`** — resolve any `uncalibrated` or `weak` steps before trusting that portion of the solve.
7. **iRacing parity check:** load `out.sto` in the sim, confirm garage panel matches within expected tolerances; export a fresh IBT and re-run a diff or sweep row for that setup if tooling exists.
8. **Learning loop (optional):** `python -m learner.ingest ...` to accumulate observation JSON for team/server and prediction-error corrections — still not lap-time-optimized selection by design.

### C. What will still be “imperfect” without extra data

- **True LLTD** from wheel loads (IBT limitation) — ARB step uses physics formula + explicit driver-anchor when unverifiable.
- **Sparse or collinear sweeps** — rear RH and coupled outputs may stay **weak** until the DOE improves excitation.
- **Game updates** that change internal display physics — requires re-ingest and refit.

---

## Rejection Summary

| # | Idea | Reason rejected |
|---|------|-----------------|
| 1 | “Use ML / deep model on one IBT” | Not identifiable; contradicts coupling + sample complexity; not grounded in project’s regression discipline |
| 2 | “Infer optimal setup from personal best lap only” | User explicitly rejected lap-time-as-objective; also confounds track, traffic, and driver |
| 3 | “Parse .sto only for garage truth” | Repo docs mark IBT session YAML as authoritative; STO decode is secondary / brittle |
| 4 | “Auto-tune every click from telemetry alone” | Underconstrained without setup excitation; same as needing DOE |
| 5 | “Single universal correlation matrix for all GTP cars” | Contradicts per-car iRacing implementation + `setup_registry` reality |
| 6 | “Calibrate LLTD from IBT proxy” | Known geometric proxy; already flagged as invalid for spring/ARB calibration |

---

## Session Log

- 2026-04-12: Initial ideation — ~24 raw candidates considered (orchestrator + parallel grounding), 6 survivors ranked; appendix playbook added for user-requested operational detail.
- 2026-04-12: Refinement — user direction to **use current IBTs + learnings first**; survivor #2 rewritten as **data-first bootstrap** with gap-targeted DOE; Appendix **A0** added for program-first workflow.
