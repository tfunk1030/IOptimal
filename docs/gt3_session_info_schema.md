# GT3 Session Info Schema — Discovered from IBT YAML

This doc captures the actual `CarSetup` YAML schema iRacing emits for GT3 cars, derived from two real session_info dumps:

- BMW M4 GT3 EVO at Spielberg / Red Bull Ring (2026-04-26 21:34) → [docs/gt3_session_info_bmw_m4_gt3_spielberg_2026-04-26.yaml](gt3_session_info_bmw_m4_gt3_spielberg_2026-04-26.yaml)
- Aston Martin Vantage GT3 EVO at Spielberg / Red Bull Ring (2026-04-26 21:25) → [docs/gt3_session_info_aston_vantage_spielberg_2026-04-26.yaml](gt3_session_info_aston_vantage_spielberg_2026-04-26.yaml)
- Porsche 911 GT3 R (992) at Spielberg / Red Bull Ring (2026-04-26 21:42) → [docs/gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml](gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml)

This is the source-of-truth schema for `output/setup_writer.py` GT3 PARAM_IDS dicts when Phase 2 wires them up.

## Top-level structure (consistent across both cars)

```
CarSetup:
  UpdateCount: <int>
  TiresAero:
    TireType: { TireType: "Dry" }
    LeftFront:  { StartingPressure, LastHotPressure, LastTempsOMI/IMO, TreadRemaining }
    LeftRear:   { ... }
    RightFront: { ... }
    RightRear:  { ... }
    <AeroBalanceCalc | AeroBalanceCalculator>:    # NAME VARIES PER CAR
      FrontRhAtSpeed:   <mm>
      RearRhAtSpeed:    <mm>
      <WingSetting | RearWingAngle>:              # NAME VARIES PER CAR
      FrontDownforce:   <%>
  Chassis:
    <FrontBrakes | FrontBrakesLights>:            # NAME VARIES PER CAR
      <ArbBlades | FarbBlades>:                   # NAME VARIES PER CAR
      TotalToeIn:                                 # paired front toe
      FrontMasterCyl:
      RearMasterCyl:
      BrakePads:                                  # "Low friction" / "Medium friction" / "High friction"
      CenterFrontSplitterHeight:                  # NEW garage param vs GTP
      EnduranceLights:                            # ASTON ONLY (so far)
      NightLedStripColor:                         # ASTON ONLY
    LeftFront:
      CornerWeight:
      RideHeight:
      BumpRubberGap:                              # NEW garage param vs GTP
      SpringRate:
      Camber:
    LeftRear:
      ... + ToeIn  (rear toe per-wheel, not total)
    Rear:
      FuelLevel:
      <ArbBlades | RarbBlades>:                   # NAME VARIES
      <WingAngle | RearWingAngle>:                # NAME VARIES
    InCarAdjustments:
      BrakePressureBias:
      AbsSetting:                                 # "<n> (ABS)"
      TcSetting:                                  # "<n> (TC)" or "<n> (TC SLIP)"
      ThrottleResponse:                           # ASTON ONLY (so far)
      EpasSetting:                                # ASTON ONLY (electronic power steering)
      FWtdist:                                    # %  ← REAL FRONT WEIGHT DISTRIBUTION
      CrossWeight:                                # %
    RightFront:    (mirror of LeftFront)
    RightRear:     (mirror of LeftRear, with ToeIn)
    GearsDifferential:
      GearStack:                                  # "FIA" / "Daytona" / etc.
      FrictionFaces:                              # 2/4/6/8/10
      DiffPreload:                                # Nm
  Dampers:
    FrontDampers:                                 # ⚠️  PER-AXLE, not per-corner
      LowSpeedCompressionDamping:                 # clicks
      HighSpeedCompressionDamping:
      LowSpeedReboundDamping:
      HighSpeedReboundDamping:
    RearDampers:
      LowSpeedCompressionDamping:
      ...
```

## Critical finding: GT3 dampers are PER-AXLE, not per-corner

The iRacing user manuals describe GT3 dampers as "4-way per corner (LSC/HSC/LSR/HSR)" — implying 16 separate adjusters. **The IBT YAML reveals iRacing actually exposes them as 8 adjusters: 4 channels × 2 axles.** Left/right are physically tied together in the setup UI.

This is a meaningful simplification compared to the GTP class (where the IBT exposes per-corner dampers). The solver's `DamperModel` and `setup_writer.py` need to handle this at axle-granularity for GT3 cars, not at corner-granularity.

| Car | Damper exposure | LSC/HSC/LSR/HSR channels |
|---|---|---|
| GTP class (BMW, Porsche, Ferrari, ...) | Per-corner | 16 channels |
| GT3 class | **Per-axle** | **8 channels** |

This is consistent across BMW M4 GT3 and Aston Vantage IBTs. Likely consistent across all 11 GT3 cars; verify when more IBTs land.

