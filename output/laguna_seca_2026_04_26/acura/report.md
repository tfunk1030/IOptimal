# Acura ARX-06 — WeatherTech Raceway Laguna Seca (2026-04-26)

## Summary

Pure-physics solver run for Acura at Laguna Seca. Acura has the LEAST telemetry coverage of all four cars (7 IBTs, none from Laguna Seca, aero maps not validated for non-Hockenheim tracks). Steps 1-3 ran cleanly. Steps 4-6 (ARBs, Geometry, Dampers) blocked because those subsystems have never been calibrated for Acura. **This setup is a starting baseline only — confidence is LOW.**

## Recalibration result

```
Sessions collected:   29  (was 22, +7 new — full re-ingest of all 7 Acura IBTs)
Unique setups:        8 / 5 minimum
Models fitted:        YES

Regression models (garage-output):
  [OK]   front_ride_height          R^2=1.000  RMSE=0.08mm  n=8
  [WEAK] rear_ride_height           R^2=0.151  RMSE=1.41mm  n=8   (was 0.748)
  [OK]   heave_spring_defl_static   R^2=0.928  RMSE=1.44mm  n=8
  [OK]   heave_spring_defl_max      R^2=1.000  RMSE=0.16mm  n=8

Component status:
  aero_compression : calibrated (4 sessions, front=7.1mm, rear=n/a)
  arb_stiffness    : insufficient data (need varied-ARB sessions)
  m_eff            : constant (5 points, 829 kg front / 256 kg rear)
  lltd_target      : DISABLED (IBT proxy is geometric, not real LLTD)
  deflection_model : calibrated (best R^2=1.00)
```

