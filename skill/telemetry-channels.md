# iRacing GTP Telemetry Channel Reference — V2 (Verified from IBT)

All channel names are **verified from a parsed Ferrari 499P IBT file** (302 channels, 60 Hz). These are the exact SDK variable names as stored in the IBT binary.

## IBT Basics
- **Location:** `Documents/iRacing/Telemetry/`
- **Format:** `.ibt` — binary, 60 samples/sec, ~299-302 channels for GTP
- **Toggle:** `Alt+L` in-sim, or auto-capture via Mu
- **Pipeline:** Mu converts `.ibt` → MoTeC `.ld` + `.ldx` (metadata with full setup snapshot)
- **Direct parsing:** See `references/ibt-parsing-guide.md` for Python IBT parser — extracts both telemetry data AND full garage setup from session info YAML
- **Tools:** MoTeC i2 (free), Track Attack, Race Engineering Center, pyirsdk, custom CSV

## Unit Conversion Quick Reference

All telemetry channels use SI units. Convert for display:

| Raw Unit | Display | Formula | Example |
|----------|---------|---------|---------|
| m/s (Speed) | km/h | × 3.6 | 81.3 m/s = 292.7 km/h |
| m/s² (Accel) | g | ÷ 9.81 | 40.7 m/s² = 4.15g |
| kPa (Pressure) | PSI | ÷ 6.895 | 152 kPa = 22.0 PSI |
| m (Heights) | mm | × 1000 | 0.030 m = 30.0 mm |
| rad (Angles) | deg | × 57.296 | 3.35 rad = 191.9° |
| rad/s (Rates) | deg/s | × 57.296 | — |
| L (Fuel) | kg | × 0.75 (approx) | 89 L ≈ 66.8 kg |

## Corner Shocks (Per-Wheel)

| Channel | Unit | Use |
|---------|------|-----|
| `LFshockDefl` / `RFshockDefl` / `LRshockDefl` / `RRshockDefl` | m | Shock deflection from static. Near max travel (check `ShockDeflMax` in LDX) = bottoming. |
| `LFshockVel` / `RFshockVel` / `LRshockVel` / `RRshockVel` | m/s | Shock velocity. Histogram: <25mm/s = LS damper regime, >100mm/s = HS regime. |

## Heave/Third Element Shocks

| Channel | Unit | Use |
|---------|------|-----|
| `HFshockDefl` | m | Front heave element deflection — **primary aero platform diagnostic** |
| `HFshockVel` | m/s | Front heave velocity |
| `HRshockDefl` | m | Rear heave (third) element deflection |
| `HRshockVel` | m/s | Rear heave velocity |

If `HFshockDefl` variance is large at high speed, front aero platform is unstable → stiffen front heave spring.

## Ride Heights

| Channel | Unit | Use |
|---------|------|-----|
| `LFrideHeight` / `RFrideHeight` / `LRrideHeight` / `RRrideHeight` | m | Per-corner ride height |
| `CFSRrideHeight` | m | **Center front splitter ride height** — when near zero, splitter is scraping. Most important single aero channel. |

All in **meters** (0.030 = 30mm). Front-rear delta should be constant at speed. If delta changes, aero balance is speed-dependent → fix with heave spring ratio.

## Tyre Temperatures (24 channels)

⚠️ **CRITICAL: Carcass temps may be unreliable in short stints.** Verified from BMW M Hybrid V8 IBT at Sebring: carcass temps (`tempCL/CM/CR`) remained flat at ambient (34.8°C) across 4 full laps while surface temps (`tempL/M/R`) showed 50-75°C variation. **Always use surface temps as primary diagnostic. Only use carcass temps if they show meaningful deviation from ambient.**

| Pattern | Unit | Description |
|---------|------|-------------|
| `LFtempL` / `LFtempM` / `LFtempR` | C | Left-front **surface** temps: Left/Middle/Right of tyre face. **PRIMARY setup diagnostic — use these first.** |
| `LFtempCL` / `LFtempCM` / `LFtempCR` | C | Left-front **carcass** temps. Slower-responding, may stay ambient in short stints. **Verify before trusting.** |

Same pattern for RF, LR, RR (24 total).

**Naming gotcha:** L/M/R refers to the tyre face viewed from behind. For left-side tyres: `tempL`=outer, `tempR`=inner. For right-side tyres: `tempR`=outer, `tempL`=inner. The setup's `LastTempsOMI` (left tyres) vs `LastTempsIMO` (right tyres) confirms this mapping.

**Targets:** Working window 85-105°C surface. Inner hottest with 5-8°C gradient to outer = good camber/pressure. If all temps are <70°C after 3+ laps, check pressures first — overinflated tyres have reduced contact patch.

## Tyre Pressures

| Channel | Unit | Use |
|---------|------|-----|
| `LFpressure` / `RFpressure` / `LRpressure` / `RRpressure` | kPa | **Hot** (live on-track) pressure |
| `LFcoldPressure` / `RFcoldPressure` / `LRcoldPressure` / `RRcoldPressure` | kPa | **Cold** pressure as set in garage |

**Conversion:** kPa ÷ 6.895 = PSI. Example from your data: 152 kPa = 22.0 PSI cold.

**GTP minimum cold pressure: 152 kPa (22.0 PSI).** Cannot go lower. Hot pressures will always exceed the ideal 20-24 PSI window (~25-27 PSI hot at minimum cold). This is a known sim constraint.

## Tyre Wear & Odometer

| Channel | Unit | Use |
|---------|------|-----|
| `LFwearL` / `LFwearM` / `LFwearR` (per corner) | % | Tread remaining. 100%=new. |
| `LFodometer` (per corner) | m | Total distance on this tyre set — useful in endurance. |

