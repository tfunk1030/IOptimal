## Calibration workflow

This document describes the easiest path for collecting and publishing setup
calibration data for a car/track pair.

> **Shell note for Windows / PowerShell users:** examples in this document use
> single-line commands so they can be pasted directly into PowerShell. If you
> prefer multiline PowerShell commands, use the backtick (`` ` ``) as the line
> continuation character. Do **not** use the Bash backslash (`\`) continuation
> style in PowerShell.

### 1. Seed baseline schema files

Generate editable schema seed files from the current setup registry:

```bash
python3 -m calibration.cli seed-schema-files --output-dir data/setup_schema
```

This creates:

- `data/setup_schema/bmw.json`
- `data/setup_schema/ferrari.json`
- `data/setup_schema/cadillac.json`
- `data/setup_schema/porsche.json`
- `data/setup_schema/acura.json`

These are editable seed files. They should be refined with real setup-row dumps
from the car's garage UI / IBT session data.

### 2. Create a raw sample pack

Create a scaffold for one sample:

```bash
python3 -m calibration.cli create-sample-pack \
  --car ferrari \
  --track sebring \
  --track-config international_raceway \
  --sample-id ferrari_sebring_pushrod_01 \
  --sample-type garage_static \
  --output-root data/calibration/raw
```

This creates a directory containing:

- `manifest.json`
- `setup_rows.json`
- optional `measured.json` for telemetry/validation samples
- `screenshots/`

### 3. Fill the raw sample pack

For every sample, provide as many of the following as possible:

- `setup_rows.json`
- `session.ibt`
- `setup.sto`
- screenshots of all relevant garage tabs
- metadata in `manifest.json`

### 4. Validate raw sample trees

Before ingesting, validate the raw tree:

```bash
python3 -m calibration.cli validate-raw-dataset \
  --raw-root data/calibration/raw/ferrari/sebring \
  --schema data/setup_schema/ferrari.json
```

This checks:

- `manifest.json` presence and required keys
- referenced artifacts exist
- schema path is accepted as input

### 5. Ingest raw samples to normalized JSONL

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

### 6. Fit models

Garage model:

```bash
python3 -m calibration.cli fit-garage-model \
  --car ferrari \
  --track sebring \
  --samples data/calibration/normalized/ferrari/sebring/garage_samples.jsonl \
  --out data/calibration/models/ferrari/sebring/garage_model.json
```

Ride-height model:

```bash
python3 -m calibration.cli fit-ride-height-model \
  --car ferrari \
  --track sebring \
  --samples data/calibration/normalized/ferrari/sebring/garage_samples.jsonl \
  --out data/calibration/models/ferrari/sebring/ride_height_model.json
```

Telemetry model:

```bash
python3 -m calibration.cli fit-telemetry-model \
  --car ferrari \
  --track sebring \
  --samples data/calibration/normalized/ferrari/sebring/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/sebring/telemetry_model.json
```

Damper model:

```bash
python3 -m calibration.cli fit-damper-model \
  --car ferrari \
  --track sebring \
  --samples data/calibration/normalized/ferrari/sebring/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/sebring/damper_model.json
```

Diff/TC model:

```bash
python3 -m calibration.cli fit-diff-model \
  --car ferrari \
  --track sebring \
  --samples data/calibration/normalized/ferrari/sebring/telemetry_samples.jsonl \
  --out data/calibration/models/ferrari/sebring/diff_model.json
```

### 7. Validate and publish

```bash
python3 -m calibration.cli validate-models \
  --car ferrari \
  --track sebring \
  --garage-model data/calibration/models/ferrari/sebring/garage_model.json \
  --rh-model data/calibration/models/ferrari/sebring/ride_height_model.json \
  --telemetry-model data/calibration/models/ferrari/sebring/telemetry_model.json \
  --validation-samples data/calibration/normalized/ferrari/sebring/telemetry_samples.jsonl \
  --report-dir data/calibration/models/ferrari/sebring
```

Then publish:

```bash
python3 -m calibration.cli publish-models \
  --car ferrari \
  --track sebring \
  --model-root data/calibration/models/ferrari/sebring
```

This makes the runtime loaders able to consume:

- `garage_model.json`
- `ride_height_model.json`
- `telemetry_model.json`
- `damper_model.json`
- `diff_model.json`
- `support_tier.json`

### 8. Runtime usage

After publication, runtime uses:

- `CarModel.active_garage_output_model(track_name)`
- `CarModel.active_ride_height_model(track_name)`
- `output.report._load_support_tier(...)`
- `solver.predictor.predict_candidate_telemetry(...)`

No code changes are required after the artifacts are published.
