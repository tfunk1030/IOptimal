# Cadillac V-Series.R — Calibration Gap (Laguna Seca race-week skip)

**Date:** 2026-04-26
**Author:** Race-week recalibration batch, Unit 5 (Cadillac documentation)
**Status:** SKIPPED for Weathertech Raceway Laguna Seca race-week setup batch.

## 1. Summary

The Cadillac V-Series.R **cannot be calibrated for any track — including
Weathertech Raceway Laguna Seca — using the telemetry currently on disk.** Only
**one** Cadillac IBT exists (Silverstone 2019 GP, 2026-03-17), and the
calibration model store
(`data/calibration/cadillac/models.json`,
file_path: `C:\Users\VYRAL\IOptimal\data\calibration\cadillac\models.json`) is a
zero-session stub with `status: "uncalibrated"` for every output. Per CLAUDE.md
Key Principle 7 ("Calibrated or instruct, never guess"), running the pipeline
for Cadillac/Laguna Seca would either (a) block at every solver step with
calibration instructions, or (b) produce a setup that is silently anchored to
BMW defaults — exactly the silent-fallback failure mode CLAUDE.md Key
Principle 8 forbids. **The user should either race a different car this week
(BMW, Porsche, Ferrari, or Acura per the parallel PRs in this batch) or accept
that any Cadillac setup is unanchored to physics for this car.**

---

## 2. Current calibration state

### `data/calibration/cadillac/`

| File | Size | Notes |
|------|------|-------|
| `models.json` | 304 B | Stub — `n_sessions=0`, `status="uncalibrated"`, all output fields `null` |
| `calibration_points.json` | 4.8 KB | 2 entries — both from the same Silverstone IBT (one tagged `"competitive"`, one tagged `"scan"` with `lap_time_s=0`); effectively **1 unique session** |

Full content of `models.json`:

```json
{
  "car": "cadillac",
  "n_sessions": 0,
  "n_unique_setups": 0,
  "calibration_complete": false,
  "status": "uncalibrated",
  "front_ride_height": null,
  "rear_ride_height": null,
  "front_deflection": null,
  "rear_deflection": null,
  "aero_compression": null,
  "damper_zeta": null
}
```

Note: `models.json` reports `n_sessions=0` even though `calibration_points.json`
holds 2 rows. This is because the auto-calibrator ran but rejected the data —
both rows describe the **same setup**, so there is zero variation to fit
regressions against.

### `data/learnings/`

| File | Sessions | Notes |
|------|----------|-------|
| `models/cadillac_global_empirical.json` | 3 | Aero compression front 11.15 mm, rear 25.93 mm, m_eff_front 286.9 kg. Confidence labelled `"low"`. No roll-gradient signal. |
| `models/cadillac_silverstone_empirical.json` | 1 | Empty `relationships`, empty `corrections`, no sensitivity entries — model has no useful per-track knowledge. |
| `observations/cadillac_silverstone_circuit_cadillacvseriesrgtp_silverstone_2019_gp_2026-03-17_19-58-55.json` | 1 | Single observation, single setup. |

There are **no Cadillac entries for Laguna Seca, Sebring, Algarve, Hockenheim, or
any other track** in either the calibration points store or the learner.

---

## 3. Available telemetry inventory

Single IBT on disk:

| Path | Size | Date | Track | Wing | Lap time |
|------|------|------|-------|------|----------|
| `C:\Users\VYRAL\IOptimal\ibtfiles\cadillac\cadillacvseriesrgtp_silverstone 2019 gp 2026-03-17 19-58-55.ibt` | 184.8 MB | 2026-03-17 17:56 | Silverstone Circuit | 17° | 105.76 s (one row) |

What this IBT captured (from the calibration_points entry):

- **One setup snapshot only.** Front heave = 40, rear third = 680, rear spring = 140 N/mm, front torsion OD = 14.76 mm, front pushrod = -33.5 mm, rear pushrod = +0.5 mm, F-ARB Soft/1, R-ARB Medium/5.
- **Static weights:** LF 2750 / RF 2750 / LR 2920 / RR 2920 N (≈ 48.5 % front, matches `cars.py:2164`).
- **One dynamic-RH point:** front 17.76 mm, rear 28.20 mm; one σ measurement (front 5.18 mm, rear 5.86 mm).
- **One LLTD proxy reading:** 0.5047 — but per CLAUDE.md "LLTD epistemic gap" this is a geometric ratio insensitive to spring stiffness, **not** a real LLTD measurement.

**There is no setup variation. A single point cannot fit a regression.**

---

## 4. Gap analysis per solver step

Required subsystems are taken from
`car_model/calibration_gate.py:758-765` (`STEP_REQUIREMENTS`). Each row lists
what is missing for Cadillac and the rough number of additional varied IBT
sessions needed to unblock that subsystem.