## Wheel Speeds

| Channel | Unit | Use |
|---------|------|-----|
| `LFspeed` / `RFspeed` / `LRspeed` / `RRspeed` | m/s | Per-wheel speed. Compare to `Speed` to detect lock-ups (wheel < car) or wheelspin (wheel > car). L-R rear delta reveals diff behavior. |

## Brakes

| Channel | Unit | Use |
|---------|------|-----|
| `Brake` / `BrakeRaw` | % | Pedal position (0-1). |
| `BrakeABSactive` | bool | True when ABS is cutting pressure. |
| `BrakeABScutPct` | % | How much ABS is reducing brake force. |
| `LFbrakeLinePress` / `RFbrakeLinePress` / `LRbrakeLinePress` / `RRbrakeLinePress` | bar | **Per-corner brake line pressure.** Shows actual force distribution including bias and ABS. |

## Vehicle Dynamics

| Channel | Unit | Use |
|---------|------|-----|
| `Speed` | m/s | GPS vehicle speed |
| `LatAccel` | m/s² | Lateral G (including gravity) |
| `LongAccel` | m/s² | Longitudinal G |
| `VertAccel` | m/s² | Vertical G |
| `Yaw` / `YawRate` | rad / rad/s | Heading and rotation rate. YawRate spikes = oversteer events. |
| `Roll` / `RollRate` | rad / rad/s | Roll angle and rate. Asymmetric L/R = check springs/preloads. |
| `Pitch` / `PitchRate` | rad / rad/s | Nose pitch. |
| `VelocityX` / `VelocityY` / `VelocityZ` | m/s | Body-frame velocities. |
| `SteeringWheelAngle` | rad | Steering input. Increasing through mid-corner at constant speed = understeer. |
| `SteeringWheelTorque` | N·m | FFB torque — spikes indicate tyre saturation or kerb strikes. |

## Driver Inputs & In-Car Adjustments

| Channel | Unit | Use |
|---------|------|-----|
| `Throttle` / `ThrottleRaw` | % | Throttle position. |
| `Gear` | int | -1=R, 0=N, 1-7 |
| `RPM` | revs/min | Engine RPM. Check for limiter hits before braking zones. |
| `dcBrakeBias` | | In-car brake bias adjustment — if moving a lot, base setup bias is wrong. |
| `dcTractionControl` | | TC1 / TCLON adjustment — if increasing during stint, rears overheating. |
| `dcTractionControl2` | | TC2 / TCLAT adjustment. |
| `dcABS` | | ABS level adjustment. |
| `dcAntiRollFront` / `dcAntiRollRear` | | In-car ARB adjustments (if available). |
| `dcMGUKDeployMode` | | Hybrid deploy mode. |

## Hybrid / ERS

| Channel | Unit | Use |
|---------|------|-----|
| `EnergyERSBattery` / `EnergyERSBatteryPct` | J / % | Battery state of charge. |
| `PowerMGU_H` / `PowerMGU_K` | W | MGU mechanical power output. |
| `TorqueMGU_K` | Nm | MGU-K torque — contributes to rear axle torque. |
| `EnergyMGU_KLapDeployPct` | % | Energy available for deploy this lap. |

## Weather

| Channel | Unit | Use |
|---------|------|-----|
| `AirTemp` / `TrackTempCrew` | C | Air and track temperature. |
| `AirDensity` | kg/m³ | Affects aero downforce and engine power. |
| `Precipitation` | % | Current precipitation level. |
| `TrackWetness` | int | Track wetness enum. |
| `WeatherDeclaredWet` | bool | Steward allows rain tyres. |
| `WindVel` / `WindDir` | m/s / rad | Wind speed and direction. |

## Fuel

| Channel | Unit | Use |
|---------|------|-----|
| `FuelLevel` | L | Fuel remaining. GTP tanks ~89L. Full-to-empty = ~65kg balance shift. |
| `FuelUsePerHour` | kg/h | Instantaneous consumption. |

## Setup Extraction — Two Methods

### Method 1: IBT Session Info (Preferred for direct analysis)
The `.ibt` file itself contains a YAML session info block with the **complete garage setup** under the `CarSetup` key. This is parsed directly from the binary header — see `references/ibt-parsing-guide.md`. The YAML also contains `WeekendInfo` (track, weather, session type), `DriverInfo` (all drivers, cars — filter `CarIsPaceCar != 1`), and `SessionInfo` (session results).

**Key setup paths in the YAML:**
- `CarSetup.TiresAero` — Pressures, wing angle, aero calculator (RH at speed, DF balance, L/D)
- `CarSetup.Chassis.Front` — Heave spring, heave perch, ARB, toe, pushrod offset
- `CarSetup.Chassis.LeftFront` / `RightFront` — Corner weights, ride height, shock defl, torsion bar, dampers, camber
- `CarSetup.Chassis.LeftRear` / `RightRear` — Spring rate, spring perch, dampers, camber, toe
- `CarSetup.Chassis.Rear` — Third spring, third perch, ARB, pushrod offset, cross weight
- `CarSetup.BrakesDriveUnit` (BMW/LMDh) or `CarSetup.Systems` (Ferrari) — Brakes, diff, TC, gearing, fuel

### Method 2: LDX File (MoTeC workflow)
The `.ldx` file (MoTeC XML, created by Mu alongside `.ld`) contains the same setup data in XML format. Key parameters are prefixed `CarSetup_Chassis_`, `CarSetup_TiresAero_`, `CarSetup_Dampers_` (Ferrari) or `CarSetup_Chassis_` (BMW dampers), `CarSetup_Systems_` (Ferrari) or `CarSetup_BrakesDriveUnit_` (BMW).