## Per-car field-name divergence (BMW vs Aston vs Porsche)

Same schema shape, but field names and section placement diverge:

| Concept | BMW M4 GT3 path | Aston Vantage path | Porsche 992 GT3 R path |
|---|---|---|---|
| Aero balance section | `TiresAero.AeroBalanceCalc` | `TiresAero.AeroBalanceCalculator` | `TiresAero.AeroBalanceCalc` |
| Wing field (in aero) | `WingSetting` | `RearWingAngle` | `WingSetting` |
| Wing field (in chassis rear) | `WingAngle` | `RearWingAngle` | `WingSetting` |
| Front brakes/lights section | `Chassis.FrontBrakes` | `Chassis.FrontBrakesLights` | `Chassis.FrontBrakesLights` |
| Front ARB field | `ArbBlades` | `FarbBlades` | **`ArbSetting`** |
| Rear ARB field | `ArbBlades` (in `Chassis.Rear`) | `RarbBlades` (in `Chassis.Rear`) | **`RarbSetting`** (in `Chassis.Rear`) |
| ARB encoding | int blade index | int blade index | int integer setting (NOT blade) |
| Front toe | `TotalToeIn` (paired) | `TotalToeIn` (paired) | `TotalToeIn` (paired) |
| Rear toe | per-wheel `ToeIn` (LR/RR) | per-wheel `ToeIn` (LR/RR) | **`TotalToeIn` (paired) in `Chassis.Rear`** |
| FuelLevel location | `Chassis.Rear.FuelLevel` | `Chassis.Rear.FuelLevel` | **`Chassis.FrontBrakesLights.FuelLevel`** |
| Endurance lights | (absent) | `EnduranceLights`, `NightLedStripColor` | `NightLedStripColor` only |
| Throttle map | (absent) | `ThrottleResponse` | `ThrottleShapeSetting` |
| Power steering | (absent) | `EpasSetting` | (absent) |
| Dash display | (absent) | (absent) | `DashDisplayPage` |
| TC label format | "n (TC)" | "n (TC SLIP)" | "n (TC-LAT)" |

**Implication for the solver**: GT3 cars need per-car PARAM_IDS dicts in `setup_writer.py` (same pattern as GTP — BMW vs Ferrari vs Porsche all have car-specific paths). The Porsche 992 GT3 R has an exceptional amount of divergence vs the other GT3s — including different ARB encoding (integer setting, not blade index) and different toe/fuel placement. A naive "one GT3 base template + minor overrides" approach is not enough; per-car maps will be required for at least the Porsche outlier.

**Implication for the solver**: GT3 cars need per-car PARAM_IDS dicts in `setup_writer.py` (same pattern as GTP — BMW vs Ferrari vs Porsche all have car-specific paths). A shared "GT3 base template + per-car overrides" approach maps cleanly.

## Verified per-car constants (from IBT DriverInfo + CarSetup)

### BMW M4 GT3 EVO (`bmwm4gt3`)
- `DriverCarFuelMaxLtr`: **100.0** L
- `DriverCarRedLine`: **7250** rpm
- `DriverCarIdleRPM`: 1480
- IBT screen name: **"BMW M4 GT3 EVO"** (not "BMW M4 GT3" as in user manual V3)
- Driver-loaded setup at Spielberg:
  - FWtdist 46.4%, CrossWeight 50.0%
  - Springs: F 252 N/mm, R 179 N/mm
  - Dampers (per-axle): F LSC 7 HSC 3 LSR 5 HSR 3 / R LSC 6 HSC 4 LSR 7 HSR 5
  - ARB: front 5, rear 4
  - Wing -2°, splitter 70.0 mm
  - Camber: F -4.0°, R -2.8°
  - Total front toe -2.8 mm; rear toe-in +1.5 mm/side
  - Bump rubber gap: F 15 mm, R 52 mm
  - Static RH: F 72.6 mm, R 82.6 mm
  - Dynamic RH at speed: F 68 mm, R 70 mm; FrontDownforce 41.2%
  - Brake bias 52.0%, master cyl F+R 17.8 mm, pads Low friction
  - Diff preload 100 Nm, friction faces 8, gear stack FIA
  - TC 4, ABS 6
  - Cold tire pressure 159 kPa all four