| Step | Subsystem | Minimum data to unblock | Cadillac current | Sessions still needed |
|------|-----------|-------------------------|-------------------|-----------------------|
| 1 | `aero_compression` | 3+ varied-RH IBT laps at speed (V²-RMS ≥ ~150 kph) on a single track | 1 IBT, 1 setup; learner mean already in `cars.py:2185` flagged `is_calibrated=True` from 2 sessions, but stub `models.json` says null. **Mixed/contradictory.** | 2–4 more, ideally on the target track (Laguna Seca) |
| 1 | `ride_height_model` | 5+ varied-pushrod / varied-perch / varied-spring IBT setups; auto-cal threshold is `n_samples >= 3 × n_features` (CLAUDE.md 2026-04-10 overfitting fix) | `RideHeightModel.uncalibrated()` — `cars.py:2255`. Front model has a 2-variable approximation in `PushrodGeometry` (4 garage points, ±1.5 mm — see comment at `cars.py:2258-2266`) but **no IBT-fit static-RH regression** | 5–8 (with deliberate variation in pushrod, heave perch, OD, torsion turns, camber) |
| 1 | `pushrod_geometry` | 4+ garage points with varied pushrod/perch | Calibrated to ±1.5 mm from 4 garage points (Silverstone, see `cars.py:2189-2200`). Status borderline **weak** (no R² recorded, only 4 points, single track). | 0 for unblock, 4+ for confidence promotion |
| 2 | `spring_rates` | 5+ varied-spring IBT sessions for heave/third regression | None. Single observed `front_heave=40, rear_third=680, rear_spring=140`. Cannot fit. | 5+ |
| 3 | `spring_rates` (corner) | Same dataset as Step 2 plus rear-spring sweep | None | 5+ (same dataset as Step 2) |
| 4 | `arb_stiffness` | ARB blade sweep across 5+ IBT sessions with constant springs to extract roll-stiffness gradient via auto-cal back-solve | None. ARB hardware values in `cars.py:2223-2238` are **propagated from BMW** (`Dallara platform — same as BMW`) — not measured on Cadillac. | 5+ varied-ARB sessions |
| 4 | `lltd_target` | EITHER (a) wheel-force telemetry channels (not currently exposed by iRacing IBT), OR (b) 10+ varied per-axle ARB sessions with lap-time correlation, OR (c) car-specific OptimumG/Milliken physics inputs (tyre sensitivity confirmed for the Cadillac compound) | The single `lltd_measured=0.5047` is the geometric proxy CLAUDE.md flagged 2026-04-08 — **not a real LLTD**. Tyre sensitivity in `cars.py:2170` is labelled `ESTIMATE`. No physics-derived target written. | 10+ varied-ARB sessions (see "LLTD epistemic gap" in CLAUDE.md) |
| 5 | `roll_gains` | Geometry roll-gain calibration from 3+ varied-camber IBT laps with lateral-g segmentation | None. Roll-gain values `front_roll_gain=0.60, rear_roll_gain=0.50` in `cars.py:2242-2243` are inherited "Dallara platform — same as BMW". | 3+ varied-camber sessions |
| 6 | `damper_zeta` | Per-corner damper sweep across 5+ IBT sessions with constant springs; needs `zeta_is_calibrated=True` set on `DamperModel`. (Same blocker as Porsche Step 6.) | None. `DamperModel` at `cars.py:2245-2254` has BMW-scale clicks but **no zeta calibration field set**. | 5+ varied-click sessions |
| Supporting | brake bias, diff preload, TC, tyre pressures | Driver style + measured slip; tolerable on smaller datasets | Brake bias 47.5 % from the 1 IBT (`cars.py:2165`), nothing else measured | 3+ sessions |

### Cascade impact on Laguna Seca

`car_model/calibration_gate.py` cascades blocks: `{2→1, 3→2, 4→3, 5→4, 6→3}`.
For Cadillac that means **all 6 steps would block at runtime today**:

- Step 1 blocks on `ride_height_model` (uncalibrated).
- Step 2 blocks on `spring_rates`, AND cascades from Step 1.
- Step 3 blocks on `spring_rates`, AND cascades from Step 2.
- Step 4 blocks on `arb_stiffness` + `lltd_target`, AND cascades from Step 3.
- Step 5 blocks on `roll_gains`, AND cascades from Step 4.
- Step 6 blocks on `damper_zeta`, AND cascades from Step 3.

The pipeline would print 6 `STEP N: ... — BLOCKED` blocks and emit no setup
values. Per CLAUDE.md Key Principle 7 this is the **correct** behaviour.

---

## 5. Recommendation

### Short-term (this race week)

