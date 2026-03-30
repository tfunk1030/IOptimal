# Per-Car Setup Quirks & Parameter Reference — V2 (With Real Data)

This file contains car-specific setup knowledge. Sections marked with **[VERIFIED]** include real parameter values extracted from actual setup files (Sebring International, 2026 Season 1).

## Table of Contents
1. [Critical Architecture Differences](#architecture)
2. [BMW M Hybrid V8](#bmw)
3. [Ferrari 499P](#ferrari)
4. [Cadillac V-Series.R](#cadillac)
5. [Porsche 963](#porsche)
6. [Acura ARX-06](#acura)
7. [Cross-Car Rankings](#rankings)
8. [Track Classification](#tracks)
9. [Wet/Rain Setup](#wet)

---

## Critical Architecture Differences {#architecture}

**This is the most important section in this file.** The five GTP cars use fundamentally different setup UI structures AND fundamentally different real-world chassis philosophies. Parameter names, value types, and even what's adjustable differs between cars. Never assume a parameter from one car maps directly to another.

### Real-World Chassis Context
- **Dallara LMDh** (BMW, Cadillac): Shared carbon monocoque spine, but firewalled OEM-specific teams with locked rooms. Led by CTO Aldo Costa (ex-Mercedes F1). 80+ engineers across shared and OEM teams. Despite shared monocoque, differentiation through bodywork, engine, electronics, cooling. Pushrod-actuated double-wishbone with Penske adjustable dampers.
- **Multimatic LMDh** (Porsche 963): True clean-sheet design, 16,705 individual parts. Critical advantage: proprietary **DSSV (Dynamic Suspensions Spool Valve)** dampers — spool valves instead of shim stacks, only 4% force degradation from 30-120°C (vs 14-16% for conventional). Being both chassis and damper supplier creates uniquely tight integration. The Porsche's 4.6L V8 Biturbo is a structural element carrying chassis loads.
- **ORECA LMDh** (Acura ARX-06): LMP2-derived chassis bringing that platform's proven dynamics to Hypercar.
- **Ferrari 499P bespoke LMH**: Complete design freedom for chassis, hybrid, and battery. Monocoque is Ferrari's proprietary design (manufactured by Dallara for quality). 3.0L twin-turbo V6 is a fully stressed member. **200 kW front-axle electric motor** with its own differential — 4× the hybrid power of LMDh's spec 50 kW Bosch system, deploying at the front rather than rear. 900V F1-derived battery.

### LMDh Cars (BMW, Cadillac, Acura) — iRacing Parameter Structure
- **Rear suspension:** Coil springs with `SpringRate` (N/mm) and `SpringPerchOffset` (mm)
- **Rear heave:** Called "Third" — `ThirdSpring` (N/mm), `ThirdPerchOffset` (mm)
- **Dampers organized under:** `CarSetup_Chassis_[Corner]_[DamperParam]`
- **Pushrod param name:** `PushrodLengthOffset`
- **ARB values:** Descriptive labels ("Soft", "Medium", "Stiff") — NOT numeric
- **Brakes/Diff/TC/Gears under:** `CarSetup_BrakesDriveUnit_`
- **Diff:** Preload (Nm) + ClutchFrictionPlates + CoastDriveRampAngles
- **All use spec Xtrac P1359 7-speed sequential gearbox** with integrated clutch-plate limited-slip differential

### Porsche 963 — Multimatic Chassis
- Different suspension geometry from the Dallara cars. Same numeric setup value may produce different response.
- Similar parameter structure to LMDh cars but with Multimatic-specific geometry.
- **Multimatic DSSV dampers respond more progressively** and maintain more consistent force across temperature ranges than conventional shim-stack dampers. This is why the Porsche feels different from Dallara cars even at identical numeric damper settings.

### Ferrari 499P — Bespoke LMH Chassis
- **Rear suspension:** Torsion bars with `TorsionBarOD` (indexed, not mm) and `TorsionBarTurns`
- **Rear heave:** Called "Heave" — `HeaveSpring` (indexed value), `HeavePerchOffset` (mm)
- **Dampers organized under:** `CarSetup_Dampers_[Corner]Damper_[Param]` — separate hierarchy
- **Pushrod param name:** `PushrodLengthDelta` (not "Offset")
- **ARB values:** Letter indices ("A", "B", "C") — NOT descriptive labels
- **Brakes/Diff/TC/Gears under:** `CarSetup_Systems_`
- **Diff:** Has BOTH `FrontDiffSpec_Preload` AND `RearDiffSpec_Preload` + CoastDriveRampOptions + ClutchFrictionPlates
- **Extra hybrid params:** `HybridRearDriveEnabled`, `HybridRearDriveCornerPct`
- **Front-axle hybrid deploys only >190 km/h** — below that speed, the 499P is pure RWD. Above, the front MGU provides up to 100 kW of torque vectoring. The front BBW system works inversely to LMDh (front BBW vs LMDh's rear BBW), meaning the brake feel characteristics are fundamentally different.

---

## BMW M Hybrid V8 {#bmw}

**[VERIFIED] Sebring Setup (Garage 61, Season 1 2026)**

### Chassis
| Parameter | Value | Notes |
|-----------|-------|-------|
| Front ride height | 30.1 mm | |
| Rear ride height | 47.8 mm | Rake: 17.7mm |
| Front pushrod offset | -22.5 mm | |
| Rear pushrod offset | -29.0 mm | |
| Front heave spring | 30 N/mm | |
| Front heave perch offset | -13 mm | |
| Rear third spring | 530 N/mm | Much stiffer than front heave |
| Rear third perch offset | 42.5 mm | |
| Front torsion bar OD | 13.9 mm | |
| Rear spring rate | 160 N/mm | Coil spring |
| Rear spring perch offset | 30 mm | |

### ARBs
| Parameter | Value |
|-----------|-------|
| Front ARB size | Soft |
| Front ARB blades | 1 |
| Rear ARB size | Medium |
| Rear ARB blades | 3 |

### Alignment
| Parameter | Value |
|-----------|-------|
| Front camber | -2.9° |
| Rear camber | -1.9° |
| Front toe | -0.4 mm (slight toe-out) |
| Rear toe | 0 mm |

### Dampers (all in clicks)
| Corner | LS Comp | HS Comp | HS Slope | LS Rbd | HS Rbd |
|--------|---------|---------|----------|--------|--------|
| Front | 7 | 5 | 11 | 6 | 8 |
| Rear | 6 | 5 | 11 | 7 | 9 |

Note: BMW has relatively low damper click values compared to Ferrari. The scales are different.

### Brakes, Diff & TC
| Parameter | Value |
|-----------|-------|
| Brake bias | 46.0% |
| Brake pads | Medium |
| Front master cyl | 19.1 mm |
| Rear master cyl | 20.6 mm |
| Diff preload | 20 Nm |
| Diff clutch plates | 4 |
| Diff coast/drive ramp | 40/... |
| TC (TCLON) | 3 |
| TC (TCLAT) | 4 |

### Aero
| Parameter | Value |
|-----------|-------|
| Rear wing angle | 17° |
| Downforce balance | 50.1% |
| L/D ratio | 3.8 |
| Front RH at speed | 15 mm |
| Rear RH at speed | 40 mm |
| Starting pressures | 152 kPa (22.0 PSI) all around |
| Fuel load | 89 L |
| Gear stack | Short |

### BMW-Specific Quirks
- **[HARD RULE] Front heave must run 30-50 N/mm on ALL tracks except Daytona and Le Mans.** Enforced as a car-specific hard clamp in the heave solver. On exempt tracks (Daytona, any Le Mans config), the physics solver runs unconstrained within the normal spring range. This rule is based on verified telemetry showing the BMW's front aero platform cannot tolerate heave below 30 N/mm (vortex burst) or above 50 N/mm (excessive platform stiffness kills mechanical grip) across the typical GTP track portfolio.
- **Cold tyre snap:** Notably worse than other GTP cars on out-laps. The Garage 61 setup runs soft front ARB and modest damping which helps but doesn't eliminate this.
- **Rear ARB sensitivity:** One step in ARB diameter can swing balance dramatically. The verified setup uses "Medium" rear ARB — this is the middle ground. Use blades (3 in this setup) for fine-tuning.
- **Low brake bias (46%):** Much lower than the Ferrari (56.5%). BMW front brakes are aggressive and the car has a tendency to lock fronts, so bias sits further rearward.
- **Rear third spring is massive (530 N/mm):** This keeps the rear aero platform extremely stiff. The front heave runs at 50 N/mm — this is the minimum safe value at Sebring. **Do not run front heave below 50 N/mm** — Garage61's 30 N/mm variant produced 22 clean-track bottoming events in 3 laps with vortex burst aero instability.
- **Pressure rise is aggressive:** Starting at 152 kPa (22.0 PSI) cold — the **minimum allowed** — hot pressures reach 181-185 kPa (26.2-26.8 PSI) by lap 4. This is 3-5 PSI over ideal hot window, but **152 kPa is the lowest cold pressure available in iRacing GTP**. Cannot be addressed through pressure alone — manage tyre performance via camber, alignment, and spring/damper tuning instead.
- **Front platform at Sebring — heave 50 is the minimum safe value:** Garage61's heave 30 N/mm produced 22 clean-track bottoming events in 3 laps (LF -4.6mm, LR -12.6mm) with a 2.45s lap-time spread — the aero platform was cutting in and out through the vortex burst threshold. Heave 50 N/mm eliminated all front clean-track bottoming. **Do not run heave below 50 N/mm on the BMW at Sebring.** The original Grid-and-Go setup (heave 50) was correct; the Garage61 variant (heave 30) sacrificed platform safety for mechanical compliance. Kerb strikes (T4 at 10-15%) remain intentional driving choices, not setup failures — always correlate bottoming with track position before recommending heave spring changes. **Front static RH sits at the 30.0 mm sim-enforced floor (all GTP cars — 29.9 mm fails setup validation).**
- **Rear back-straight bottoming — resolved via HS comp progression:** LR bottomed to -3.5mm at 44-47% (back straight bumps at 251 kph) on the original Grid-and-Go setup. Rear σ=9.9mm in this zone. Fixed across 4 sessions:
  - S1 (Garage61, heave 30): 22 clean-track bottoming events — dangerous, heave spring inadequate
  - S2 (heave 50, rear comp 6, rear slope 10): 4 clean-track events — massive improvement
  - S3 (+ front slope 10, comp 5, pushrod -27): 0 clean-track events — race-ready
  - S4 (+ rear comp 7, low fuel 12L): 0 back-straight events — back straight fully resolved
  - **Fix was primarily rear HS comp (5→6→7) and HS comp slope (11→10).** The rear third spring at 530 N/mm was already adequate — the damping was the missing piece. Do not increase third spring as a first response to rear bottoming.
- **[VERIFIED S1 2026] Fuel-load pushrod compensation:** When switching from race fuel (89L) to qualifying fuel (12L), pushrods must be adjusted more negative to maintain the same target ride height. Less fuel = less weight = springs uncompress = car sits higher. The S4 qualifying run used pushrod -29.5mm (vs -27.0mm in S3 at 89L) to maintain 49.9mm rear RH. **This is not a setup change — it's maintaining the same platform at different weight.** Never flag pushrod differences between sessions without checking fuel load first. Compare resulting garage ride height, not raw pushrod values.
- **Pit-straight bump at 99.6% (qualifying specific):** S4 showed 3 transient bottoming events at 99.6% track position (pit straight before T1) at 250 kph — LR -8.4mm, RR -9.1mm simultaneously. This bump hits both rears together (heave event, not roll). Not seen in full-fuel S1-S3 sessions — the lighter qualifying car carries more speed approaching T1 and has less inertia to damp oscillation. Accept it (0.05s transient, not affecting 1:48.80 pace) or address via rear HS rebound if the car is arriving unsettled.
- **Tyre wear pattern:** Rears wear ~2x faster than fronts (LR 7.8%, RR 6.9% vs LF 4.0%, RF 4.4% after 4 full laps). Monitor diff preload (20 Nm) and rear ARB if rear degradation compounds in long stints.
- **Surface temp asymmetry (track-dependent):** Right-side tyres show 12-13°C inner-outer spread vs 4-6°C on left side at Sebring (right-hand dominant track). Normal for track layout — only address if persistent after pressure correction.
- **[VERIFIED S1 2026] RARB as primary live balance tool, FARB kept at or near 1:** Current coaching advice is to keep front ARB blades at 1 or near 1 (maximum front mechanical grip). Front CAN be adjusted but the recommended approach is to use the rear ARB blades as the primary live balance variable. The mechanism works through LLTD (Lateral Load Transfer Distribution) on BOTH axles simultaneously:
  - **Stiffer rear ARB (blade 4-5):** Shifts more load transfer to the rear → front carries LESS load transfer → front tires stay more evenly loaded → front GAINS grip → **sharper front-end bite and turn-in.** Simultaneously, rear tires are more unevenly loaded → rear loses grip → car pivots around a strong front end. Both effects compound for aggressive rotation.
  - **Softer rear ARB (blade 1):** Load transfer splits more evenly between axles → front has less relative bite, rear has more grip → stable, planted rear → no snap oversteer. Important at slow speed where there's no aero downforce to stabilize the car.
  - Telemetry shows 6-10 RARB changes per lap: blade 1 for slow corners (avg 1.2 at <80 kph), blade 4-5 for high-speed sections (avg 4.2 at >2.5g lateral). Best lap (1:49.98) used this full range. **The Medium rear ARB diameter is correct — it provides the 1-5 blade range the driver needs.**
- **[VERIFIED S1 2026] Shock velocities at Sebring — slope 10 confirmed effective:** RF peak 991 mm/s was a T4 kerb strike on slope 11 (linear). Progression across sessions: p99 values dropped consistently with slope change (LR p99: 323→307→286 mm/s from slope 11→10). Peak values are stochastic single events, but the 99th percentile improvement confirms the digressive slope is working at the critical frequency range. **Slope 10 on all four corners is the validated Sebring setting.** Slope 11 (linear) applies excessive force at extreme velocities; slope 9 may be worth testing on bumpier tracks but is unvalidated.
- **[VERIFIED S1 2026] Vision tread tire conditioning rates at Sebring:** Fronts condition at +2.2-2.6°C/lap, rears at +2.9-3.5°C/lap. At these rates, rears reach 85°C operating window by lap 8-9, fronts by lap 13-15. A 5-lap Offline Testing stint will NOT reach operating temps — this is normal Vision tread conditioning behavior, not a setup failure. For sprint sessions, increase camber and toe-out to accelerate thermal buildup. For endurance, the conditioning model handles it over the first 8-10 laps.
- **Brake migration IS available (correction from earlier analysis).** The BMW shows `BrakeBiasMigration` in the setup — previously incorrectly documented as "no migration." All tested sessions ran migration at 0 (disabled). Migration is available as a tuning tool but has not been tested. BB at 46.0% has been stable across 7 sessions.

### BMW Front Heave/Slider/Torsion Calibration (19 sessions, March 7-12 2026)

**Derived Formulas (BMW-specific, OD=13.9mm baseline):**
```
HeaveSpringDeflMax = 103.4 - 0.262 * HeaveSpring    (depends ONLY on spring rate)
HeaveSliderDeflStatic = 46.2 + 0.012 * Heave + 0.251 * Perch   (perch dominates 21.8:1)
TorsionBarTurns = 0.0856 + 0.668 / HeaveSpring      (at OD=13.9mm)
```

**Key data points (unique setups):**
| Heave | Perch | SldrS | SprDS | SprDM | TB_OD | Turns | TBDfl |
|-------|-------|-------|-------|-------|-------|-------|-------|
| 90 | -13.0 | 43.4 | 8.8 | 80.4 | 13.9 | 0.093 | 18.3 |
| 80 | -13.0 | 44.2 | 9.6 | 82.5 | 13.9 | 0.093 | 18.6 |
| 70 | -13.0 | 44.3 | 9.7 | 84.8 | 13.9 | 0.095 | 19.8 |
| 70 | -14.5 | 42.5 | 9.4 | 84.8 | 13.9 | 0.096 | 20.1 |
| 50 | -13.0 | 44.7 | 10.1 | 90.2 | 13.9 | 0.098 | 22.2 |
| 50 | -12.5 | 44.9 | 9.8 | 90.2 | 13.9 | 0.098 | 22.3 |
| 50 | -10.0 | 43.3 | 5.7 | 90.2 | 13.9 | 0.102 | 24.4 |
| 40 | -17.5 | 41.8 | 11.7 | 93.6 | 13.9 | 0.100 | 22.5 |
| 30 | -31.5 | 40.4 | 24.3 | 97.7 | 14.3 | 0.092 | 16.6 |
| 30 | -31.5 | 36.8 | 20.7 | 97.7 | 15.1 | 0.091 | 14.6 |

**Perch offset guidelines by heave spring rate:**
- **Heave 30:** Requires very negative perch (-31.5mm) to maintain slider position; over-preloads spring (DeflStatic=24mm = 25% of travel consumed)
- **Heave 40-50:** Perch -13 to -17mm works well; DeflStatic ~10mm, SliderStatic ~42-45mm
- **Heave 70-90:** Perch -13mm is safe; SliderStatic ~43-44mm, plenty of dynamic travel
- **Perch more positive than -10mm is risky:** SliderStatic approaches 45mm limit, spring nearly unloaded (DeflStatic ~5mm)

**Load path physics:** Softening heave spring shifts static load to torsion bars. At heave 50 with OD 13.9, torsion bars carry ~0.102 turns of preload. At heave 90, only ~0.093 turns. This means **changing heave spring without checking crossweight in garage will unbalance the car.**

---

## Ferrari 499P {#ferrari}

**[VERIFIED] Sebring Setup (Season 1 2026)**

### Chassis
| Parameter | Value | Notes |
|-----------|-------|-------|
| Front ride height | 30.5 mm | |
| Rear ride height | 48.3 mm | Rake: 17.8mm |
| Front pushrod delta | -2.5 mm | Different param name from BMW |
| Rear pushrod delta | 12.5 mm | |
| Front heave spring | 1 (indexed) | Not a physical value — indexed selection |
| Front heave perch offset | -11.5 mm | |
| Rear heave spring | 2 (indexed) | |
| Rear heave perch offset | -102 mm | Very different scale from front |
| Front torsion bar OD | 3 (indexed) | |
| Front torsion bar turns | 0.103 | |
| Rear torsion bar OD | 8 (indexed) | |
| Rear torsion bar turns | 0.057 | |

### ARBs
| Parameter | Value |
|-----------|-------|
| Front ARB size | A (indexed) |
| Front ARB blades | 1 |
| Rear ARB size | B (indexed) |
| Rear ARB blades | 2 |

### Alignment
| Parameter | Value |
|-----------|-------|
| Front camber | -2.9° |
| Rear camber | -1.8° |
| Front toe | -2.0 mm (aggressive toe-out) |
| Rear toe | 0 mm |

### Dampers (all in clicks — DIFFERENT SCALE from BMW)
| Corner | LS Comp | HS Comp | HS Slope | LS Rbd | HS Rbd |
|--------|---------|---------|----------|--------|--------|
| Front | 15 | 15 | 8 | 25 | 6 |
| Rear | 18 | 40 | 11 | 10 | 40 |

**Note the massive difference from BMW:** Ferrari damper clicks are on a completely different scale. LF LS comp 15 on Ferrari ≠ 15 on BMW. Do not transfer damper values between cars.

### Brakes, Diff & TC
| Parameter | Value |
|-----------|-------|
| Brake bias | 53-54% (live adjusted) |
| Brake pads | Medium |
| Front master cyl | 17.8 mm |
| Rear master cyl | 17.8 mm |
| Brake migration | 1 (enabled, gain 0.0) |
| Front diff preload | 0 Nm |
| Rear diff preload | 15 Nm |
| Rear diff clutch plates | 6 |
| Rear diff coast/drive ramp | "Less Locking" |
| TC1 (slip) | 7 |
| TC2 (gain) | 6 |

### Aero
| Parameter | Value |
|-----------|-------|
| Rear wing angle | 17° |
| Downforce balance | 49.0% |
| L/D ratio | 3.86 |
| Front RH at speed | 15 mm |
| Rear RH at speed | 40 mm |
| Starting pressures | 152 kPa (22.0 PSI) all around |
| Fuel load | 89 L |
| Gear stack | Short |

### Ferrari-Specific Quirks
- **Indexed parameter values:** Springs and ARBs use abstract indices (1, 2, A, B) not physical units. You cannot directly compare "Heave Spring 1" on the Ferrari to "30 N/mm" on the BMW. Treat each car's parameter space independently.
- **Front diff preload exists:** Unlike the LMDh cars, the Ferrari has a front differential with adjustable preload. Front preload remains at 0 Nm. Rear preload was raised from 0 to 15 Nm after S1 telemetry showed severe off-throttle understeer from fully open diffs.
- **Brake bias 53-54% (revised from initial 55-56%):** Initial setup ran 55%+ which caused front lockups as aero diminished mid-braking zone. Reduced to 53-54% with live adjustment. The Ferrari's braking architecture distributes force differently from BMW — do not compare bias numbers between cars.
- **Brake migration enabled, gain 0.0:** Migration system is ON but gain at zero = no dynamic migration active. Room to experiment with gain 0.5-1.0 for late-braking stability as aero drops approaching apexes.
- **Aggressive front toe-out (-2.0mm):** Five times more toe-out than the BMW (-0.4mm). The Ferrari wants sharp turn-in. This also heats the front tyres faster.
- **[VERIFIED S1-S2 2026] OFF-THROTTLE UNDERSTEER — MULTIPLE CONTRIBUTING FACTORS.** Telemetry shows the car needs 2× the steering input off-throttle vs on-power for the same lateral g. Understeer ratio: 14.4 deg/g on power → 28.0 deg/g off-throttle. Worst at T8-T9 (333 events, 77° steer for 1.3g at 80 kph) and T5-T6 (263 events at 115 kph) — both below 190 kph hybrid cutoff where aero is minimal.
  - **⚠ DAMPER EFFECTS ARE SPEED-DEPENDENT.** At T8-T9 (80 kph) there is essentially no meaningful aero — the diffuser/rake argument is irrelevant. This is a pure mechanical weight transfer problem. At T15 (250 kph), aero dominates and ride height matters. **Different corners may require different reasoning for the same symptom.**
  - **Contributing factors for LOW-SPEED off-throttle understeer (T8-T9, T5-T6):**
    1. **Front LS Comp 15 is relatively stiff** — resists nose dive on throttle lift → weight transfers to front slowly → front doesn't load fast enough for turn-in. Fix: soften to 12-13 clicks to let the nose drop faster, getting weight onto the front tires sooner.
    2. **Rear LS Rbd 10 could go either direction** — softer lets rear rise/unload faster = weight goes forward = promotes rotation. But also means rear grip disappears faster. At low speed where aero is irrelevant, softer rear rebound (7-8) helps the rear get out of the way for rotation. At high speed, the opposite may be true (rear rising kills diffuser).
    3. **Front hybrid OFF below 190 kph** — worst understeer at 60-100 kph (ratio 59.8). Above 190 kph with hybrid, ratio drops to 14.5. The hybrid masks the mechanical understeer at speed.
    4. **Diff clutch plates at 6** — acts as a locking force multiplier. Reducing to 4 weakens the overall diff locking at all ramp angles, letting the rear wheels differentiate speed more freely through corners. Less coast-side locking = less off-throttle understeer.
    5. **BB at 53%** — still fairly front-biased. Dropping to 51-52% shifts braking torque rearward, helping the rear break traction under trail-braking and promoting entry rotation.
  - **The 8mm rear RH rise off-throttle (34→43mm) is real** but its impact depends on speed. At 250 kph it matters (diffuser). At 80 kph where the worst understeer occurs, it's primarily a weight distribution indicator, not an aero problem.
  - **Worst corners:** T8-T9 (333 events, 80 kph, 77° steering for 1.3g), T5-T6 (263 events, 115 kph). Both below hybrid cutoff, minimal aero, pure mechanical balance.
- **Rear HS Rbd 40 = equal to HS Comp 40 — extreme value.** The rear resists extending after bumps as aggressively as compressing — causes suspension packing through rough sections. Consider reducing to 20-25 for better bump compliance. This is separate from the LS transition understeer.
- **Front LS Rbd 25 — role is context-dependent.** At high speed, holds nose down and maintains rake (beneficial for aero). At low speed, holds inside front loaded during roll (may contribute to front sticking). The net effect depends on which corner phase dominates. Leave at 25 unless low-speed changes don't resolve the issue.
- **RARB behavior differs fundamentally from BMW:**
  - S1 (before diff preload): Constant 4-5, zero changes on best lap. The hybrid provided front grip electronically where the BMW needs RARB shifts.
  - S2 (after diff preload + damper changes): Now live 1-5, avg 2.8, 7-11 changes/lap — adapted to BMW-like corner-by-corner management. The driver compensates for off-throttle understeer by running softer RARB in slow corners.
- **Rear dampers are MUCH stiffer than front:** HS comp 40 rear vs 15 front, HS rbd 40 rear vs 6 front. This creates an extremely stiff rear platform for aero stability while letting the front move more for mechanical grip.
- **Front HS slope was raised from 5 to 8 after S1.** Original slope 5 was too digressive — kerbs passed straight through with no chassis feedback. Slope 8 provides connected kerb feel while maintaining compliance. p99 shock velocities remain lower than BMW (167-180 vs 211-223) due to the torsion-bar suspension characteristics.
- **Cornering mode (added S4 2025 Patch 3):** `HybridRearDriveCornerPct` set to 90% — adjusts front hybrid drive amount in high-speed corners. This is effectively a high-speed aero balance tuning tool **unique to the 499P** — no LMDh car has this.
- **Front-axle hybrid deploys only above 190 km/h** (corrected in S4 2025 hybrid overhaul). Below 190 km/h the car is pure RWD. Above 190 km/h, up to 100 kW from the front MGU provides partial AWD — **genuine advantage in wet conditions**.
- **Fuel consumption: 4.04 L/lap** — 22% higher than BMW's 3.32 L/lap. Range ~16 laps vs BMW ~22. This is the energy cost of the 200kW front hybrid system and is a significant race strategy factor.
- **RF bottoming at 66.2% (T12 area):** 3 clean-track events at 270 kph in S1. Different bottoming location from BMW (which bottoms at 44-47% back straight). Ferrari's higher front ride height (18-20mm mean vs BMW 16-17mm) provides more general clearance but this specific bump gets through. Monitor — may need front HS comp increase if persistent.
- **Ferrari back straight is dramatically more stable than BMW:** LR min 7.9mm vs BMW -2.7mm, rear σ 7.8 vs 10.1. The bespoke torsion-bar rear suspension handles Sebring's back straight surface transitions with significantly less oscillation.
- **Vision tread tires + 10 kg weight reduction (S1 2026 Patch 2):** The Ferrari received the most comprehensive BoP update in Jan 2026: new tire properties, brake cooling recalibration, brake pedal force demands, rear suspension geometry adjustments, and a 10 kg weight reduction.
- **Narrow optimal braking window:** The 499P is easy to lock fronts or rears. Aggressive overtaking under braking is risky. This is the car's primary weakness.

---

## Cadillac V-Series.R {#cadillac}

**Dallara LMDh chassis — same architecture as BMW but different character. Consensus best all-rounder in the GTP class.**

No verified setup file available from your data, but the Dallara architecture means parameter names and types match the BMW exactly. Key known differences from community and manufacturer data:

- **Best all-rounder and endurance weapon.** Slight understeer bias, front-biased weight distribution, excellent kerb compliance make it the most forgiving GTP car. Community and Coach Dave Academy consistently rank it as the best overall choice across iRacing's circuit variety.
- **Naturally aspirated 5.5L V8** — the only NA engine in the GTP class. Most linear power delivery, zero turbo lag. Predictable and consistent, especially valuable in endurance.
- **Has brake migration** (like Porsche and Ferrari, unlike BMW and Acura). Adds setup depth but also complexity.
- **Tyre overheating risk** when forcing rotation — if front carcass temps consistently >100°C, add rotation via setup (diff, rear ARB) rather than steering.
- **Diff preload sweet spot** is typically lower than BMW — the car's stability lets you run less preload for more rotation.
- **Recommended for GTP newcomers,** especially those with Dallara LMP2 experience.

---

## Porsche 963 {#porsche}

**Multimatic chassis — NOT Dallara. Different geometry and response curves.**

- **Aero-dominant.** Speed advantage comes from aero platform, not mechanical grip. Heave spring and HS compression settings are paramount.
- **Highest top speed in low-DF trim.** The natural choice for Le Mans and Daytona specification.
- **Best traction and rear-end stability in class.** Compensates for its slow-corner understeer.
- **Multimatic chassis responds more progressively** to spring and damper changes compared to Dallara's more direct response. Same numeric value produces different handling effect.
- **Entry understeer is inherent.** Don't just add front wing — that destabilizes the rear at high speed. Use combination of front pushrod offset adjustment to lower front ride height, softer front ARB, and reduced diff preload. Front ARB blade reduction is the first-line tool.
- **Has brake migration** (like Cadillac and Ferrari, unlike BMW and Acura).
- **Most popular GTP car.** Most community setups available.
- **Gentle on tyres.** Can run more aggressive camber/toe without excessive wear.

---

## Acura ARX-06 {#acura}

**ORECA LMDh chassis — NOT Dallara. Different suspension architecture from BMW/Cadillac.**

- **Diff preload is THE setup parameter.** 1-2 click changes create large handling shifts. More sensitive than any other GTP car. Keep preload low to preserve the car's natural rotation advantage — high preload restricts the defining characteristic.
- **Sharpest front end in class.** Most responsive of any GTP car, requiring minimal steering angle to rotate. Prone to snap oversteer. Tame it with diff preload and softer rear ARB, but preserve the rotation advantage.
- **NO brake migration** (like BMW, unlike Cadillac/Porsche/Ferrari). Requires consistent pedal modulation.
- **Lowest top speed.** On high-speed tracks, forced to run lower wing, which makes the rear setup challenge harder. Poorly suited for Le Mans and Daytona.
- **Highest ceiling at technical tracks.** Excels at high-downforce circuits (Laguna Seca, Barber, tight street circuits) in the hands of experienced drivers.
- **Power delivery** from the 2.4L twin-turbo V6 is peaky and can overwhelm rear grip in slow corners. Smooth throttle inputs are critical.

### Acura Suspension Architecture (ORECA-specific)

**Heave + Roll damper format** (NOT per-corner like BMW/Cadillac):
- **Heave dampers:** LS/HS comp + LS/HS rbd + HS slope (5 adjustments per axle)
- **Roll dampers:** LS + HS only (2 adjustments per axle, no comp/rbd split, no slope)
- Click range: 1–10 (BMW is 1–11)
- Telemetry has no per-corner shock channels — corner shocks synthesized from heave ± roll

**Rear corner springs are torsion bars** (not coil springs like BMW/Cadillac):
- Both front and rear use torsion bars with discrete OD steps
- Front OD: 13.90, 14.34, 14.76 mm (capped — higher ODs cause heave damper bottoming)
- Rear OD: 13.90–18.20 mm (14 discrete steps)
- Rear torsion bar turns can be negative (e.g., -0.097, -0.114)

**Diff ramp angles** (not separate coast/drive ramp like BMW):
- Single "DiffRampAngles" field in iRacing garage

### Calibration Status (2026-03-30)

**What's calibrated from telemetry (Hockenheim IBT):**
- Front heave effective mass: 600 kg
- Rear heave effective mass: 186 kg
- Heave spring baselines: front 180 N/mm, rear 120 N/mm
- Heave perch offsets: front 34.5 mm, rear 35.0 mm
- Roll damper baselines: front LS=2/HS=3, rear LS=9/HS=6
- Heave damper baselines from garage screenshots

**What's estimated (needs ORECA-specific data):**
- Torsion bar C constant: using BMW value (0.0008036) — ORECA likely differs
- Front torsion OD capped at 14.76 mm because higher ODs cause front heave damper deflection to go negative (bottoming). The relationship between torsion OD, heave spring rate, and heave damper travel is not yet modeled.
- Aero compression: front 15 mm, rear 8 mm (rough estimates)
- Damper force per click: LS 18 N, HS 80 N (Dallara estimate, ORECA may differ)
- ARB stiffness values are estimates

**Known issues requiring more garage data:**
1. **Front heave damper bottoming:** At OD ≥ 14.76 mm, heave damper defl goes to -1.7 mm. The OD-to-heave-travel interaction needs a multi-variable model (heave spring + OD + camber + pushrod).
2. **Rear ride height misses target:** Solver targets 48.2 mm rear RH (from aero maps), but actual rear RH is 37–44 mm. Pushrod-to-RH model is single-variable but the Acura's RH depends on multiple parameters. Aero map targets themselves may be wrong (calibrated for BMW, not Acura).
3. **Heave perch offset slider disabled:** Not calibrated for ORECA chassis — perch offset changes don't reliably predict RH changes.
4. **Roll dampers not physics-tuned:** Using baseline values only. Need lateral g spectrum data for proper roll damping calculation.
5. **Need 5+ varied garage screenshots** with different spring/pushrod/camber combos to build multi-variable RH regression models (like BMW's front/rear RideHeightModel).

---

## Cross-Car Rankings {#rankings}

### Competitive Hierarchy (Early 2026)
**No single car dominates** due to active BoP management. Any of the five can win at any track with proper setup and driving. The competitive order shifts season-to-season as BoP is adjusted.

### Natural Rotation (most → least)
1. Acura ARX-06
2. Ferrari 499P
3. BMW M Hybrid V8
4. Cadillac V-Series.R
5. Porsche 963

### Aero Platform Sensitivity (most → least dependent on stable platform)
1. Porsche 963
2. BMW M Hybrid V8
3. Cadillac V-Series.R
4. Ferrari 499P
5. Acura ARX-06

### Diff Sensitivity
1. Acura ARX-06 (diff IS the setup)
2. BMW M Hybrid V8
3. Ferrari 499P
4. Porsche 963
5. Cadillac V-Series.R

### Top Speed (low-DF trim, highest → lowest)
1. Porsche 963
2. Cadillac V-Series.R
3. BMW M Hybrid V8
4. Ferrari 499P
5. Acura ARX-06

### Best Use Case by Circuit Type
- **Low DF / Endurance:** Cadillac V-Series.R (consistency, tyre life, forgiveness)
- **Low DF / Sprint:** Porsche 963 (top speed, traction)
- **High DF / Technical:** Acura ARX-06 (rotation, front-end response)
- **Medium/High Speed Sweepers:** Ferrari 499P (mid/high-speed stability, cornering mode)
- **All-Round / Newcomers:** Cadillac V-Series.R (most forgiving, best all-rounder consensus)
- **Bumpy / Mechanical Grip:** BMW M Hybrid V8 (neutral baseline, rewards setup work)

### Setup Effort Required (most → least iteration needed per track)
1. BMW M Hybrid V8 (neutral but demands setup work to be competitive)
2. Ferrari 499P (bespoke chassis, unique parameter space)
3. Acura ARX-06 (diff sensitivity requires careful tuning)
4. Porsche 963 (progressive, predictable response)
5. Cadillac V-Series.R (most forgiving, closest to "drive off baseline")

---

## Track Classification for GTP {#tracks}

| Track | Type | Key Setup Focus |
|-------|------|----------------|
| Daytona | Low DF, smooth, banking, long straights | Minimum wing, stiff heave/third springs for 31° banking compression loads (will bottom car if too soft), raise ride heights for banking, higher tyre pressures for sidewall loading, bus stop chicane is the key compromise — trail-brake for front grip the aero setup can't provide. Porsche 963 highest top speed here. |
| Sebring | Bumpy, mixed speed, concrete/asphalt transitions | Compliant suspension (softer HS comp slope for digressive damping). Front heave 50 N/mm is adequate on clean track for BMW — the bumps that cause bottoming are mostly kerb strikes (T4, T11, T15) which are driving choices, not setup failures. **The real platform issue is the back straight (44-47% of lap): rear bottoming at 250 kph with σ=9.9mm. Fix via rear HS comp damping/slope, not heave spring.** Front RH at 30.0mm floor (all GTP cars). Expect elevated hot pressures (152 kPa min cold → 25-27 PSI hot). BMW excels here on mechanical grip. |
| COTA | High-speed + technical, heavy kerbs | Medium-high wing, S1 esses need firm heave springs for rapid direction changes under aero load, but elevation changes elsewhere create dynamic DF variations at crests that punish over-stiff setups. Moderate heave + firm rear third spring. Stiffen HS comp for kerbs. |
| Watkins Glen | Medium-high DF, elevation, bumpy | High wing, **softer front heave springs** (officially documented as requiring softer settings like Sebring), lower HS comp slope for bump absorption, Boot section elevation changes reward proper heave/third spring tuning, focus on mid-speed balance, camber important |
| Road Atlanta | Fast sweepers + hard braking | Aero platform critical through esses, strong braking setup |
| Road America | Long straights + fast corners, smooth | Lower wing than you'd think, can run stiffer heave springs (smooth surface), heave springs important for aero platform through fast kink |
| Spa | High speed, elevation, mixed | Medium-high wing, very stiff rear heave for Eau Rouge/Blanchimont platform. HS rebound must allow suspension to extend through Raidillon crest without unloading wheels. Ferrari 499P excels through Pouhon/Blanchimont. Bus Stop chicane needs compliance. |
| Le Mans | Ultra-low drag, long straights + chicanes | Minimum wing, **Long gear stack MANDATORY since S2 2025**. Front RH at 30mm = minimum drag configuration by default. Stiff rear third spring for Porsche Curves platform consistency. Chicane compliance vs straight speed is the key trade-off. Porsche 963 reaches highest top speed. Ferrari 499P cornering mode valuable through Porsche Curves. |
| Indianapolis GP | Medium speed, mostly flat, some bumps | Mid-level everything, good baseline track |
| Laguna Seca | Technical, elevation change (Corkscrew) | High wing, mechanical grip focus, heavy braking setup. Acura ARX-06 excels here. |
| Monza | Low DF, long straights, few fast corners | Low wing, low drag, stiffer HS comp slope (smooth, linear damping suits this surface), minimize tyre heat in chicanes |
| Nürburgring GP | Technical, mixed speed | Medium-high wing, good all-around setup |
| Imola | Technical, kerb-heavy, elevation | Medium wing, compliant over kerbs, strong braking |
| Suzuka | Fast flowing, high commitment | Aero platform critical, high wing, confidence setup |
| Bathurst | Extreme elevation, bumpy, tight sections | Very compliant for mountain section, strong heave for straight, dual-personality setup |
| Hockenheim GP | Smooth, mixed speed, tight stadium section | Smooth modern tarmac allows softer heave/third springs for mechanical grip. Parabolika + back straight reward lower wing angles for top speed. Stadium section (Motodrom) is tight and technical — diff preload critical for traction out of slow corners (especially important for Acura ARX-06). Stadium kerbs can be aggressive — tune HS comp/slope for compliance. Balance aero platform stability through fast sections vs mechanical grip in stadium. |

---

## Wet/Rain Setup Adjustments {#wet}

When rain is declared or `TrackWetness` indicates wet conditions:

### Mandatory Changes
1. **Fit wet tyres immediately.** Wet tyres have ~2x tread depth and larger diameter than slicks. This raises ride heights — re-check aero balance after tyre swap.
2. **Shift brake bias rearward** by 2-4%. Fronts lock much more easily on a wet surface. Cars without ABS need even more rearward bias.
3. **Switch to wet ABS and TC maps** if available. Increase TC intervention.

### Recommended Suspension Changes
- **Soften ARBs** — less roll stiffness helps tyres find grip on low-traction surfaces.
- **Soften heave/third springs** — in the dry, stiff heaves maintain aero platform. In the wet, aero loads are lower (slower speeds), so the platform benefit is reduced and compliance matters more.
- **Increase ride heights** slightly — adjust via pushrod length offsets and heave/spring perch offsets for standing water clearance, reduces aquaplaning risk. Verify resulting ride height in garage.
- **Soften HS compression damping** — helps tyres maintain contact over water patches.

### Tyre Considerations
- Wet tyres overheat rapidly on a drying track. Monitor carcass temps closely — if above 100°C on wets, the track may be dry enough for slicks.
- Wet compound has a lower optimal temperature window than dry compound.
- Wet tyres wear fastest on dry surfaces — the compound is too soft for high-temperature operation.

### TC Map Management
- TC channels: `dcTractionControl` (TC1/TCLON) and `dcTractionControl2` (TC2/TCLAT)
- In the wet, run higher TC values to prevent wheelspin. This is a setup-level decision (garage TC maps) AND a live in-car adjustment.
- **Ferrari at Sebring runs TC1=7, TC2=6 in the dry.** In the wet, increase both by 2-3 steps as a starting point.
- **BMW at Sebring runs TCLON=3, TCLAT=4 in the dry.** Scale up similarly.
- Watch `dcTractionControl` in telemetry — if the driver is constantly increasing TC during the stint, the base setup TC is too low.

### Wet Racing Line
- **Avoid the rubbered dry racing line in wet conditions** — it becomes extremely slippery. Explore alternative lines on cleaner tarmac. This is a driving consideration but directly impacts how the setup's balance manifests — a car that understeers on the dry line may oversteer on a wetter off-line section.

### Ferrari 499P Wet Advantage
- The Ferrari's front-axle hybrid deployment above 190 km/h provides **partial AWD in wet conditions** — a genuine competitive advantage in rain. The cornering mode (`HybridRearDriveCornerPct`) becomes especially valuable for high-speed wet stability. No LMDh car has this capability.
