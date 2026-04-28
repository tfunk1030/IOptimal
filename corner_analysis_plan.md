# Corner-by-Corner Balance Analysis System — Implementation Plan

**Date:** 2026-04-28
**Branch:** `gt3-phase0-foundations`
**Author:** IOptimal Setup Engineering

---

## 1. What Already Exists

### 1.1 Corner Segmentation (`analyzer/segment.py`)
- `CornerAnalysis` dataclass: rich per-corner metrics including entry/apex/exit speeds,
  shock velocities, ride heights, understeer_mean_deg, body_slip_peak_deg, trail_brake_pct,
  throttle_onset_dist_m, phase timings (braking, release, turn-in, entry, apex, exit),
  platform/traction risk flags, and roll_gradient_deg_per_g.
- `_detect_corners()`: smoothed |lat_g| threshold (0.5g, 15-sample kernel), returns
  (start_idx, apex_idx, end_idx, direction).
- `_detect_braking_zones()`: brake > 10% threshold, min 10 samples.
- `_match_braking_to_corner()`: associates braking zones with corner entries.
- `segment_lap()`: main function — loads all channels, detects corners, computes per-corner
  suspension/handling metrics, returns `list[CornerAnalysis]`.

### 1.2 Telemetry Extraction (`analyzer/extract.py`)
- `MeasuredState` dataclass: lap-aggregate metrics — RH at speed, shock velocities,
  understeer (mean, low-speed, high-speed), body slip, yaw rate correlation, slip ratios,
  tyre thermals, roll gradient. **Not per-corner-phase.**
- `aggregate_corner_roll_gradients()`: aggregates per-corner roll gradient samples.

### 1.3 Handling Diagnosis (`analyzer/diagnose.py`)
- `diagnose()` operates on **aggregate** `MeasuredState` — produces `Problem` list sorted
  by priority (safety → platform → balance → damper → thermal → grip).
- Accepts `corners` parameter but passes it through to `infer_car_states()` — does NOT
  do per-corner-phase balance diagnosis.
- Balance checks use lap-wide understeer_mean_deg, roll_distribution_proxy, body_slip.

### 1.4 Solver Chain (`solver/solve_chain.py`)
- `SolveChainInputs` accepts `corners: list[Any] | None` but the 6-step solver
  (rake → heave → corner spring → ARB → wheel geometry → damper) does not consume
  per-corner-phase balance data. Corners feed into `SolverModifiers` indirectly via
  driver style and diagnosis.

### 1.5 Recommendations (`analyzer/recommend.py`)
- `recommend()` maps diagnosed Problems to `SetupChange` objects with parameter, current,
  recommended, reasoning. Per-problem, not per-corner-phase.

### 1.6 Driver Style (`analyzer/driver_style.py`)
- Consumes `CornerAnalysis` list for trail braking, throttle onset, steering smoothness.
  Per-corner data aggregated into `DriverProfile`.

### 1.7 Solver Modifiers (`solver/modifiers.py`)
- `SolverModifiers` dataclass: df_balance_offset, heave floors, lltd_offset, damper offsets.
  Computed from diagnosis + driver profile. No per-corner-phase input.

### 1.8 Pipeline (`pipeline/produce.py`)
- Phase B: extract telemetry → MeasuredState
- Phase C: segment corners → list[CornerAnalysis]
- Phase D: driver style analysis
- Phase E: adaptive thresholds + diagnosis
- Phase F+: solver chain → .sto output + report
- **Gap:** No Phase C.5 for per-corner-phase balance analysis.

---

## 2. Architecture: Corner-by-Corner Balance Analysis

### 2.1 New Module: `analyzer/corner_balance.py`

#### Data Model

