# Regression Test Baselines

These `.sto` fixtures are the reference outputs for `tests/test_setup_regression.py`.
Any code change that alters solver output must regenerate them.

`.sto` files are gitignored globally (see `.gitignore`); the GT3 baselines below
are force-added (`git add -f`) so they're committed despite the pattern.

## Regeneration Commands

```bash
# GTP baselines (require real IBT, not LFS pointer; not committed by default)
python -m pipeline.produce --car bmw \
    --ibt "data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt" \
    --wing 17 --sto tests/fixtures/baselines/bmw_sebring_baseline.sto

python -m pipeline.produce --car porsche \
    --ibt "ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt" \
    --fuel 58 --wing 17 --sto tests/fixtures/baselines/porsche_algarve_baseline.sto

# GT3 baselines (W9.2; --force bypasses calibration gate — GT3 is intercept-only)
python -m pipeline.produce --car bmw_m4_gt3 \
    --ibt "data/gt3_ibts/bmwm4gt3_spielberg gp 2026-04-26 21-34-43.ibt" \
    --force --sto tests/fixtures/baselines/bmw_m4_gt3_spielberg_baseline.sto

python -m pipeline.produce --car aston_martin_vantage_gt3 \
    --ibt "data/gt3_ibts/amvantageevogt3_spielberg gp 2026-04-26 21-25-55.ibt" \
    --force --sto tests/fixtures/baselines/aston_vantage_gt3_spielberg_baseline.sto

python -m pipeline.produce --car porsche_992_gt3r \
    --ibt "data/gt3_ibts/porsche992rgt3_spielberg gp 2026-04-26 21-42-39.ibt" \
    --force --sto tests/fixtures/baselines/porsche_992_gt3r_spielberg_baseline.sto
```

## Current Status

- **GTP baselines (BMW/Sebring + Porsche/Algarve):** Skipped in CI — IBT files
  are gitignored / LFS-only and not present in checkout. Tests skip gracefully
  when fixture or IBT is missing. Regenerate locally before merging
  solver-touching PRs.
- **GT3 baselines (3 cars at Spielberg):** Committed as of W9.2 (2026-04-27).
  IBT files at `data/gt3_ibts/*.ibt` are gitignored — drivers must re-capture
  to regenerate. The 3 baselines lock the Wave 1–8 intercept-only pipeline
  output. After varied-spring IBT capture lights up the W7.2 regression fits,
  these baselines will need regenerating.
