## IOptimal usage and calibration guide

If you only want the shortest path to get started, read:

- `docs/quickstart.md`

This guide is for two audiences:

1. **Users / drivers / engineers** who want to run the solver, diagnose a car,
   export a setup, and collect calibration data correctly.
2. **Future coders** who need to understand how the runtime and calibration
   pipeline fit together so they can extend the system safely.

The implementation truth in this repo is:

```text
IBT -> track_model/build_profile
    -> analyzer/extract + segment + driver_style + diagnose
    -> solver/modifiers
    -> solver/solve_chain.run_base_solve
    -> legality / garage validation
    -> report / JSON / .sto
```

Authoritative telemetry-backed runtime entrypoint:

- `python3 -m pipeline.produce`

Authoritative calibration CLI:

- `python3 -m calibration.cli`

---

## 1. Support tiers and what they mean

Do not treat all cars the same.

### Calibrated
- Full or near-full garage truth exists
- Runtime legality and RH reconciliation are meaningful
- Objective/ranking evidence exists for at least one car/track anchor

### Partial
- Setup parsing and some garage/output relationships exist
- Runtime may still rely on weaker approximations in some subsystems
- Export may be usable, but ranking and “best” claims should be treated carefully

### Exploratory
- Basic parsing and physics path exist
- Garage truth and telemetry calibration are incomplete
- Output may still be legal-range plausible without being garage-truth authoritative

### Unsupported
- The code may know the car name, but runtime confidence is too weak to make
  strong setup claims

As of the current repo state, BMW/Sebring remains the strongest path. Ferrari and
Acura are being improved but still require real calibration data to become
authoritative.

---

## 2. Common user workflows

### A. Diagnose one IBT and get a setup recommendation

```bash
python3 -m pipeline.produce \
  --car bmw \
  --ibt "path/to/session.ibt" \
  --wing 17 \
  --scenario-profile single_lap_safe \
  --sto output.sto
```

What this does:
- parses the setup from the IBT session info
- extracts telemetry and driver profile
- diagnoses balance/platform/grip issues
- runs the canonical solve chain
- applies legality / garage correlation checks
- exports a `.sto` if requested

Useful flags:
- `--lap <n>`: choose a specific lap instead of best lap
- `--json <path>`: save structured output
- `--report-only`: suppress intermediate step printing
- `--free`: run legal-manifold search from the pinned baseline
- `--scenario-profile quali|sprint|race`
- `--no-learn`: disable learner corrections/ingestion

### B. Analyze only, no `.sto`

```bash
python3 -m analyzer --car ferrari --ibt "path/to/session.ibt"
```

This uses the production pipeline but prints a report instead of focusing on
export.

### C. Track-only solve when no IBT exists

```bash
python3 -m solver.solve \
  --car cadillac \
  --track silverstone \
  --wing 15 \
  --scenario-profile single_lap_safe
```

Use this only when you do **not** have telemetry yet. It is weaker because:
- no measured setup diagnosis exists
- no driver profile exists
- no session-specific correction loop exists

### D. Multi-IBT reasoning path

```bash
python3 -m pipeline.produce \
  --car bmw \
  --ibt run1.ibt run2.ibt run3.ibt \
  --wing 17 \
  --scenario-profile sprint
```

This routes into `pipeline/reason.py::reason_and_solve`.

Use it when:
- you want to compare multiple recent experiments
- you want candidate-family reasoning from several sessions

---

## 3. How to get the best runtime output as a user

### Use the right inputs
- Prefer a **clean push lap**
- Avoid pit-out / traffic / cooldown laps
- Use correct wing if the IBT setup doesn’t expose it cleanly
- Keep fuel realistic for the intended use case

### Choose the right scenario
- `single_lap_safe`: default, conservative
- `quali`: more aggressive single-lap pace
- `sprint`: compromise setup
- `race`: stronger stability / robustness penalties

### Understand what the tool can and cannot claim
- If support tier is not calibrated, treat output as **engineering guidance**,
  not proven optimum
- If legality tier is range-clamp only, the setup may be in-range without being
  fully correlated to the garage display model

---

## 4. How to collect good calibration data

Calibration quality depends more on **dataset quality** than on fitting code.

### Every useful raw sample should contain
- `manifest.json`
- `setup_rows.json`
- `session.ibt` if available
- `setup.sto` if available
- screenshots of garage tabs if available

### Use the sample-pack scaffold

Create one sample pack:

```bash
python3 -m calibration.cli create-sample-pack \
  --root-dir data/calibration/raw \
  --car ferrari \
  --track sebring \
  --sample-id ferrari_sebring_pushrod_01 \
  --sample-type garage_static
```