```
CornerPhase (Enum)
├── ENTRY   — brake point through trail-brake to turn-in
├── MID     — peak lateral G through to throttle application
└── EXIT    — throttle application through corner exit

PhaseBalance (Dataclass)
├── phase: CornerPhase
├── understeer_deg: float        # steering_angle - neutral_steer_angle
├── yaw_rate_error_pct: float    # (actual - expected) / expected × 100
├── rear_slip_proxy: float       # rear shock deflection asymmetry
├── lateral_load_transfer_front_pct: float
├── traction_utilization_pct: float  # exit only: throttle% vs rear slip%
├── lateral_g_mean: float
├── speed_kph_mean: float
├── duration_s: float
└── stability_margin: float      # headroom before limit

CornerBalance (Dataclass)
├── corner_id: int
├── corner_type: str             # "low" | "mid" | "high" (from speed_class)
├── direction: str               # "left" | "right"
├── lap_dist_start_m: float
├── entry: PhaseBalance
├── mid: PhaseBalance
└── exit: PhaseBalance

BalanceSummary (Dataclass)
├── dominant_entry_issue: str    # "understeer" | "oversteer" | "neutral"
├── dominant_mid_issue: str
├── dominant_exit_issue: str
├── entry_understeer_pct: float  # % of corners with entry US
├── mid_understeer_pct: float
├── exit_oversteer_pct: float
├── weighted_entry_us_deg: float # severity-weighted average
├── weighted_mid_us_deg: float
├── weighted_exit_us_deg: float
├── stability_margin_mean: float
├── high_speed_bias: str         # balance tendency in fast corners
├── low_speed_bias: str          # balance tendency in slow corners
└── corners: list[CornerBalance]
```

#### Core Functions

1. **`segment_corner_phases(corner: CornerAnalysis, telemetry_slice) -> (entry, mid, exit)`**
   - Entry: from corner start to peak |lat_g| where brake > 0 OR steering increasing
   - Mid: peak |lat_g| region (within 90% of peak), before throttle > 20%
   - Exit: from throttle onset to corner end

2. **`compute_phase_balance(phase_data, car, phase_type) -> PhaseBalance`**
   - Understeer angle = steering_angle/steering_ratio - (lat_g × 9.81 × wheelbase / speed²)
   - Yaw rate error = (measured_yaw - speed × lat_g × 9.81 / speed²) / expected
   - Rear slip proxy = |LR_shock_defl - RR_shock_defl| normalized
   - Front vs rear lateral load transfer from shock deflection differences
   - Traction utilization (exit only): throttle % vs rear slip %
   - Stability margin: (max_possible_lat_g - actual_lat_g) / max_possible_lat_g

3. **`analyze_corner_balance(ibt, start, end, car, corners) -> list[CornerBalance]`**
   - Main entry point. For each corner in `corners`, slices telemetry, segments phases,
     computes per-phase balance.

4. **`aggregate_balance(corners: list[CornerBalance]) -> BalanceSummary`**
   - Weight by corner severity: high-speed corners × 1.5 for aero params,
     low-speed × 1.5 for mechanical params.
   - Compute dominant issue per phase across all corners.
   - Identify if 70%+ of corners share same issue → priority fix.
   - Separate high-speed vs low-speed bias for compromise detection.

5. **`map_balance_to_params(summary: BalanceSummary, car) -> dict[str, float]`**
   - Entry understeer → front_heave_nmm ↑, brake_bias ↓, diff_coast ↓
   - Entry oversteer → rear_spring_nmm ↓, diff_coast ↑, brake_bias ↑
   - Mid understeer → front_arb ↓ OR rear_arb ↑ (LLTD shift), front_camber ↑, front_rh ↓
   - Mid oversteer → front_arb ↑ OR rear_arb ↓, rear_camber ↑, rear_rh ↓
   - Exit understeer → diff_preload ↑, rear_spring ↓
   - Exit oversteer → diff_preload ↓, TC ↑, rear_camber ↓
   - Magnitudes scaled by measured deficit (e.g., 1° US → specific N/mm change)

### 2.2 Data Flow Diagram

```
IBT File
  │
  ▼
track_model/build_profile.py ──► TrackProfile (corners, braking zones)
  │
  ▼
analyzer/extract.py ──────────► MeasuredState (lap-aggregate)
  │
  ▼
analyzer/segment.py ──────────► list[CornerAnalysis] (per-corner metrics)
  │
  ▼
analyzer/corner_balance.py ◄── NEW ──────────────────────────────────┐
  │                                                                   │
  ├─► segment_corner_phases() ──► (entry, mid, exit) per corner      │
  ├─► compute_phase_balance() ──► PhaseBalance per phase             │
  ├─► analyze_corner_balance() ─► list[CornerBalance]                │
  ├─► aggregate_balance() ──────► BalanceSummary                     │
  └─► map_balance_to_params() ──► dict[str, float]                   │
       │                                                              │
       ▼                                                              │
  solver/modifiers.py ◄── augmented with balance-driven offsets      │
       │                                                              │
       ▼                                                              │
  solver/solve_chain.py ──► 6-step solve with balance context        │
       │                                                              │
       ▼                                                              │
  pipeline/report.py ◄── corner balance section in eng report ───────┘
```

