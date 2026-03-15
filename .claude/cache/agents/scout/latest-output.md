# Codebase Report: Solver Physics Constants, Signatures, and CarModel Dataclasses
Generated: 2026-03-15

## 1. solver/damper_solver.py

### Zeta Constants (L261-263, L289-291)

| Regime | Line | Value | Notes |
|--------|------|-------|-------|
| LS front | L262 | 0.88 | Near-critical; soft springs need high zeta for entry control |
| LS rear  | L263 | 0.30 | Light; rear traction needs compliance |
| HS front | L289 | 0.45 | Platform control over bumps |
| HS rear  | L291 | 0.14 | Maximum compliance; stiff rear HS = snap oversteer |

All four are multiplied by damping_ratio_scale (L440-443) before use.

### solve() Signature (L401-410)

    def solve(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_dynamic_rh_mm: float,
        rear_dynamic_rh_mm: float,
        fuel_load_l: float = 89.0,
        damping_ratio_scale: float = 1.0,
        measured: MeasuredState | None = None,
    ) -> DamperSolution:

### Zeta to Coefficient to Clicks Flow (L430-480)

1. m_front = total_mass * weight_dist_front / 2  (L431-432)
2. c_crit = 2 * sqrt(k_wheel_nm * mass_kg)  (L219-225, k converted N/mm -> N/m internally)
3. c_ls_front = zeta_ls_f * c_crit_front  (L445)
4. c_hs_rear  = zeta_hs_r * c_crit_rear   (L448)
5. LS v_ref = 0.025 m/s (constant, L454)
6. HS v_ref = track.shock_vel_p95_front/rear_mps (per axle, L467-468)
7. F = c * v_ref; clicks = round(F / force_per_click), clamped to range (L473-480)
8. force_per_click: ls=18.0 N/click, hs=80.0 N/click (DamperModel)

### Rebound Ratios (_rbd_comp_ratio, L293-325)

    LS front = 0.86   (rbd < comp, wheel planting)
    LS rear  = 1.17   (more rbd to resist squat)
    HS front = 1.60   (platform recovery)
    HS rear  = 3.00   (prevent rear wheel bounce)

Applied: rbd_clicks = round(comp_clicks * ratio), clamped (L488-491)

---

## 2. solver/rake_solver.py

### Excursion Model (docstring L35-38; implemented via cars.py rh_excursion_p99)

    excursion_mm = shock_vel_p99_mps / (2 * pi * dominant_bump_freq_hz) * 1000

dominant_bump_freq_hz = 5.0 Hz (BMW Sebring, RideHeightVariance)
In solve(): uses shock_vel_p99_front_CLEAN_mps if > 0, else raw p99 (L349-352)

### Aero Compression Logic (_build_solution L232-239)

    track_speed = track.median_speed_kph  (or comp.ref_speed_kph if 0)
    front_comp = front_compression_mm * (track_speed / ref_speed_kph)^2
    rear_comp  = rear_compression_mm  * (track_speed / ref_speed_kph)^2
    static_front = dynamic_front + front_comp
    static_rear  = dynamic_rear  + rear_comp

BMW calibration: ref=230 kph, front_compression=15.0mm, rear_compression=9.5mm

### solve() Signature (L326-332)

    def solve(
        self,
        target_balance: float = 50.14,
        balance_tolerance: float = 0.1,
        fuel_load_l: float = 89.0,
        pin_front_min: bool = True,
    ) -> RakeSolution:

---

## 3. solver/arb_solver.py

### LLTD Target Computation (L263)

    target_lltd = car.weight_dist_front + 0.05 + lltd_offset

OptimumG baseline: static front weight fraction + 5%.
lltd_offset from solver modifiers (default 0.0).

### solve() Signature (L234-246)

    def solve(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        lltd_offset: float = 0.0,
    ) -> ARBSolution:

Both inputs are WHEEL rates (N/mm). Caller converts rear: spring_rate * MR^2 (MR=0.60 BMW).

### Roll Stiffness Formula (_corner_spring_roll_stiffness L184-203)

    k_wheel_nm  = k_spring_nmm * MR^2 * 1000
    t_half_m    = (track_width_mm / 2) / 1000
    k_roll_rad  = 2 * k_wheel_nm * t_half_m^2
    k_roll_deg  = k_roll_rad * (pi / 180)   # N*m/deg