The rear_ride_height R^2 got WORSE during re-ingestion (0.748 -> 0.151). The two new Daytona Road IBTs (rear RH 44.9 mm vs Hockenheim's 41-46 range) introduced cross-track variance the per-track-agnostic model cannot explain. Per-track Hockenheim model still hits R^2=1.00. The global rear RH model is unreliable — solver Step 1 rear pushrod prediction at Laguna Seca is **not trustworthy**.

Per-track model `models_hockenheim.json` was also refit (7 setups, R^2=1.000).

## Pipeline result

| Step | Status | Notes |
|------|--------|-------|
| 1 — Rake/RH | ran | Front static 30.1 mm, rear static 43.4 mm. Wing 8.0 deg. DF balance 48.99% (target 49.00%). L/D 3.781. **Aero map not Acura-Laguna validated.** |
| 2 — Heave/Third | ran | Front heave 90 N/mm, rear third 60 N/mm. |
| 3 — Corner Springs | ran | Torsion bar 15.14 mm OD (front), rear coil 30 N/mm raw spring. |
| 4 — ARBs | **BLOCKED** | `arb_stiffness` uncalibrated. Need >=5 sessions with varied front/rear ARB blade settings. |
| 5 — Geometry | **BLOCKED** | Cascade-blocked from Step 4 (geometry uses k_roll_total). Camber falls back to baseline -3.0/-2.0 deg. |
| 6 — Dampers | **BLOCKED** | `damper_zeta` uncalibrated. Solver leaves dampers at iRacing garage defaults (NO `CarSetup_Dampers_*` written to .sto). |

## Setup highlights

```
Front static RH:   30.1 mm
Rear static RH:    43.4 mm
Rake (static):     13.3 mm
Front pushrod:    -37.5 mm
Rear pushrod:     -29.0 mm

Heave spring:      90 N/mm  (perch +0 mm)
Rear 3rd spring:   60 N/mm  (perch +85 mm -> clamped to +55 mm by garage validator)
Front torsion:     15.14 mm OD, 0 turns   WARNING: exceeds 14.76 mm bottom-out limit
Rear coil:         30 N/mm   (snapped to 100 N/mm by garage validator — raw rate too low)

ARBs:              [BLOCKED — uncalibrated]
Geometry camber:   -3.0 F / -2.0 R deg  (defaults, not solved)
Dampers:           [BLOCKED — iRacing garage defaults]

Brake bias:        46.0%
Diff:              30 Nm preload, 45/70 ramps, 6 plates
TC:                gain 4 / slip 3
Tyre cold:         152 kPa all corners
```

### Garage validator clamps (red flags)
- `rear_third_perch`: 85.0 -> 55.0 mm clamped
- `rear_spring_rate`: 30 -> 100 N/mm snapped (Acura raw rear spring range floors at 100 N/mm)

These clamps mean the solver is asking for spring rates outside the legal garage range — usually a sign the underlying model targets are off (likely the Step 1 rear RH issue cascading through Step 3).

### Front torsion bar bottom-out warning
Per CLAUDE.md, "Front heave damper bottoms at torsion OD >= 14.76 mm." Solver picked 15.14 mm. **The setup may bottom the front heave damper on Laguna's compressions (T1 downhill, T6 corkscrew exit).**

## Predicted competitiveness for Laguna Seca

**LOW confidence — likely should NOT be the best-car recommendation.**

Reasons:
1. **Zero Laguna Seca telemetry for Acura.** Aero map interpolation is unvalidated outside Hockenheim conditions.
2. **Rear RH model R^2=0.15 globally** — Step 1 rear pushrod is essentially a guess.
3. **Steps 4, 5, 6 all blocked.** No physics-derived ARB, geometry, or damper values. The solver leaves these at iRacing garage defaults, which means the car will run Acura's stock baseline ARB/camber/damper settings — fine for warmup, not competitive.
4. **Garage clamps** indicate the spring/perch targets are outside legal range — driver will need to manually adjust.
5. **Front torsion bar bottom-out risk** at 15.14 mm OD on Laguna's elevation changes.

If forced to race the Acura at Laguna, the driver should:
- Lower torsion OD to <=14.5 mm to clear the bottom-out limit.
- Manually set ARB blades from feel (Step 4 has no recommendation).
- Expect to chase rear ride height through the session (Step 1 rear is unreliable).

## Blockers (calibration needed)

To make Acura competitive at any track, run sessions and ingest:
1. **ARB sweep** — 5+ sessions with varied front and rear ARB blade settings (current: 0 such sessions). Blocks Steps 4 and 5.
2. **Damper zeta calibration** — set `zeta_is_calibrated=True` in car model OR run ARB+damper varied sessions to back-solve zeta. Blocks Step 6.
3. **Rear torsion bar C constant** — currently borrowed from BMW. Need 5+ varied torsion-bar OD setups (Acura uses indexed torsion bars). Would improve corner spring prediction confidence.
4. **Aero map validation for Laguna Seca** — needs 1+ Acura/Laguna IBT to verify front_RH x rear_RH x wing -> DF balance and L/D match the spreadsheet.
5. **Rear RH global model regression** — adding Daytona data dropped R^2 from 0.748 to 0.151. Per-track models help but global solver still uses the global model. Consider per-track-only calibration switch for cars with sparse data.

## Files

- `setup.sto` — iRacing setup file (3.9 KB, no damper section since Step 6 blocked)
- `setup.json` — Full solver JSON output (no `calibration_provenance` field — that's pipeline.produce-only; we ran solver.solve directly because Acura has no Laguna IBT)
- `report.md` — This file

## Code changes in this PR

- `car_model/cars.py`: Acura `supported_track_keys` extended from `("hockenheim",)` to `("hockenheim", "laguna_seca")`. Per CLAUDE.md key principle 7, calibration is car-specific not track-specific; opening a new track does not invalidate models. The aero map module already supports Laguna RH/wing queries (interpolation).
- Recalibration JSONs at `data/calibration/acura/{calibration_points.json, models.json, models_hockenheim.json}` updated from re-ingesting all 7 IBTs.