---

## 3. Per-File Changes

| File | Change | Effort |
|------|--------|--------|
| `analyzer/corner_balance.py` | **NEW** — core module (~450 lines) | 4h |
| `pipeline/produce.py` | Add Phase C.5 call after segment_lap, pass results downstream | 0.5h |
| `analyzer/report.py` or `pipeline/report.py` | Add corner balance section to engineering report | 1h |
| `solver/modifiers.py` | Add `from_corner_balance()` method to consume BalanceSummary | 1h |
| `analyzer/diagnose.py` | (Future) Replace aggregate balance checks with per-corner-phase | 2h |
| `tests/test_corner_balance.py` | Unit tests for phase segmentation, balance computation | 2h |

**Total estimated effort: ~10.5 hours**

---

## 4. Implementation Order

1. **`analyzer/corner_balance.py`** — core data model + functions (this PR)
2. **`pipeline/produce.py`** — wire into pipeline after Phase C (this PR)
3. **`pipeline/report.py`** — surface in engineering report (this PR, minimal)
4. `solver/modifiers.py` — feed balance summary into solver targets (follow-up)
5. `analyzer/diagnose.py` — replace aggregate diagnosis with per-phase (follow-up)
6. `tests/test_corner_balance.py` — unit tests (follow-up)

---

## 5. Integration Strategy

### Augments, Does Not Replace
The corner balance system **augments** the existing 6-step solver. It does NOT replace:
- `analyzer/segment.py` — still provides CornerAnalysis; corner_balance consumes it
- `analyzer/diagnose.py` — still runs aggregate diagnosis; corner balance adds per-phase detail
- `solver/modifiers.py` — still computes modifiers; balance summary provides additional offsets
- `solver/solve_chain.py` — still runs all 6 steps; balance context refines targets

### Phase 1 Deliverable (This PR)
- Core `analyzer/corner_balance.py` with all dataclasses and functions
- Pipeline integration (runs after segment_lap, before diagnosis)
- Balance summary logged and included in engineering report
- No changes to solver logic yet — balance data is computed and reported

### Phase 2 (Follow-Up)
- `map_balance_to_params()` output fed into `SolverModifiers`
- Per-corner-phase diagnosis replaces aggregate balance checks in `diagnose.py`
- Stability margin analysis drives wing/ARB trade-off recommendations

---

## 6. Balance-to-Parameter Mapping Reference

### Entry Phase
| Balance Issue | Primary Parameter | Secondary | Physics |
|--------------|-------------------|-----------|---------|
| Understeer | front_heave_nmm ↑ | brake_bias ↓, diff_coast_ramp ↓ | Nose dives on braking → front loses mechanical grip; too-rear bias underloads front |
| Oversteer | rear_spring_nmm ↓ | diff_coast_ramp ↑, brake_bias ↑ | Rear too stiff = snappy rotation; free diff coast = no rear stability |

### Mid-Corner Phase
| Balance Issue | Primary Parameter | Secondary | Physics |
|--------------|-------------------|-----------|---------|
| Understeer | front_arb ↓ / rear_arb ↑ | front_camber ↑, front_rh ↓ | LLTD too high = front saturates first; more camber/DF = more front grip |
| Oversteer | front_arb ↑ / rear_arb ↓ | rear_camber ↑, rear_rh ↓ | LLTD too low = rear saturates; more rear grip needed |

### Exit Phase
| Balance Issue | Primary Parameter | Secondary | Physics |
|--------------|-------------------|-----------|---------|
| Understeer | diff_preload ↑ | rear_spring ↓ | Low preload = no rear traction drive; stiff rear = traction loss |
| Oversteer | diff_preload ↓ | TC ↑, rear_camber ↓ | Too much preload = locks inside rear; excess camber = reduced contact patch |

### Stability Margin Analysis
| Condition | Action | Reasoning |
|-----------|--------|-----------|
| Neutral + low margin | Keep setup | At the limit, balance is right |
| Neutral + high margin | Reduce wing / soften ARBs | Trading stability for straight-line speed |
| Entry stable + exit unstable | Stiffen rear ARB, increase diff preload | Phase-specific compromise |

