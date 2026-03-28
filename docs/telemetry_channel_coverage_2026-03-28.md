# Telemetry Channel Coverage Audit (2026-03-28)

## Purpose
Classify telemetry by how it currently affects the program:
- `solve-critical`
- `diagnostic-only`
- `context-only`
- `unused or effectively unused`

## Solve-critical today
These channels or derived signals materially influence setup output, legality floors, or supporting settings.

### Platform / chassis
- front/rear ride heights
- splitter ride height
- heave shock deflection
- corner shock velocities
- heave shock velocities
- pitch / pitch rate
- roll / roll rate
- derived front heave travel usage

### Handling / stability
- steering angle
- yaw rate
- longitudinal/lateral velocity
- wheel speeds
- throttle / brake traces
- derived understeer
- derived body slip
- front braking lock ratio
- rear power slip ratio

### Brake channels
- per-corner brake line pressure
- ABS active / cut percentage
- derived decel asymmetry and hydraulic split

### Tyres actively used
- hot and cold pressures
- selected carcass/surface temperature aggregates used in support reasoning and decision traces

### Additional support context with real solve influence
- fuel level
- ERS battery percentage / level
- MGU-K torque peak
- selected in-car adjustment channels (especially brake bias / traction-control context)

## Diagnostic-only or lightly solve-coupled
These are read and analyzed, but they mostly affect diagnosis, notes, traces, or multi-session reasoning rather than the main solve output.

- detailed tyre temperature spreads beyond coarse aggregates
- tyre wear channels
- RPM
- gear
- many extended live adjustment counters
- session-comparison normalization metrics

## Context-only
These are read mostly for comparability, environment summary, or pressure correction.

- air temperature
- track temperature
- air density
- wind velocity
- wind direction

## Unused or effectively unused today
These are documented or extracted but do not appear to materially change the main solver output.

- precipitation
- track wetness
- weather declared wet
- steering wheel torque
- vertical velocity (`VelocityZ`)
- MGU-H power
- MGU-K power
- MGU-K lap deploy percentage
- fuel-use-per-hour
- odometer

## Implications
1. The solver already uses a meaningful subset of telemetry for platform control, balance, braking, and pressure decisions.
2. The largest telemetry gap is not raw ingestion; it is that many channels stop at analysis or reporting and do not become solve-time constraints or ranking signals.
3. Weather/wetness, tyre wear, energy/deploy detail, and aero-environment channels are the strongest candidates for the next wave of solver-grade integration.

## Recommended follow-up
1. Create a machine-readable telemetry registry with fields:
   - channel name
   - source channel(s)
   - derived metric(s)
   - classification
   - consuming modules
   - decision impact
2. Add tests that fail when a telemetry channel is moved or removed without updating the registry.
3. Make reports explicit about which channels materially changed the proposed setup.