### Porsche 911 GT3 R (992) (`porsche992rgt3`)
- `DriverCarFuelMaxLtr`: **100.0** L
- `DriverCarRedLine`: **9500** rpm (highest in GT3 class — flat-6 NA)
- `DriverCarIdleRPM`: 1750
- IBT screen name: **"Porsche 911 GT3 R (992)"**
- Driver-loaded setup at Spielberg:
  - **FWtdist 44.9% (RR-layout signature — lowest of three GT3s sampled)**, CrossWeight 50.0%
  - Springs: F **220** N/mm, R **260** N/mm (rear-stiff, opposite of FR/MR cars)
  - Dampers (per-axle): F LSC 8 HSC **12** LSR 6 HSR 9 / R LSC 8 HSC 3 LSR **12** HSR 7
    (driver values reach 12 — implies 0–12 click range, wider than BMW/Aston's 0–11)
  - ARB: ArbSetting=7, RarbSetting=7 (single integer, NOT blade-paired)
  - Wing 5.7° (Porsche uses 0.7-degree offset from integers), splitter 76.6 mm
  - Camber: F -4.0°, R -3.0°
  - Total front toe -3.9 mm, **total rear toe-in +3.0 mm (paired axle, not per-wheel)**
  - Bump rubber gap: F 30 mm, R 51 mm
  - Static RH: F 69.8 mm, R 70.5 mm (close to symmetric — RR layout)
  - **Dynamic RH at speed: F 69 mm, R 61 mm (REVERSE RAKE — front higher than rear)**
  - FrontDownforce 36.0% (lower than BMW/Aston 40-41%)
  - Brake bias 51.7%, master cyl F **20.2 mm**, R **18.8 mm** (asymmetric MC sizes)
  - Brake pads Low friction
  - Diff preload 110 Nm, friction faces 8, gear stack FIA
  - TC 3 (TC-LAT), ABS 5, ThrottleShapeSetting 3, DashDisplayPage "Race 2"
  - Cold tire pressure 159 kPa all four
  - Fuel: 99.0 L (driver had near-full)

### Aston Martin Vantage GT3 EVO (`amvantageevogt3`)
- `DriverCarFuelMaxLtr`: **106.0** L
- `DriverCarRedLine`: **7000** rpm
- `DriverCarIdleRPM`: 1977
- IBT screen name: **"Aston Martin Vantage GT3 EVO"**
- Driver-loaded setup at Spielberg:
  - FWtdist 48.0%, CrossWeight 50.0%
  - Springs: F 200 N/mm, R 180 N/mm
  - Dampers (per-axle, all corners): F LSC 9 HSC 11 LSR 9 HSR 11 / R same
  - ARB: front 5 (FarbBlades), rear 5 (RarbBlades)
  - Wing +5°, splitter 70.4 mm
  - Camber: F -4.0°, R -2.8°
  - Total front toe -3.0 mm; rear toe-in +1.5 mm/side
  - Bump rubber gap: F 17 mm, R 54 mm
  - Static RH: F 70.1 mm, R 76.0 mm
  - Dynamic RH at speed: F 70 mm, R 70 mm; FrontDownforce 40.5%
  - Brake bias 55.8%, master cyl F+R 19.1 mm, pads Medium friction
  - Diff preload 110 Nm, friction faces 10, gear stack FIA
  - TC 5 (TC SLIP), ABS 5, ThrottleResponse 4 (RED), EpasSetting 3 (PAS)
  - Cold tire pressure 159 kPa all four

## What's still unknown (PENDING more IBTs)

- **Spring rate ranges**: Both cars are within published manual ranges; need varied-spring sessions to confirm step size and absolute min/max in iRacing UI vs manual.
- **ARB blade count for Aston**: manual is image-based PDF (not text-extractable). Driver loaded "5" both axles; max blade count unknown.
- **Damper click range and polarity**: BMW values 3–7 fit a 0–11 scale (matches manual). Aston values 9–11 also fit 0–11. PENDING max/min sweep to verify.
- **Wing angle min/max**: parsed aero maps imply BMW -2..+6 (9 angles), Aston +5..+13 (9 angles). Confirmed by aero-map file naming; the in-game garage range may extend further.
- **Bump rubber gap range**: per-corner adjustment, BMW had F=15/R=52, Aston had F=17/R=54. Range PENDING multi-session sweep.
- **CenterFrontSplitterHeight range**: BMW=70.0, Aston=70.4. Adjustable splitter height is a NEW GT3 garage parameter. Range and step PENDING.
- **DriverCarFuelKgPerLtr** = 0.75 for both — slightly different from GTP's 0.742 (E10 gasoline). May reflect different real-world fuel spec (likely E20 or pure gasoline ratio). Worth verifying.
- **Aero compression**: only one IBT each so far. Need 3+ varied sessions per car/track to back-solve front/rear compression coefficients via auto_calibrate.

## Next concrete steps unblocked by this data

1. **Phase 2 setup_writer GT3 dispatch table**: now possible — per-car YAML paths are documented.
2. **BMW M4 GT3 first auto_calibrate run**: not yet — needs 5+ varied IBT sessions at the same track for spring/perch/RH regression. Currently we have 1 session.
3. **Aston onboarding**: same — single session is enough for the stub; calibration needs more.
4. **End-to-end smoke test**: with `BMW_M4_GT3.suspension_arch=GT3_COIL_4WHEEL` and `wing_angles=[-2..6]` + the parsed aero map, Step 1 (rake/RH balance-only) can be exercised against the aero surface even before Steps 2-6 calibration data exists. Demonstrates the architecture wiring works end-to-end.