Called with motion_ratio=1.0 (caller already converted to wheel rate).
Front track: 1730mm; Rear track: 1650mm (ARBModel, BMW).

---

## 4. solver/diff_solver.py

### Lateral Load Transfer Expression (_compute_preload L252-263)

    mass = car.total_mass(89.0)
    track_width_m = car.corner_spring.track_width_mm / 1000  # = 1600mm BMW
    lateral_llt_n = mass * peak_lat_g * 9.81 * track_width_m / (2 * car.wheelbase_m)
    preload_min = lateral_llt_n * 0.002   # -> 5-15 Nm for GTP

NOTE: formula uses wheelbase (2.740m) not cg_height. This is intentionally simplified
(not a rigorous ΔFz formula). cg_height_mm field exists but is NOT used here.

### Lock Percentage Formula (_lock_pct L354-371)

    ramp_rad    = radians(ramp_deg)
    lock_torque = preload + (n_plates * 45.0) / tan(ramp_rad)
    lock_pct    = min(100.0, lock_torque / torque_input * 100.0)

Constants: CLUTCH_TORQUE_PER_PLATE=45 Nm, BMW_DEFAULT_CLUTCH_PLATES=6
torque_input = max_torque_nm * 0.7 (L200) = 700 * 0.7 = 490 Nm

### solve() Signature (L180-185)

    def solve(
        self,
        driver: DriverProfile,
        measured: MeasuredState,
        track: TrackProfile | None = None,
    ) -> DiffSolution:

---

## 5. car_model/cars.py Key Dataclasses

### AeroCompression (L21-41)

    ref_speed_kph: float
    front_compression_mm: float
    rear_compression_mm: float
    # V^2 scaling:
    front_at_speed(v) = front_compression_mm * (v / ref_speed_kph)^2
    rear_at_speed(v)  = rear_compression_mm  * (v / ref_speed_kph)^2

### CornerSpringModel (L409-483) - fields for roll stiffness + lateral load transfer

    front_torsion_c: float           # k_wheel = C * OD^4
    front_torsion_od_ref_mm: float
    front_torsion_od_options: list   # discrete garage options
    rear_spring_range_nmm: tuple
    rear_spring_perch_baseline_mm: float
    front_motion_ratio: float = 1.0  # already wheel rate (baked into C*OD^4)
    rear_motion_ratio: float = 1.0   # BMW = 0.60 (calibrated from measured LLTD)
    track_width_mm: float = 1600.0   # used by diff_solver lateral LT calc
    cg_height_mm: float = 350.0      # available, NOT currently used in solver formulas

BMW-specific: front_torsion_c=0.0008036, rear_motion_ratio=0.60, track_width=1600, cg_height=350

### ARBModel (L486-514) - track widths

    track_width_front_mm: float = 1730.0   # BMW actual front contact point width
    track_width_rear_mm:  float = 1650.0   # BMW actual rear contact point width

### CarModel (L594-699) - selected fields

    mass_car_kg, mass_driver_kg=75.0, fuel_density_kg_per_l=0.742
    weight_dist_front: float = 0.47    (BMW calibrated: 0.4727)
    wheelbase_m: float = 2.740
    steering_ratio: float = 17.8
    aero_compression: AeroCompression
    corner_spring: CornerSpringModel   # contains track_width_mm, cg_height_mm
    arb: ARBModel                      # contains track_width_front_mm, track_width_rear_mm

### Roll Center Fields: NONE EXIST

No roll_center_height, roll_center_front, or roll_center_rear fields anywhere in cars.py.
The ARB solver uses a simplified LLTD formula without roll center height.

---

## Critical Inconsistency: track_width Split

| Consumer | Field Path | BMW Value | Purpose |
|----------|-----------|-----------|---------|
| diff_solver | car.corner_spring.track_width_mm | 1600.0 mm | Lateral LT baseline |
| arb_solver (front) | car.arb.track_width_front_mm | 1730.0 mm | Roll stiffness calc |
| arb_solver (rear) | car.arb.track_width_rear_mm | 1650.0 mm | Roll stiffness calc |

130mm difference between diff and arb front values. Intentional (different measurement points).
