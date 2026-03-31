# Calibration Quality Delta (2026-03-31)

This note captures the impact of the telemetry fitter update made on 2026-03-31:

- File changed: `calibration/feature_builders.py`
- Function changed: `fit_linear_model()`
- Goal: avoid zero-fit outcomes caused by feature dimensionality exceeding sample constraints.

## What Changed

The fitter now:

- uses a simpler minimum sample gate (`min_samples=3`) rather than `samples >= features + 1`
- excludes the target from candidate features (prevents target leakage)
- drops constant features
- caps feature count (`max_features=24`)
- selects top features by absolute correlation when pruning is needed

## Before vs After

The table below compares telemetry model fit coverage before and after the fitter patch, using the same expanded dataset run.

| car | track | telemetry_samples | fitted_targets_before | fitted_targets_after | mean_rmse_before | mean_rmse_after |
|---|---|---:|---:|---:|---:|---:|
| bmw | sebring | 115 | 0 | 9 | null | 0.317710 |
| acura | hockenheim | 31 | 0 | 9 | null | 0.200668 |
| ferrari | hockenheim | 14 | 0 | 9 | null | 0.079304 |

## Targets Fitted After Patch

All three roots now fit the full default telemetry target set:

- `front_heave_travel_used_pct`
- `front_rh_excursion_measured_mm`
- `rear_rh_std_mm`
- `pitch_range_braking_deg`
- `front_braking_lock_ratio_p95`
- `rear_power_slip_ratio_p95`
- `body_slip_p95_deg`
- `understeer_low_speed_deg`
- `understeer_high_speed_deg`

## Notes

- Support tier remains `unsupported` in generated calibration reports; this patch improves fitter behavior but does not, by itself, satisfy tier-promotion requirements.
- Garage sample coverage is still sparse for BMW and Acura (`garage_samples = 0`), so garage and ride-height model quality remains constrained by data availability.