This creates a structured folder with placeholders and a valid manifest.

### Validate raw trees before ingest

```bash
python3 -m calibration.cli validate-raw-dataset \
  --raw-root data/calibration/raw/ferrari/sebring \
  --schema data/setup_schema/ferrari.json
```

Use this before fitting so you catch:
- missing artifacts
- bad manifests
- missing screenshot/IBT/STO references
- Ferrari alias/coverage issues

### Best practice for calibration collection

#### For garage correlation
Collect **static setup sweeps**:
- pushrod sweeps
- heave spring sweeps
- perch sweeps
- torsion/OD/preload sweeps
- interaction points

#### For telemetry correlation
Collect **controlled telemetry sessions**:
- change only one subsystem at a time when possible
- 3–5 push laps per setup
- stable weather/session conditions

---

## 5. Schema workflow for users and data curators

### Seed schema files

```bash
python3 -m calibration.cli seed-schema-files --output-dir data/setup_schema
```

This generates:
- `data/setup_schema/bmw.json`
- `data/setup_schema/ferrari.json`
- `data/setup_schema/cadillac.json`
- `data/setup_schema/porsche.json`
- `data/setup_schema/acura.json`

These are **seed schemas**, not proof of final calibration.

### Bootstrap from real row dumps

If you have exported setup-row JSON files:

```bash
python3 -m calibration.cli bootstrap-schema \
  --car ferrari \
  --input-glob "data/calibration/raw/ferrari/**/*.json" \
  --output data/setup_schema/ferrari.json
```

This refines the schema with observed labels/sections/ranges.

### Validate schema coverage

```bash
python3 -m calibration.cli validate-schema \
  --schema data/setup_schema/ferrari.json \
  --input-glob "data/calibration/raw/ferrari/**/*.json"
```

Use this to spot:
- unmapped runtime-relevant fields
- ambiguous labels
- fields that still need Ferrari/Acura-specific aliasing

---

## 6. Full calibration CLI workflow

### Step 1 — Ingest raw samples

```bash
python3 -m calibration.cli ingest-samples \
  --car ferrari \
  --track sebring \
  --raw-root data/calibration/raw/ferrari/sebring \
  --schema data/setup_schema/ferrari.json \
  --out-root data/calibration/normalized/ferrari/sebring
```

Outputs:
- `garage_samples.jsonl`
- `telemetry_samples.jsonl`

### Step 2 — Fit models

Garage:

```bash
python3 -m calibration.cli fit-garage-model \
  --car ferrari \
  --track hockenheim \
  --samples data/calibration/normalized/ferrari/hockenheim/garage_samples.jsonl \
  --out data/calibration/models/ferrari/hockenheim/garage_model.json
```

Ride height:

```bash
python3 -m calibration.cli fit-ride-height-model \
  --car ferrari \
  --track hockenheim \
  --samples data/calibration/normalized/ferrari/hockenheim/garage_samples.jsonl \
  --out data/calibration/models/ferrari/hockenheim/ride_height_model.json
```

Telemetry:

```bash
python3 -m calibration.cli fit-telemetry-model \
  --car ferrari \
  --track hockenheim \
  --samples data/calibration/normalized/ferrari/hockenheim/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/hockenheim/telemetry_model.json
```

Damper:

```bash
python3 -m calibration.cli fit-damper-model \
  --car ferrari \
  --track hockenheim \
  --samples data/calibration/normalized/ferrari/hockenheim/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/hockenheim/damper_model.json
```

Diff:

```bash
python3 -m calibration.cli fit-diff-model \
  --car ferrari \
  --track hockenheim \
  --samples data/calibration/normalized/ferrari/hockenheim/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/hockenheim/diff_model.json
```

### Step 3 — Validate

```bash
python3 -m calibration.cli validate-models \
  --car ferrari \
  --track hockenheim \
  --garage-model data/calibration/models/ferrari/hockenheim/garage_model.json \
  --rh-model data/calibration/models/ferrari/hockenheim/ride_height_model.json \
  --telemetry-model data/calibration/models/ferrari/hockenheim/telemetry_model.json \
  --validation-samples data/calibration/normalized/ferrari/hockenheim/telemetry_samples.jsonl \
  --report-dir data/calibration/models/ferrari/hockenheim
```

### Step 4 — Publish to runtime

```bash
python3 -m calibration.cli publish-models \
  --car ferrari \
  --track hockenheim \
  --model-root data/calibration/models/ferrari/hockenheim
```

This populates runtime artifacts the solver can consume automatically.

---

## 7. What runtime uses after publication

After published calibration artifacts exist, runtime uses:

