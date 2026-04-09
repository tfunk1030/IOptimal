# Regression Test Baselines

These `.sto` fixtures are the reference outputs for `tests/test_setup_regression.py`.
Any code change that alters solver output must regenerate them.

## Regeneration Commands

```bash
# BMW/Sebring baseline (requires real IBT, not LFS pointer)
python -m pipeline.produce --car bmw \
    --ibt "data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt" \
    --wing 17 --sto tests/fixtures/baselines/bmw_sebring_baseline.sto

# Porsche/Algarve baseline (requires real IBT, not LFS pointer)
python -m pipeline.produce --car porsche \
    --ibt "ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt" \
    --fuel 58 --wing 17 --sto tests/fixtures/baselines/porsche_algarve_baseline.sto
```

## Current Status

Baselines need initial generation. The tyre compliance fix (per-axle
`tyre_vertical_rate_front/rear_nmm` now wired into excursion calculations)
changes solver output vs any prior `.sto` files. IBT files are stored in
Git LFS — ensure LFS is pulled before regenerating.