1. **Do not attempt to ship a Cadillac setup for Laguna Seca.** Pick one of the
   four cars handled by the parallel PRs in this batch:
   - BMW M Hybrid V8 (6/6 steps calibrated for Sebring; Laguna Seca will run from
     calibrated subsystems where they generalise — ride-height model and aero
     compression need re-fit per track but the rest carries).
   - Porsche 963 (5/6 steps; Step 6 still blocks anywhere — CLAUDE.md 2026-04-10).
   - Ferrari 499P (1/6, weak — only Step 1 partially runs; treat with caution).
   - Acura ARX-06 (3/6 steps for Hockenheim — Steps 1–3 runnable, 4–6 blocked).
2. If a Cadillac setup is non-negotiable, **load a known-good driver setup in
   iRacing manually** and accept that the solver cannot anchor to it. Do **not**
   trust any pipeline output that surfaces under those conditions — it will be
   BMW defaults extrapolated.

You can confirm the block behaviour with:

```bash
python -m solver.solve --car cadillac --track weathertech_raceway_laguna_seca
```

The output will be a calibration-instructions block, not a setup.

### Medium-term (Cadillac calibration plan)

To get Cadillac to BMW/Sebring parity (6/6 calibrated) on **any** track, the
user needs roughly **15–25 deliberately-varied IBT sessions** on a single track,
distributed approximately:

- 5–8 sessions varying front pushrod / heave perch / torsion OD (Step 1 RH model)
- 5+ sessions varying heave + third + rear spring (Steps 2–3)
- 5+ sessions varying ARB blade with constant springs (Step 4)
- 3+ sessions varying camber (Step 5)
- 5+ sessions varying damper clicks (Step 6)

Some sessions can serve multiple steps if the setup deltas are orthogonal.
Practically: **1–2 weeks of focused practice** on a single track with a
spreadsheet of intended setup variations, then `python -m learner.ingest` per
session and `python -m car_model.auto_calibrate --car cadillac` to refit.

The existing Silverstone IBT is a reasonable seed — recommend continuing on
**Silverstone Circuit** for the calibration sweep (existing baseline, already
have aero map in `data/aeromaps/`), then validating cross-track on Sebring or
Algarve before trusting Laguna Seca runs.

---

## 6. Why this matters for Laguna Seca race week

CLAUDE.md Key Principle 8 ("No silent fallbacks"):

> Every value the solver uses must come from one of: (a) measured data with
> R² ≥ 0.85, (b) first-principles physics computation, (c) car-specific hand
> calibration with explicit warning. The user explicitly asked for "no
> fallbacks to baselines or hardcoded values".

The Cadillac car model in `car_model/cars.py:2152-2267` is **dense with BMW
inheritance** that satisfies none of (a)/(b)/(c) in a Cadillac-specific sense:

- ARB stiffness arrays `[0, 5500, 11000, 16500]` and `[1500, 3000, 4500]` are
  copy-pasted from BMW with the comment `"Dallara platform — same as BMW
  verified"` (`cars.py:2227, 2231`) — but no Cadillac-side IBT verification
  exists.
- Roll gains `0.60 / 0.50` are inherited (`cars.py:2242-2243`).
- Damper click ranges and force-per-click are inherited (`cars.py:2245-2254`).
- `front_torsion_c=0.0008036` is BMW's value (`cars.py:2212`).
- `track_width_mm` and `cg_height_mm` are explicitly tagged ESTIMATE
  (`cars.py:2220-2221`).
- `tyre_load_sensitivity=0.20` is ESTIMATE (`cars.py:2170`).

If the pipeline ran through the calibration gate without strict mode, every
parameter the solver "computed" for Cadillac/Laguna Seca would actually be
BMW-derived. That's the exact failure mode the strict gate exists to prevent.
**Skipping Cadillac for this race week is the honest engineering choice.** The
parallel PRs in this batch each represent a car where at least Step 1 has real
Cadillac/Porsche/Ferrari/Acura-specific calibration; Cadillac does not.

---

## References

- CLAUDE.md (project root) — Key Principles 7, 8, 9; "Current calibration
  status (2026-04-10)"; "🚨 LLTD CALIBRATION GAP (2026-04-08)".
- `car_model/calibration_gate.py:758-765` — `STEP_REQUIREMENTS` table.
- `car_model/cars.py:2152-2267` — Cadillac V-Series.R definition.
- `skill/per-car-quirks.md:287-353` — Cadillac chapter (chassis context, no
  calibration data).
- `data/calibration/cadillac/models.json` — calibration stub.
- `data/calibration/cadillac/calibration_points.json` — single-setup observations.
- `data/learnings/models/cadillac_global_empirical.json` — `confidence: "low"`.
- `ibtfiles/cadillac/cadillacvseriesrgtp_silverstone 2019 gp 2026-03-17 19-58-55.ibt`
  — sole IBT (184.8 MB, 2026-03-17).
