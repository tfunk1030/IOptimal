# Deep Audit: `analyzer/` and `pipeline/` Directories

**Date:** 2026-03-31
**Scope:** All files in `analyzer/` (17 files) and `pipeline/` (6 files)

---

## Table of Contents

1. [analyzer/extract.py — Telemetry Extraction](#analyzerextractpy)
2. [analyzer/diagnose.py — Handling Diagnosis](#analyzerdiagnosepy)
3. [analyzer/segment.py — Corner Segmentation](#analyzersegmentpy)
4. [analyzer/driver_style.py — Driver Profiling](#analyzerdriver_stylepy)
5. [analyzer/recommend.py — Recommendation Engine](#analyzerrecommendpy)
6. [analyzer/setup_reader.py — Setup Parsing from IBT/STO](#analyzersetup_readerpy)
7. [analyzer/sto_reader.py — STO Inspection CLI](#analyzersto_readerpy)
8. [analyzer/sto_binary.py — Binary STO Decoder](#analyzersto_binarypy)
9. [analyzer/sto_adapters.py — Car-Specific STO Mapping](#analyzersto_adapterspy)
10. [analyzer/setup_schema.py — Canonical Schema](#analyzersetup_schemapy)
11. [analyzer/state_inference.py — High-Level State Diagnosis](#analyzerstate_inferencepy)
12. [analyzer/telemetry_truth.py — Signal Quality Assessment](#analyzertelemetry_truthpy)
13. [analyzer/context.py — Session Context](#analyzercontextpy)
14. [analyzer/conflict_resolver.py — Conflict Resolution](#analyzerconflict_resolverpy)
15. [analyzer/causal_graph.py — Causal Root Cause Analysis](#analyzercausal_graphpy)
16. [analyzer/adaptive_thresholds.py — Adaptive Thresholds](#analyzeradaptive_thresholdspy)
17. [analyzer/stint_analysis.py — Stint Analysis](#analyzerstint_analysispy)
18. [analyzer/overhaul.py — Overhaul Assessment](#analyzeroverhaulpy)
19. [analyzer/report.py — Report Formatting](#analyzerreportpy)
20. [analyzer/__init__.py / __main__.py — Entry Points](#analyzerinit)
21. [pipeline/produce.py — End-to-End Orchestrator](#pipelineproducepy)
22. [pipeline/reason.py — Multi-Session Reasoning Engine](#pipelinereasonpy)
23. [pipeline/report.py — Pipeline Report Generator](#pipelinereportpy)
24. [pipeline/preset_compare.py — Preset Comparison Tool](#pipelinepreset_comparepy)
25. [pipeline/__main__.py — Entry Point](#pipelinemainpy)
26. [Telemetry Channel Flow: extract → diagnose → modifiers → solver](#telemetry-channel-flow)
27. [Hardcoded Constants & Thresholds Summary](#hardcoded-constants)
28. [BMW-Specific vs Generic Logic](#bmw-specific-logic)
29. [Production Path Summary](#production-path-summary)

---

## analyzer/extract.py

**Purpose:** Extract 100+ measured telemetry quantities from an IBT file into a single `MeasuredState` dataclass.

**Production path:** YES — called by `pipeline/produce.py::produce()` and `pipeline/reason.py::_analyze_session()`.

### IBT Channels Read (Complete List)

| Channel Name | Usage | Domain |
|---|---|---|
| `Speed` | Speed masks, regime classification | Core |
| `LatAccel` | Lateral g for cornering masks | Core |
| `LapDist` | Kerb spatial masking, corner detection | Core |
| `LapCurrentLapTime` | Lap time extraction | Core |
| `Lap` | Lap number | Core |
| `Brake` | At-speed masks, braking detection | Core |
| `Throttle` / `ThrottleRaw` | Throttle signal, TC intervention | Core |
| `LFrideHeight`, `RFrideHeight`, `LRrideHeight`, `RRrideHeight` | Ride heights → aero compression, bottoming, LLTD, roll | Aero/Platform |
| `LFshockVel`, `RFshockVel`, `LRshockVel`, `RRshockVel` | Corner shock velocities (per-corner p95) | Damper |
| `HFshockVel`, `HRshockVel` | Heave shock velocities; fallback for corner shocks if missing | Heave damper |
| `TRshockVel` | Third/rear heave shock velocity | Heave damper |
| `FROLLshockVel`, `RROLLshockVel` | Roll damper velocities (Acura: synthesize corner from heave±roll) | Roll damper |
| `HFshockDefl`, `HRshockDefl` | Heave spring deflection → travel usage, bottoming | Spring travel |
| `LFshockDefl`, `RFshockDefl`, `LRshockDefl`, `RRshockDefl` | Corner shock deflection → travel proximity | Spring travel |
| `CFSRrideHeight` | Center front splitter ride height → scrape detection | Aero safety |
| `Roll` | Body roll angle → roll gradient, p95 roll | Balance |
| `RollRate` | Roll rate p95 → damper diagnosis | Damper |
| `PitchRate` | Pitch rate p95 | Damper |
| `Pitch` | Pitch angle → rake, braking pitch range | Platform |
| `SteeringWheelAngle` | Understeer calculation, corner direction | Handling |
| `YawRate` | Yaw rate correlation with steering | Handling |
| `VelocityX`, `VelocityY` | Body slip angle | Handling |
| `LFspeed`, `RFspeed`, `LRspeed`, `RRspeed` | Wheel speeds → slip ratios (lock/power) | Grip |
| `LFtempL`, `LFtempR`, `RFtempL`, `RFtempR`, etc. | Surface temps → temp spread (inner-outer) | Thermal |
| `LFtempM`, `RFtempM`, `LRtempM`, `RRtempM` | Middle surface temps | Thermal |
| `LFtempCM`, `RFtempCM`, `LRtempCM`, `RRtempCM` | Carcass temps → thermal window | Thermal |
| `LFtempCL`, `LFtempCR`, etc. | Carcass gradient (inner-outer) → camber validation | Thermal |
| `LFpressure`, `RFpressure`, `LRpressure`, `RRpressure` | Hot tyre pressures | Thermal |
| `LFcoldPressure`, `RFcoldPressure`, etc. | Cold tyre pressures | Thermal |
| `LFwearL`, `LFwearM`, `LFwearR`, etc. | Tyre wear per corner (3-point average) | Thermal |
| `LFbrakeLinePress`, `RFbrakeLinePress`, `LRbrakeLinePress`, `RRbrakeLinePress` | Hydraulic brake split | Braking |
| `LongAccel` | Braking deceleration (fallback from Speed gradient) | Braking |
| `BrakeABSactive` | ABS activity percentage | Braking |
| `BrakeABScutPct` | ABS force cut percentage | Braking |
| `RPM` | Rev limiter detection | RPM |
| `Gear` | Gear at apex, max gear | Gear |
| `FuelLevel` | Fuel level and consumption | Fuel |
| `EnergyERSBatteryPct` / `EnergyERSBattery` | Hybrid battery state | Hybrid |
| `TorqueMGU_K` | MGU-K peak torque | Hybrid |
| `AirTemp`, `TrackTempCrew`, `AirDensity` | Environmental conditions | Environment |
| `WindVel`, `WindDir` | Wind conditions | Environment |
| `dcBrakeBias` | Brake bias adjustments tracking | In-car |
| `dcTractionControl`, `dcTractionControl2` | TC adjustments tracking, live TC values | In-car |
| `dcAntiRollFront`, `dcAntiRollRear` | ARB adjustments tracking, live ARB values | In-car |
| `dcABS` | ABS adjustments tracking | In-car |
| `dcMGUKDeployMode` | Deploy mode adjustments tracking | In-car |
| `VertAccel` | Kerb event detection (via `_find_kerb_events`) | Kerb |
| `TireLF_RumblePitch`, `TireRF_RumblePitch` | Rumble strip detection → kerb mask | Kerb |

**Total: ~70 unique IBT channel names read.**

### Derived Metrics Computed (Key Ones)

| Metric | Derivation | Used Downstream By |
|---|---|---|
| `aero_compression_front/rear_mm` | static_RH - dynamic_RH | diagnose, modifiers |
| `bottoming_event_count_*` | RH < mean - 3σ, split clean/kerb | diagnose (safety), modifiers |
| `vortex_burst_event_count` | front RH < mean - 3.5σ at speed | diagnose (safety) |
| `front/rear_rh_excursion_measured_mm` | p99 |RH - mean| at speed | diagnose (platform) |
| `roll_distribution_proxy` (aliased `lltd_measured`) | front_moment / total_moment from RH differential × track_width² | diagnose (balance), modifiers |
| `understeer_mean/low_speed/high_speed_deg` | road_wheel_angle - wb × yaw_rate / speed | diagnose (balance), modifiers |
| `body_slip_p95_deg` | arctan2(Vy, |Vx|) p95 | diagnose (balance) |
| `front/rear_rh_settle_time_ms` | Event-based clean disturbance response (median of ≥3 clean events) | diagnose (damper), modifiers |
| `front/rear_dominant_freq_hz` | FFT of RH at >200 kph on clean straights | diagnose (spring) |
| `roll_gradient_measured_deg_per_g` | Linear regression |Roll| vs |LatAccel| in 1-2g range | learner, validation |
| `front_heave_travel_used_pct` | p99 defl / DeflMax × 100 | diagnose (safety), modifiers |
| `rear/front_shock_oscillation_hz` | Zero-crossing frequency of shock velocity | learner (damper validation) |
| `hydraulic_brake_split_pct` | front_brake_pressure / total during braking | diagnose (grip) |
| `tc_intervention_pct` | % time ThrottleRaw > Throttle + 0.02 | context, report |

### BMW-Specific Logic

- `extract.py` itself is **generic** — no car-specific branching.
- It uses `car.heave_spring` parameters for DeflMax calculation and `car.steering_ratio`, `car.wheelbase_m`, `car.arb.track_width_*` from the `CarModel`, which are car-specific but accessed generically.
- Heave/roll damper synthesis (lines 391-398) handles Acura's `HFshockVel + FROLLshockVel` architecture transparently.

---

## analyzer/diagnose.py

**Purpose:** Evaluate `MeasuredState` against physics-derived thresholds to identify handling problems.

**Production path:** YES — called by `pipeline/produce.py` and `pipeline/reason.py`.

### Diagnostic Categories (6 Priority Levels)

| Priority | Category | Checks | Key Thresholds (Baseline) |
|---|---|---|---|
| 0 | **safety** | Vortex burst, front/rear bottoming (clean vs kerb), heave travel exhaustion (>85%), splitter scrape, direct heave bottoming | vortex: >0 events; bottoming: >5 events; travel: >85%; splitter: <2mm |
| 1 | **platform** | Front/rear RH variance, excursion near bottoming, braking pitch range | variance: F>8mm R>10mm; excursion: >80% of dynamic RH; pitch range: >0.9° |
| 2 | **balance** | Understeer (mean, LS, HS), oversteer, speed gradient, directional asymmetry, LLTD proxy, body slip, speed-dependent roll proxy shift | US: >2.5°; OS: <-0.5°; gradient: >1.5°; LLTD delta: >8%/-2%; body slip: >4°; dir asym: >0.3° |
| 3 | **damper** | Settle time (too long/short), yaw rate correlation, roll rate | settle: >200ms or <50ms; yaw R²: <0.65; roll rate: >25°/s |
| 4 | **thermal** | Temp spread vs target (F: 10°C, R: 8°C inner-hot), carcass temp window (80-105°C), hot pressure window (155-175 kPa) | spread delta: >4°C; carcass: <80 or >105°C; pressure: <155 or >175 kPa |
| 5 | **grip** | Rear traction slip p95, front braking lock p95, hydraulic brake split, ABS activity (>30%), ABS cut (>15%) | rear slip: >0.08; front slip: >0.06; ABS: >30% |
| 5+ | **brake/hybrid** | Hydraulic split vs setup delta, ABS activity/cut, in-car adjustments (>5 changes) | split delta: >4%; bias adj: >5; TC adj: >5 |

### Car-Specific vs Universal Thresholds

- **Baseline thresholds** are universal (defined in `adaptive_thresholds.py::BASELINE_THRESHOLDS`).
- **Car-specific baselines** in `CAR_BASELINES` dict: BMW, Ferrari, Porsche, Cadillac, Acura each have `understeer_nominal_deg` and `body_slip_nominal_deg` — these shift the understeer threshold (nominal + 1.5° allowable deviation).
- **Track scaling** via `TRACK_SENSITIVITY` adjusts platform/bottoming/settle thresholds based on surface severity (shock velocity p99 vs 200 mm/s baseline).
- **Driver scaling** adjusts handling thresholds: smooth-consistent × 0.85, aggressive-erratic × 1.20.

### Post-Diagnosis Steps

1. `causal_graph.analyze_causes(problems)` — maps symptoms to root causes.
2. `infer_car_states(measured, setup, problems, driver, corners)` — identifies high-level states.
3. `assess_overhaul(state_issues)` — classifies overhaul level.

---

## analyzer/segment.py

**Purpose:** Corner-by-corner lap segmentation with per-corner suspension/handling metrics.

**Production path:** YES — called by `pipeline/produce.py`.

### Corner Detection

- `_detect_corners()`: Smooths |lat_g| with 15-sample kernel, thresholds at 0.5g, min 10 samples.
- `_detect_braking_zones()`: brake > 0.10 edges.
- Speed classification: low (<120 kph), mid (120-180), high (>180).

### Per-Corner Metrics (`CornerAnalysis` dataclass)

- **Suspension:** front/rear shock vel p95/p99, front/rear RH mean/min.
- **Handling:** understeer_mean_deg, body_slip_peak_deg, trail_brake_pct, throttle_onset_dist_m.
- **Kerb:** overlap flag, severity max.
- **Phase timing:** braking, release, turn-in, entry, apex, throttle pickup, exit phases (seconds).
- **Time-loss proxies:** entry_loss_s, apex_loss_s, exit_loss_s (bounded opportunity analysis).
- **Risk flags:** platform_risk_flags, traction_risk_flags.

---

## analyzer/driver_style.py

**Purpose:** Quantitative driver behavior profiling from telemetry for setup tuning.

**Production path:** YES — called by `pipeline/produce.py`.

### Metrics and Classification

| Metric | Source | Classification | Thresholds |
|---|---|---|---|
| trail_brake_depth_mean/p95 | CornerAnalysis.trail_brake_pct | "light" / "moderate" / "deep" | <0.15 / 0.15-0.40 / >0.40 |
| throttle_progressiveness | R² of linear fit during exit phase | "progressive" / "moderate" / "binary" | >0.75 / 0.50-0.75 / <0.50 |
| steering_jerk_p95 | d²(steering)/dt² percentile | "smooth" / "moderate" / "aggressive" | <50 / 50-100 / >100 rad/s² |
| apex_speed_cv | CV across laps | "consistent" / "variable" / "erratic" | <0.03 / 0.03-0.08 / >0.08 |
| avg_peak_lat_g_utilization | actual / theoretical limit | "conservative" / "moderate" / "limit" | <0.75 / 0.75-0.90 / >0.90 |

**Overall style:** `{prefix}-{consistency}` (e.g., "smooth-consistent", "aggressive-erratic").

### Downstream Impact

- `solver/modifiers.py`: smooth → ζ × 0.92; aggressive → F HS comp +1; limit cornering → HS comp +1 F+R; aggressive-erratic → ζ × 1.05.
- `adaptive_thresholds.py`: smooth-consistent → thresholds × 0.85; aggressive-erratic → × 1.20.

### Additional Functions

- `refine_driver_with_measured(profile, measured)` — adjusts profile with measured telemetry.
- `separate_driver_noise(profile, measured)` — separates driver noise from setup noise.

---

## analyzer/recommend.py

**Purpose:** Compute specific garage parameter changes for each diagnosed `Problem`.

**Production path:** YES — called by `pipeline/produce.py` (the `AnalysisResult` is built and its `.changes` drive the report).

### Recommendation Logic by Category

| Category | Parameters Adjusted | Physics Basis |
|---|---|---|
| safety | `front_heave_nmm`, `rear_third_nmm` | excursion ~ 1/√k; ×2.04 for vortex/front bottom, ×1.4 for rear |
| platform | `front_heave_nmm`, `rear_third_nmm` | σ ~ 1/√k; solve for target σ (8mm front, 10mm rear) |
| balance | `rear_arb_blade`, `front_arb_blade`, `rear_rh_at_speed_mm`, `diff_preload_nm` | LLTD shift via ARB; aero balance via RH; diff for body slip |
| damper | `front_ls_rbd`, `rear_ls_rbd`, `front_hs_comp`, `rear_hs_comp` | ±1 click based on settle time (>200ms or <50ms) |
| thermal | `front_camber_deg`, `rear_camber_deg`, `front_toe_deg`, `rear_toe_deg`, `front_cold_pressure_kpa`, `rear_cold_pressure_kpa` | Temp spread → camber ±0.1°; carcass <80°C → more toe; pressure window targeting |
| grip | `tc_slip`, `diff_preload_nm`, `brake_bias_pct` | Rear slip → TC/diff; front lock → bias shift |

### Conflict Resolution

After generating recommendations, `conflict_resolver.resolve_conflicts(changes)` is called to handle contradictory adjustments (e.g., understeer says soften rear ARB, body slip says stiffen it). Strategies: priority-based, compromise, Pareto-weighted.

---

## analyzer/setup_reader.py

**Purpose:** Extract current car setup from IBT session info YAML or binary STO file into `CurrentSetup` dataclass.

**Production path:** YES — called by `pipeline/produce.py` for every IBT.

### Key Features

- `CurrentSetup.from_ibt(ibt)` — parses YAML session info for all garage-settable parameters.
- `CurrentSetup.from_sto(path, car)` — parses binary STO via `sto_binary.decode_sto()` + `sto_adapters.adapt_sto()`.
- Handles unit parsing: "50 N/mm", "14.00 mm", "52.0%" etc.
- BMW-specific: Parses CarSetup structure with Chassis/Front/Rear/LeftFront etc. hierarchy.

---

## analyzer/sto_reader.py

**Purpose:** CLI tool for inspecting binary STO files.

**Production path:** NO — debug/inspection utility only.

---

## analyzer/sto_binary.py

**Purpose:** Low-level decoding of iRacing version-3 binary STO containers.

**Production path:** YES — called by `setup_reader.py::CurrentSetup.from_sto()`.

### Key Details

- `decode_sto(path)` → `DecodedSto` with header words, SHA256 hash, inferred car ID, provider name, notes, raw chunks.
- `_infer_car_id()` — heuristic from filename.
- `_infer_provider_name()` — heuristic from notes.
- BMW-specific: Filename heuristics for car detection.

---

## analyzer/sto_adapters.py

**Purpose:** Car-specific mapping from raw `DecodedSto` payload to canonical setup parameters.

**Production path:** YES — called by `setup_reader.py::CurrentSetup.from_sto()`.

### Key Details

- Registry of known STO hashes (currently Acura examples only).
- `adapt_sto(decoded, car)` — main adaptation function.
- `build_current_setup_fields()`, `build_diff_rows()` — for comparison output.

---

## analyzer/setup_schema.py

**Purpose:** Canonical schema (`SetupField`, `SetupSchema`) for setup parameters. Correlates IBT YAML, Ferrari LDX XML, and live telemetry channels.

**Production path:** YES — called by `pipeline/produce.py`.

### Key Features

- `build_setup_schema(ibt, setup, car, ...)` — constructs schema combining IBT session info + optional LDX files.
- `apply_live_control_overrides(schema, measured)` — promotes stable in-car adjustments (brake bias, TC, ARB) to authoritative setup view.
- Ferrari-specific: `find_matching_ferrari_ldx()`, `parse_ldx_setup_entries()`.

---

## analyzer/state_inference.py

**Purpose:** Infers high-level car states from `MeasuredState`, `CurrentSetup`, and `Problem` diagnoses.

**Production path:** YES — called by `diagnose()` after problems are identified.

### Key States Identified (`CarStateIssue`)

- "front platform collapse under braking" (heave travel + braking pitch)
- "front platform near limit high speed" (variance + excursion)
- "rear platform under/over supported"
- "entry front limited" / "exit traction limited"
- "balance asymmetric" (left/right understeer split)
- "brake system front limited" (ABS + lock proxy)
- "thermal window invalid" (carcass out of range)

Each state has: severity, confidence, estimated time loss, implicated solver steps, evidence list, causes, recommended direction. Confidence is scaled by `_driver_confidence()`.

---

## analyzer/telemetry_truth.py

**Purpose:** Signal quality assessment and bundling framework.

**Production path:** YES — called by `extract.py` at the end of extraction.

### Key Components

- `TelemetrySignal[T]` — value + quality ("trusted"/"derived"/"proxy"/"unknown") + confidence (0-1) + source + invalidation reasons.
- `build_signal_map(measured)` — creates signal dict from `MeasuredState`, assigning quality based on derivation method and fallback usage.
- `build_telemetry_bundle(signals)` — organizes signals into domain bundles: `aero_platform`, `braking_platform`, `traction_balance`, etc.
- `SessionNormalization` — session quality normalization context.
- `get_signal(measured, field_name)` — used by `diagnose.py` for settle time (quality-gated).
- `usable_signal_value(measured, field, ...)` — used by `pipeline/reason.py` for quality-gated metric access.

---

## analyzer/context.py

**Purpose:** Build `SessionContext` summarizing session quality and comparability.

**Production path:** YES — called by `pipeline/produce.py`.

### Context Factors

- `fuel_confidence` — fuel level relative to expected.
- `tyre_state_confidence` — wear level assessment.
- `thermal_validity` — carcass in working range.
- `pace_validity` — lap time vs expected.
- `traffic_confidence` — estimated from speed variance.
- `weather_confidence` — environmental stability.
- `overall_score` — weighted combination.

---

## analyzer/conflict_resolver.py

**Purpose:** Resolve conflicting `SetupChange` recommendations targeting the same parameter.

**Production path:** YES — called by `recommend.py`.

### Resolution Strategies

1. `_resolve_by_priority` — higher-priority problem wins.
2. `_resolve_by_compromise` — weighted average based on confidence.
3. `_resolve_with_pareto` — Pareto-optimal selection for multi-objective tradeoffs.

### Physics-Aware Coupling

- `INDIRECT_CONFLICTS` — parameter coupling map (e.g., front_heave_nmm ↔ rear_third_nmm).
- `COMPENSATION_RULES` — compensating actions (e.g., if front heave goes up, increase front perch).

---

## analyzer/causal_graph.py

**Purpose:** Static causal graph for root cause analysis of diagnosed problems.

**Production path:** YES — called by `diagnose()` when problems are present.

### Graph Structure

- `NODES` — dict of `CausalNode` (root cause, intermediate, symptom).
- `EDGES` — dict of `CausalEdge` with mechanism and strength.
- `analyze_causes(problems)` → `CausalDiagnosis` with root cause analyses and causal chains.
- `_match_problem_to_symptom()` — maps `Problem` objects to graph nodes.

---

## analyzer/adaptive_thresholds.py

**Purpose:** Compute diagnostic thresholds that adapt to track/car/driver context.

**Production path:** YES — called by `pipeline/produce.py` before `diagnose()`.

### Adaptation Mechanisms

1. **Track scaling** (`_track_scale_factor`): shock_vel_p99 / 200 mm/s baseline; range [0.7, 1.5]. Applied to: front/rear RH variance (α=0.30), excursion (0.15), bottoming events (0.50), settle time (0.20), roll rate (0.15).

2. **Driver scaling** (`_driver_scale_factor`): smooth-consistent × 0.85; aggressive-erratic × 1.20; aggressive × 1.10; erratic × 1.10. Applied to: understeer, body slip, settle time thresholds. Aggressive steering additionally tightens settle time × 0.85.

3. **Car baselines** (`CAR_BASELINES`): Per-car nominal understeer and body slip. Understeer threshold = nominal + 1.5° allowable deviation. Speed-specific: HS is 0.5° stricter, LS is 0.5° more lenient.

### Hardcoded Constants

- `BASELINE_SURFACE_SEVERITY_MPS = 0.200` (200 mm/s)
- `CAR_BASELINES`: BMW nominal US = 1.5°, Ferrari/Porsche = 2.0°, Cadillac = 1.5°, Acura = 1.5°
- `DEFAULT_CAR_BASELINE`: US = 1.5°, body slip = 2.5°
- All values in `BASELINE_THRESHOLDS` dict

---

## analyzer/stint_analysis.py

**Purpose:** Multi-lap stint analysis: lap snapshots, degradation rates, stint segmentation, quality assessment.

**Production path:** YES (conditional) — called by `pipeline/produce.py` when `--stint` is enabled.

### Key Data Structures

- `LapSnapshot` — subset of `MeasuredState` for per-lap tracking.
- `DegradationRates` — linear regression slopes and R² for fuel, temps, pressures, lap time.
- `LapQuality` — usable/downgraded/hard_reject classification (pit contamination, fuel reset detection).
- `StintDataset` / `StintEvolution` — full stint organization and summary.

---

## analyzer/overhaul.py

**Purpose:** Classify overhaul level (minor tweak, moderate rework, baseline reset) from `CarStateIssue` severity.

**Production path:** YES — called by `diagnose()`.

### Classification Logic

- Aggregates scores from state issues weighted by severity and confidence.
- Contextual distance factors (telemetry envelope, setup cluster) adjust score.
- Output: `OverhaulAssessment` with classification, confidence, score, reasons.

---

## analyzer/report.py

**Purpose:** Format analysis results for ASCII terminal output and JSON export.

**Production path:** YES — called by `pipeline/produce.py` and `pipeline/report.py`.

### Key Functions

- `format_report(result, ...)` — generates 63-char-width ASCII report with current setup, diagnosis, recommended changes, telemetry data sections.
- `save_analysis_json(result, path)` — comprehensive JSON export.

---

## analyzer/__init__.py / __main__.py {#analyzerinit}

- `__init__.py` — module docstring only.
- `__main__.py` — CLI entry point that delegates to `pipeline.produce.produce_result()`. Accepts `--car`, `--ibt`, `--wing`, `--sto`, `--json`, `--lap`, `--learn`, `--auto-learn`, `--stint` arguments. BMW is the default car.

---

## pipeline/produce.py

**Purpose:** Core end-to-end orchestrator — IBT → extract → diagnose → solve → .sto output.

**Production path:** YES — this IS the production path.

### Full End-to-End Flow

```
1.  Parse args, resolve scenario profile
2.  Load CarModel (get_car)
3.  If multiple IBTs → delegate to reason_and_solve() (reason.py)
4.  Open IBTFile
5.  Apply learned corrections (LearnedCorrections from KnowledgeStore)
    - heave mass effect, prediction errors, empirical corrections
    - Disable with --no-learn
6.  Load aero surfaces (load_car_surfaces)
7.  Build TrackProfile (build_profile) or load saved
8.  Extract measurements (extract_measurements) for best/specified lap
9.  Apply live_control_overrides (brake bias, TC, ARB from telemetry)
10. Build setup schema (build_setup_schema)
11. [Optional] Stint analysis (build_stint_dataset, dataset_to_evolution)
12. Corner segmentation (segment_lap)
13. Driver style analysis (analyze_driver → refine → separate_noise)
14. Compute adaptive thresholds (compute_adaptive_thresholds)
15. Diagnose (diagnose → problems, state_issues, causal_diagnosis)
16. Build session context (build_session_context)
17. [Optional] Query learner for envelope/cluster distances
18. Compute aero gradients (compute_gradients)
19. Compute solver modifiers (compute_modifiers from diagnosis + driver + measured)
20. Run 6-step solver:
    a. RakeSolver (Step 1: ride heights, pushrod offsets)
    b. HeaveSolver (Step 2: heave/third springs)
    c. CornerSpringSolver (Step 3: corner springs)
    d. ARBSolver (Step 4: ARBs, LLTD)
    e. WheelGeometrySolver (Step 5: camber, toe)
    f. DamperSolver (Step 6: all damper clicks)
    — Includes fixed-point refinement passes
21. SupportingSolver (brake bias, diff, TC, pressures)
22. [Optional] Legal-manifold search (GridSearchEngine / run_legal_search)
23. Write .sto (write_sto) if --sto
24. Write JSON summary if --json
25. Generate report (pipeline.report.generate_report)
26. [Optional] Auto-learn: ingest into KnowledgeStore, update HeaveCalibration
```

### Key Decision Points

- **Multiple IBTs:** Delegates entirely to `reason_and_solve()` (9-phase pipeline).
- **Learned corrections:** Applied before solver if `--no-learn` not set; requires KnowledgeStore with ≥3/5 sessions.
- **Solver path selection:** `optimize_if_supported()` for BMW/Sebring; otherwise standard 6-step sequential.
- **Legal search:** Activated by `--explore-legal-space`, `--search-mode`, or `--free` flags.
- **Stint compromise:** If `--stint` and enough usable laps, runs `solve_stint_compromise()`.
- **Run trace:** `RunTrace` records all major steps, decisions, overrides.

### BMW-Specific Logic

- `optimize_if_supported()` gating for BMW/Sebring.
- `HeaveCalibration` update in auto-learn path.
- Default min_lap_time = 108.0s (Sebring calibrated).

---

## pipeline/reason.py

**Purpose:** Enhanced multi-session reasoning engine (9-phase pipeline). Reads N IBT files, builds evolving understanding via all-pairs delta analysis, per-corner profiling, speed-regime separation, target telemetry construction, historical knowledge integration, physics cross-validation, and confidence-gated modifier generation.

**Production path:** YES — called by `produce.py` when multiple IBTs are provided.

### 9-Phase Pipeline

| Phase | Function | Purpose |
|---|---|---|
| 1 | `_load_sessions_into_state()` → `_analyze_session()` | Extract each IBT into `SessionSnapshot` |
| 2 | `_all_pairs_deltas()` | Compare every pair of sessions, weighted by quality |
| 3 | `_build_corner_profiles()` | Per-corner weakness map across sessions |
| 4 | `_analyze_speed_regimes()` | Separate HS aero vs LS mechanical problems |
| 5 | `_build_target_profile()` | Cherry-pick best metrics → ideal car state |
| 6 | `_integrate_historical()` | Query learner for prior knowledge |
| 7 | `_run_physics_reasoning()` | Cross-validate, category scoring, trade-offs |
| 8 | `_reason_to_modifiers()` | Sensitivity-scaled, confidence-gated modifiers |
| 9 | Solve + Report | 6-step solver, enhanced report |

### Key Data Structures

- `SessionSnapshot` — everything from one IBT (setup, measured, driver, diagnosis, corners, observation, fingerprint, stint data).
- `WeightedDelta` — session delta with quality weighting.
- `ParameterLearning` — accumulated parameter direction/confidence from deltas.
- `CornerProfile` — cross-session per-corner weakness analysis.
- `SpeedRegimeAnalysis` — HS vs LS problem separation.
- `TargetProfile` / `TargetGap` — ideal metric values and gaps.
- `ReasoningState` — master state accumulator for all 9 phases.

### Key Features

- Auto-detects minimum lap time floor from fastest observed lap × 0.95.
- Authority session selection (best or validated setup).
- Validation clusters via `SetupFingerprint`.
- Aggregate measured state from quality-weighted sessions.
- Confidence-gated modifier generation with aero gradient scaling.
- Handles setup rotation controls preservation.

---

## pipeline/report.py

**Purpose:** Generate comprehensive pipeline report extending the garage card with telemetry context, driver profile, handling diagnosis, and predicted improvements.

**Production path:** YES — called by `produce.py`.

### Key Functions

- `generate_report(...)` — orchestrates building the detailed report.
- `_build_prediction_lines(...)` — generates comparison lines: measured telemetry vs predicted telemetry after solver application. Includes `prediction_confidence` score.

---

## pipeline/preset_compare.py

**Purpose:** CLI tool to generate and compare setup "presets" (race, sprint, quali) from IBTs.

**Production path:** YES (CLI tool) — standalone executable via `python -m pipeline.preset_compare`.

### Preset Definitions (`PRESET_CONFIGS`)

- `race` — scenario_profile="race", stint behavior enabled.
- `sprint` — scenario_profile="sprint".
- `quali` — scenario_profile="quali", low fuel load.

### Comparison Fields (`COMPARISON_FIELDS`)

Lists ~30+ setup parameters compared side-by-side across presets.

---

## pipeline/__main__.py

**Purpose:** Entry point for `python -m pipeline`. Calls `pipeline.produce.main()`.

**Production path:** YES.

---

## Telemetry Channel Flow: extract → diagnose → modifiers → solver {#telemetry-channel-flow}

This traces how raw IBT channels flow through the pipeline into actual solver decisions.

### Flow 1: Ride Height → Aero Platform → Rake Solver (Step 1)

```
IBT: LF/RF/LR/RRrideHeight
  → extract.py: mean_front/rear_rh_at_speed_mm, front/rear_rh_std_mm, aero_compression_*
  → diagnose.py: _check_platform() — front/rear RH variance vs adaptive threshold
  → modifiers.py: front_heave_vel_hs_pct → front_heave_min_floor_nmm
                   pitch_range_deg > 1.5° → heave floor 38 N/mm
  → solver/solve.py: RakeSolver uses aero model + modified balance target
```

### Flow 2: Understeer → Balance → LLTD & DF Balance Modifiers → ARB Solver (Step 4)

```
IBT: SteeringWheelAngle, YawRate, Speed, VelocityX
  → extract.py: understeer_mean_deg, understeer_low/high_speed_deg
  → diagnose.py: _check_balance() — understeer > adaptive threshold (car-specific nominal + 1.5°)
  → modifiers.py:
      understeer > 2.5° → lltd_offset -= 0.02
      speed_gradient > 1.5° → df_balance_offset_pct += 0.5
      directional asymmetry > 0.3° → lltd_offset ± 0.01
  → solver/solve.py: ARBSolver applies lltd_offset to target LLTD
                      RakeSolver applies df_balance_offset_pct to target DF balance
```

### Flow 3: Bottoming → Safety → Heave Floor → Heave Solver (Step 2)

```
IBT: LF/RF/LR/RRrideHeight, HFshockDefl, CFSRrideHeight
  → extract.py: bottoming_event_count_front_clean, heave_bottoming_events_front,
                 front_heave_travel_used_pct, splitter_scrape_events
  → diagnose.py: _check_safety() — clean-track bottoming > 5 events + direct confirmation
  → modifiers.py:
      front bottoming > 5 events → front_heave_min_floor_nmm = max(30, 35 + shock_vel_p99 × 50)
      heave travel ≥ 90% → floor = 60 N/mm
      heave travel ≥ 80% → floor = 50 N/mm
      heave travel exhausted → front_heave_perch_target_mm = -11.0
  → solver/solve.py: HeaveSolver enforces front_heave_min_floor_nmm as minimum spring rate
```

### Flow 4: Settle Time → Damper → LS Rebound Offset → Damper Solver (Step 6)

```
IBT: LF/RFshockVel + LF/RFrideHeight (event-based clean disturbance response)
  → extract.py: front/rear_rh_settle_time_ms (median of ≥3 clean events, TelemetrySignal-gated)
  → diagnose.py: _check_dampers() — settle > 200ms or < 50ms (quality-gated via get_signal)
  → modifiers.py:
      settle > 300ms → front/rear_ls_rbd_offset += 1 (underdamped)
      settle < 50ms → front/rear_ls_rbd_offset -= 1 (overdamped)
  → solver/solve.py: DamperSolver applies ls_rbd_offset to computed click values
```

### Flow 5: Driver Style → ζ Scaling + HS Offsets → Damper Solver (Step 6)

```
IBT: SteeringWheelAngle, Throttle, Brake, LatAccel (via corners)
  → segment.py: CornerAnalysis (trail_brake_pct, apex_speed_kph, etc.)
  → driver_style.py: DriverProfile (smoothness, aggression, consistency)
  → modifiers.py:
      smooth → damping_ratio_scale × 0.92
      aggressive steering → front_hs_comp_offset += 1
      limit cornering → HS comp +1 F+R
      aggressive-erratic → ζ × 1.05
  → solver/solve.py: DamperSolver applies damping_ratio_scale to target ζ values,
                      applies hs_comp_offset to computed click values
```

### Flow 6: Tyre Temps → Thermal Diagnosis → Camber Recommendation

```
IBT: LFtempL/R, RFtempL/R, etc. + LFtempCL/CR (carcass)
  → extract.py: front_temp_spread_lf_c, front_carcass_gradient_lf_c, etc.
  → diagnose.py: _check_thermal() — spread vs target (F: +10°C, R: +8°C inner-hot)
  → recommend.py: _recommend_thermal() — camber ±0.1° per corner, toe adjustment
  → NOTE: Thermal recommendations go to recommend.py::AnalysisResult, not to solver modifiers.
          Solver Step 5 (WheelGeometrySolver) uses physics model, not thermal diagnosis.
```

### Flow 7: Heave Shock Velocity → Platform → HS Comp Offset → Damper Solver

```
IBT: HFshockVel
  → extract.py: front_heave_vel_p95_mps, front_heave_vel_hs_pct
  → modifiers.py (directly, not via diagnose):
      heave_vel_hs_pct > 33% → heave floor 40 N/mm
      heave_vel_p95 > 0.35 m/s → front_hs_comp_offset += 1
  → solver/solve.py: HeaveSolver enforces floor; DamperSolver applies HS comp offset
```

### Flow 8: State Inference → Confidence Weighting → All Modifiers

```
diagnose.py: state_issues (CarStateIssue with confidence)
  → modifiers.py: _conf() extracts confidence from state issues for each modifier category
      lltd_conf from: entry_front_limited, exit_traction_limited, balance_asymmetric
      df_conf from: front_platform_near_limit_high_speed
      front_heave_conf from: front_platform_collapse_braking, front_platform_near_limit_high_speed
      damper_conf from: front_platform_collapse_braking, brake_system_front_limited, rear_platform_under_supported
  → _scale(value, confidence): 
      confidence ≥ 0.75 → full value
      confidence ≤ 0.35 → value × 0.25
      linear interpolation between
  → All modifier values are scaled by their category's confidence before clamping
  → Safety floors (heave travel, pitch range) are re-applied AFTER confidence scaling
```

---

## Hardcoded Constants & Thresholds Summary {#hardcoded-constants}

### extract.py

| Constant | Value | Context |
|---|---|---|
| At-speed mask | >150 kph, brake < 0.05 | Ride height analysis |
| High-speed aero mask | >200 kph, brake < 0.05 | m_eff filtering |
| Pit mask | speed < 5.0 kph | Static RH detection |
| Cornering mask | |lat_g| > 0.5g, speed > 40 kph | Understeer analysis |
| LLTD corner mask | |lat_g| > 1.0g | Roll distribution proxy |
| Low-speed corners | <120 kph, |lat_g| > 0.8g | Speed-dependent understeer |
| High-speed corners | >180 kph, |lat_g| > 0.5g | Speed-dependent understeer |
| Bottoming threshold | mean - 3σ | Bottoming events |
| Vortex burst threshold | mean - 3.5σ at speed | Vortex burst events |
| Splitter scrape | <2mm | Splitter scrape events |
| FFT freq range | 0.5-10.0 Hz | Natural frequency |
| min_lap_time default | 108.0s | Best lap selection (Sebring-calibrated) |
| LS regime | <25 mm/s | Heave velocity classification |
| HS regime | >100 mm/s | Heave velocity classification |
| Settle time min events | ≥3 clean events | Signal quality gate |

### diagnose.py

All baseline thresholds listed in `BASELINE_THRESHOLDS` dict above.

### adaptive_thresholds.py

| Constant | Value | Context |
|---|---|---|
| BASELINE_SURFACE_SEVERITY_MPS | 0.200 m/s | Track scaling reference |
| Track scale range | [0.7, 1.5] | Clamped range |
| Allowable US deviation | 1.5° | Above car nominal |
| HS speed threshold | -0.5° stricter | US threshold speed adjustment |
| Temp spread targets | Front: +10°C, Rear: +8°C | Inner-hot target |

### modifiers.py

| Constant | Value | Context |
|---|---|---|
| US → LLTD offset | -0.02 per problem | LLTD shift |
| OS → LLTD offset | +0.02 per problem | LLTD shift |
| Speed gradient → DF offset | ±0.5% | DF balance shift |
| Heave vel HS regime | >33% → floor 40 N/mm | Platform stability |
| Heave vel p95 | >0.35 m/s → HS comp +1 | Damper HS control |
| Pitch range | >1.5° → floor 38 N/mm | Platform stability |
| Travel ≥90% | → floor 60 N/mm | Bottoming risk |
| Travel ≥80% | → floor 50 N/mm | Bottoming risk |
| Travel ≥70% | → floor 40 N/mm | Bottoming risk |
| Smooth driver | ζ × 0.92 | Compliance |
| Aggressive-erratic | ζ × 1.05 | Forgiveness |
| Confidence gate: ≥0.75 | 100% value | Full modifier |
| Confidence gate: ≤0.35 | 25% value | Suppressed modifier |
| LLTD clamp | ±0.05 | Max cumulative |
| DF balance clamp | ±1.5% | Max cumulative |
| LS rbd offset clamp | ±2 clicks | Max cumulative |
| HS comp offset clamp | ±2 clicks | Max cumulative |
| ζ scale clamp | [0.80, 1.20] | Max cumulative |

---

## BMW-Specific vs Generic Logic {#bmw-specific-logic}

### analyzer/ — Mostly Generic

| File | BMW-Specific? | Details |
|---|---|---|
| extract.py | NO | Generic; uses CarModel parameters for car-specific values |
| diagnose.py | NO | Generic; thresholds come from adaptive_thresholds |
| segment.py | NO | Fully generic |
| driver_style.py | NO | Fully generic |
| recommend.py | NO | Generic; uses CarModel for ranges |
| setup_reader.py | PARTIAL | CarSetup YAML hierarchy parsing is BMW-structured but used for all cars; `from_sto` uses car adapters |
| sto_binary.py | PARTIAL | Filename-based car inference heuristics |
| sto_adapters.py | PARTIAL | Known hash registry currently only has Acura examples |
| setup_schema.py | PARTIAL | Ferrari LDX integration is car-specific |
| state_inference.py | NO | Generic |
| telemetry_truth.py | NO | Generic |
| context.py | NO | Generic |
| conflict_resolver.py | NO | Generic |
| causal_graph.py | NO | Generic (static graph) |
| adaptive_thresholds.py | YES | `CAR_BASELINES` dict has per-car entries (BMW, Ferrari, Porsche, Cadillac, Acura) |
| stint_analysis.py | NO | Generic |
| overhaul.py | NO | Generic |
| report.py | NO | Generic |
| __main__.py | PARTIAL | Default car is "bmw" |

### pipeline/ — Some BMW-Specific Paths

| File | BMW-Specific? | Details |
|---|---|---|
| produce.py | PARTIAL | `optimize_if_supported()` gates BMW/Sebring; HeaveCalibration auto-learn; default min_lap_time=108.0s |
| reason.py | PARTIAL | BMW coverage imports (`bmw_coverage`, `bmw_rotation_search`); auto-detect lap time floor mitigates this |
| report.py | NO | Generic |
| preset_compare.py | PARTIAL | Default car is "bmw" |
| __main__.py | NO | Delegates to produce.main() |

---

## Production Path Summary {#production-path-summary}

### Core Production Path (every IBT → .sto run)

```
pipeline/produce.py::produce()
  ├── analyzer/extract.py::extract_measurements()
  │     └── analyzer/telemetry_truth.py::build_signal_map(), build_telemetry_bundle()
  ├── analyzer/setup_reader.py::CurrentSetup.from_ibt()
  ├── analyzer/setup_schema.py::build_setup_schema(), apply_live_control_overrides()
  ├── analyzer/segment.py::segment_lap()
  ├── analyzer/driver_style.py::analyze_driver(), refine_driver_with_measured(), separate_driver_noise()
  ├── analyzer/adaptive_thresholds.py::compute_adaptive_thresholds()
  ├── analyzer/diagnose.py::diagnose()
  │     ├── analyzer/causal_graph.py::analyze_causes()
  │     ├── analyzer/state_inference.py::infer_car_states()
  │     └── analyzer/overhaul.py::assess_overhaul()
  ├── analyzer/context.py::build_session_context()
  ├── solver/modifiers.py::compute_modifiers()
  ├── solver/solve.py (6-step solver)
  ├── solver/supporting_solver.py
  ├── output/write_sto.py::write_sto()
  └── pipeline/report.py::generate_report()
```

### Multi-IBT Path (adds)

```
pipeline/reason.py::reason_and_solve() (9-phase)
  ├── All of the above per-session
  ├── learner/delta_detector.py::detect_delta()
  ├── solver/setup_fingerprint.py (validation clusters)
  ├── solver/candidate_search.py (legal manifold search)
  └── solver/scenario_profiles.py (scenario resolution)
```

### Modules NOT on Production Path

- `analyzer/sto_reader.py` — debug CLI only.
- `analyzer/stint_analysis.py` — conditional on `--stint` flag.
- `analyzer/recommend.py` — production via `diagnose()` → `recommend()` chain; used for `AnalysisResult` but solver modifiers are the primary tuning path.

### TODO/FIXME Comments

**None found** in either `analyzer/` or `pipeline/` directories.

---

## Key Architectural Observations

1. **Dual tuning paths:** The codebase has TWO ways to influence solver output:
   - `recommend.py` → `SetupChange` recommendations (human-readable, applied to `improved_setup`)
   - `modifiers.py` → `SolverModifiers` (directly fed into 6-step solver targets)
   These are independent systems. The solver modifiers are the "real" path; recommendations are for reporting and manual reference.

2. **Settle time quality gating:** The settle time signal uses `TelemetrySignal` quality gating — it requires ≥3 clean disturbance events with sustained settled windows. If the signal is "unknown" quality, `diagnose.py` skips the damper check entirely via `get_signal().usable()`.

3. **Confidence cascade:** State inference confidence propagates through the entire modifier chain. A low-confidence "exit_traction_limited" state will suppress the LLTD offset to 25% of its computed value.

4. **Kerb-aware bottoming:** Bottoming events are split into clean-track vs kerb. Only clean-track bottoming triggers safety diagnosis and modifier activation. Kerb bottoming is flagged as "minor" and advisory only.

5. **reason.py dominance for multi-IBT:** When multiple IBTs are provided, `reason_and_solve()` completely replaces the single-session pipeline. It runs its own modifier computation (`_reason_to_modifiers()`) which is more sophisticated than the single-session `compute_modifiers()` — it uses sensitivity-scaled, confidence-gated, regime-weighted logic with aero gradient awareness.