- `CarModel.active_garage_output_model(track_name)`
- `CarModel.active_ride_height_model(track_name)`
- `calibration.runtime.load_support_tier(...)`
- `solver.predictor.predict_candidate_telemetry(...)`

This means:
- no code edits are required for the solver to consume published models
- later calibration improvements are mostly a **data + fit + publish** loop

Recent calibration quality delta reference:
- `docs/calibration_quality_delta_2026-03-31.md`

---

## 8. Future coder guide

This section is for developers extending the system.

### A. Do not add new car knowledge ad hoc in random modules
Prefer:
- `data/setup_schema/<car>.json`
- `calibration/`
- `car_model/setup_registry.py`
- runtime model publication

Avoid:
- one-off label parsing in unrelated modules
- hardcoding a new field in only one read/write path

### B. Preserve the layered design

The system now has these layers:

1. **Schema truth**
   - field names
   - units
   - ranges
   - aliases
   - roles

2. **Garage truth**
   - setup -> displayed garage outputs

3. **Telemetry truth**
   - setup -> telemetry change

4. **Runtime solve**
   - diagnosis/modifiers/solve_chain/legality/export

If a new feature crosses layers, update each layer deliberately.

### C. Car-specific normalization belongs in dedicated adapters
Ferrari now has:
- `calibration/ferrari_aliases.py`

If Acura or Porsche need architecture-specific logic, create:
- `calibration/acura_aliases.py`
- `calibration/porsche_aliases.py`

Do not overload one generic helper with dozens of special cases if the
architecture is genuinely different.

### D. Prefer data-driven runtime upgrades over hardcoded branching
If calibration artifacts can express it, prefer:
- publish model
- load model
- consume model

Only hardcode behavior in `car_model/cars.py` if:
- it is a safe fallback
- or there is no artifact yet

### E. Testing expectations
When extending:
- add fixture-backed normalization tests
- add scaffold/dataset validation tests
- add runtime loader tests
- add at least one integration-relevant solver/legality/report test if runtime changes

Recommended local test slices:

```bash
python3 -m unittest tests.test_calibration_pipeline tests.test_calibration_runtime
python3 -m unittest tests.test_calibration_scaffold tests.test_calibration_ferrari_workflow
python3 -m unittest tests.test_registry_consistency tests.test_ferrari_setup_schema tests.test_garage_validator
```

### F. Keep support-tier claims honest
Do not promote a car/track to calibrated because the parser works.

Minimum before stronger claims:
- schema coverage is good
- garage model exists and validates
- ride-height model exists
- telemetry model exists
- holdout validation is acceptable

### G. Known current limitations future coders should respect
- BMW/Sebring remains the strongest validated runtime path
- Ferrari workflow normalization is now materially better, but still needs real
  calibration datasets for authoritative garage truth
- Acura still needs a dedicated architecture-first workflow similar to Ferrari
- the calibration package is infrastructure-complete, not data-complete

---

## 9. Recommended next engineering steps

### Highest-value next steps
1. Acura-specific alias and flattening workflow
2. Schema-driven `CurrentSetup` parsing expansion
3. Schema-driven `setup_writer` cleanup
4. Real Ferrari garage sweep ingestion and model publication
5. Acura garage/output truth path

### If you are a user collecting data
Start with:
1. Ferrari static garage sweeps
2. Ferrari telemetry sweeps
3. publish Ferrari model artifacts
4. re-run runtime and evaluate exported setup quality

---

## 10. Quick command cheat sheet

### Run one telemetry-backed solve
```bash
python3 -m pipeline.produce --car ferrari --ibt session.ibt --wing 17 --sto output.sto
```

### Run one track-only solve
```bash
python3 -m solver.solve --car cadillac --track silverstone --wing 15
```

### Generate schema seeds
```bash
python3 -m calibration.cli seed-schema-files --output-dir data/setup_schema
```

### Create one sample pack
```bash
python3 -m calibration.cli create-sample-pack --root-dir data/calibration/raw --car ferrari --track sebring --sample-id ferrari_sebring_001 --sample-type garage_static
```

### Validate raw dataset
```bash
python3 -m calibration.cli validate-raw-dataset --raw-root data/calibration/raw/ferrari/sebring --schema data/setup_schema/ferrari.json
```

### Ingest raw dataset
```bash
python3 -m calibration.cli ingest-samples --car ferrari --track sebring --raw-root data/calibration/raw/ferrari/sebring --schema data/setup_schema/ferrari.json --out-root data/calibration/normalized/ferrari/sebring
```

### Publish validated runtime artifacts
```bash
python3 -m calibration.cli publish-models --car ferrari --track sebring --model-root data/calibration/models/ferrari/sebring
```
