"""Car physical model definitions.

Each car defines mass properties, suspension parameters, aero map axis
conventions, valid ride height ranges, and calibrated aero compression data.

IMPORTANT — Aero map axis swap:
    In the parsed aero maps, the "front_rh" axis (rows, 25-75mm) actually
    represents the REAR ride height, and the "rear_rh" axis (cols, 5-50mm)
    represents the FRONT ride height. This is because the xlsx spreadsheets
    label rows as "front" and columns as "rear", but the physical mapping
    is inverted. The CarModel stores this convention and the solver handles
    the coordinate transform.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AeroCompression:
    """Calibrated aero compression model for static-to-dynamic RH conversion.

    At a reference speed, aero loads compress the suspension from the static
    garage ride height down to the dynamic ride height at speed.

    compression(V) = compression_ref * (V / V_ref)^2

    This V-squared scaling is physically correct since aero force ~ V^2.
    """
    ref_speed_kph: float             # Speed at which compression was calibrated
    front_compression_mm: float      # Front RH compression at ref speed
    rear_compression_mm: float       # Rear RH compression at ref speed

    def front_at_speed(self, speed_kph: float) -> float:
        """Front aero compression (mm) at a given speed."""
        return self.front_compression_mm * (speed_kph / self.ref_speed_kph) ** 2

    def rear_at_speed(self, speed_kph: float) -> float:
        """Rear aero compression (mm) at a given speed."""
        return self.rear_compression_mm * (speed_kph / self.ref_speed_kph) ** 2


@dataclass
class PushrodGeometry:
    """Pushrod offset to ride height relationship.

    For GTP cars, the front static ride height is pinned at the sim-enforced
    minimum (30mm for all GTP cars). The front pushrod offset does NOT change
    the static ride height — it only affects dynamic preload and damper travel.
    This is confirmed across 6 sessions: pushrod offsets -23.0, -25.0, -25.5
    all produce exactly 30.0mm front static RH.

    The rear relationship is:
        static_rh = rear_base_rh + pushrod_offset * rear_pushrod_to_rh

    However, the rear pushrod-to-RH ratio is very weak (~0.05-0.1 mm/mm),
    much less than the naively-expected 1:1. The dominant rear RH control
    is through the spring perch, not the pushrod. The pushrod primarily
    sets the damper/third-element preload.

    Calibrated from 6 BMW Sebring sessions:
    - Front: pinned at 30.0mm regardless of pushrod offset
    - Rear: base ~46.7mm, ratio ~-0.09 (weak, noisy)
      pushrod -29 → 49.1-49.5mm, pushrod -16.5 → 48.1mm
    """
    front_pinned_rh_mm: float        # Sim-enforced front static RH
    front_pushrod_default_mm: float  # Recommended front pushrod offset
    rear_base_rh_mm: float           # Rear RH with pushrod at 0 offset
    rear_pushrod_to_rh: float        # mm RH change per mm pushrod offset (very weak ~-0.096)

    def front_offset_for_rh(self, target_rh: float) -> float:
        """Front pushrod offset — returns default since front RH is pinned."""
        # Front RH is sim-floor pinned; pushrod doesn't change it.
        # Return the default pushrod offset.
        return self.front_pushrod_default_mm

    def rear_offset_for_rh(self, target_rh: float) -> float:
        """Pushrod offset needed to achieve target rear static RH."""
        if abs(self.rear_pushrod_to_rh) < 1e-6:
            return -29.0  # Default if ratio is effectively zero
        return (target_rh - self.rear_base_rh_mm) / self.rear_pushrod_to_rh

    def front_rh_for_offset(self, offset: float) -> float:
        """Front static RH — always returns pinned value."""
        return self.front_pinned_rh_mm

    def rear_rh_for_offset(self, offset: float) -> float:
        """Rear static RH resulting from a given pushrod offset."""
        return self.rear_base_rh_mm + offset * self.rear_pushrod_to_rh


@dataclass
class RideHeightModel:
    """Multi-variable static RH prediction from calibration regression.

    Rear: rear_static_rh = intercept + Σ(coeff_i * param_i)
    Front: front_static_rh = front_intercept + Σ(coeff_i * param_i)

    Calibrated from 13 BMW Sebring sessions (March 2026).

    Front model (2 features): R² ~ 0.64, LOO-CV RMSE = 0.066 mm
      front_camber_deg (r=0.64) and front_heave_nmm (r=0.50)
    Rear model (4 features): R² = 0.9655, LOO-CV RMSE = 0.845 mm
    """
    # --- Front static RH regression ---
    front_intercept: float = 30.0   # fallback: acts as pinned value when coeffs are 0
    front_coeff_heave_nmm: float = 0.0     # mm RH per N/mm heave spring rate
    front_coeff_camber_deg: float = 0.0    # mm RH per deg front camber
    front_loo_rmse_mm: float = 0.0

    # --- Rear static RH regression ---
    rear_intercept: float = 0.0
    rear_coeff_pushrod: float = 0.0        # mm RH per mm pushrod offset
    rear_coeff_third_nmm: float = 0.0      # mm RH per N/mm third spring rate
    rear_coeff_rear_spring: float = 0.0    # mm RH per N/mm rear spring rate
    rear_coeff_heave_perch: float = 0.0    # mm RH per mm front heave perch offset
    rear_r_squared: float = 0.0
    rear_loo_rmse_mm: float = 0.0

    def predict_front_static_rh(
        self, heave_nmm: float, camber_deg: float,
    ) -> float:
        """Predict front static RH from setup parameters."""
        return (self.front_intercept
                + self.front_coeff_heave_nmm * heave_nmm
                + self.front_coeff_camber_deg * camber_deg)

    def predict_rear_static_rh(
        self, pushrod_mm: float, third_nmm: float,
        rear_spring_nmm: float, heave_perch_mm: float,
    ) -> float:
        """Predict rear static RH from setup parameters."""
        return (self.rear_intercept
                + self.rear_coeff_pushrod * pushrod_mm
                + self.rear_coeff_third_nmm * third_nmm
                + self.rear_coeff_rear_spring * rear_spring_nmm
                + self.rear_coeff_heave_perch * heave_perch_mm)

    def pushrod_for_target_rh(
        self, target_rh_mm: float, third_nmm: float,
        rear_spring_nmm: float, heave_perch_mm: float,
    ) -> float:
        """Solve for the pushrod offset that achieves a target rear static RH."""
        if abs(self.rear_coeff_pushrod) < 1e-6:
            return -29.0  # Fallback if pushrod has no effect
        other = (self.rear_intercept
                 + self.rear_coeff_third_nmm * third_nmm
                 + self.rear_coeff_rear_spring * rear_spring_nmm
                 + self.rear_coeff_heave_perch * heave_perch_mm)
        return (target_rh_mm - other) / self.rear_coeff_pushrod

    @property
    def is_calibrated(self) -> bool:
        """True if rear model has non-zero coefficients."""
        return abs(self.rear_coeff_pushrod) > 1e-6

    @property
    def front_is_calibrated(self) -> bool:
        """True if front model has non-zero coefficients."""
        return (abs(self.front_coeff_heave_nmm) > 1e-6
                or abs(self.front_coeff_camber_deg) > 1e-6)


@dataclass
class HeaveSpringModel:
    """Calibrated heave/third spring physics model.

    Models ride height excursion as a function of spring rate:
        excursion(k) = v_p99 * sqrt(m_eff / k)

    Where m_eff is an effective heave mass calibrated from telemetry.
    This is NOT the physical sprung mass — it's a lumped parameter that
    captures the frequency-domain coupling between track surface excitation,
    suspension geometry, and ride height response.

    Two constraints per axle:
    - Bottoming: excursion_p99 < dynamic_RH (binding for front)
    - Variance: sigma = excursion / 2.33 < sigma_target (binding for rear)

    The 2.33 divisor converts p99 excursion to sigma (p99 = mean + 2.33*sigma
    for a Gaussian distribution).
    """
    front_m_eff_kg: float            # Calibrated front effective heave mass
    rear_m_eff_kg: float             # Calibrated rear effective heave mass
    front_spring_range_nmm: tuple[float, float] = (20.0, 200.0)  # Valid range
    rear_spring_range_nmm: tuple[float, float] = (100.0, 1000.0)
    sigma_target_mm: float = 10.0    # Platform stability threshold
    perch_offset_front_baseline_mm: float = -13.0  # Verified baseline
    perch_offset_rear_baseline_mm: float = 43.0  # Integer-only in iRacing garage
    # HeaveSpringDeflMax calibration: DeflMax = defl_max_intercept + defl_max_slope * spring_rate
    # Derived from 19 BMW Sebring sessions (March 2026):
    #   Heave 30 -> 97.7mm, 50 -> 90.2mm, 70 -> 84.8mm, 90 -> 80.4mm
    heave_spring_defl_max_intercept_mm: float = 103.4
    heave_spring_defl_max_slope: float = -0.262  # mm per N/mm of spring rate
    # Torsion bar turns calibration (OD=13.9 baseline):
    #   Turns = turns_intercept + turns_heave_coeff / heave_spring_nmm
    torsion_bar_turns_intercept: float = 0.0856
    torsion_bar_turns_heave_coeff: float = 0.668
    torsion_bar_turns_baseline_od_mm: float = 13.9
    front_heave_hard_range_nmm: tuple[float, float] | None = None  # Car-specific hard clamp
    front_heave_hard_range_exempt_tracks: list[str] = field(default_factory=list)


@dataclass
class CornerSpringModel:
    """Corner spring physics model (torsion bars front, coil springs rear).

    Corner springs contribute to BOTH heave stiffness AND roll stiffness.
    Heave springs contribute to heave ONLY (geometric decoupling in roll).
    ARBs contribute to roll ONLY.

    Key relationships:
    - Total heave stiffness per axle = heave_spring + 2 * corner_wheel_rate
    - Natural frequency per corner = (1/2pi) * sqrt(k_wheel / m_corner)
    - Heave-to-corner ratio should be 1.5-3.5x (SKILL.md guideline)
    - Front torsion bar rate scales as OD^4: k = C_torsion * OD^4
    - Rear coil spring rate is a direct N/mm value

    The torsion bar constant C_torsion is calibrated from the verified setup
    (OD = 13.9mm maps to a known wheel rate through the suspension geometry).
    """
    # Front torsion bar
    front_torsion_c: float           # Calibration constant: k_wheel = C * OD^4
    front_torsion_od_ref_mm: float   # Reference OD for calibration
    front_torsion_od_range_mm: tuple[float, float] = (11.0, 16.0)
    front_torsion_od_step_mm: float = 0.10  # Garage step size (fallback if no options)
    # Discrete OD options from iRacing garage — if set, snap_torsion_od uses these
    # instead of continuous step rounding.
    front_torsion_od_options: list[float] = field(default_factory=list)

    # Rear coil spring
    rear_spring_range_nmm: tuple[float, float] = (100.0, 300.0)
    rear_spring_step_nmm: float = 10.0      # Garage step size

    # Calibrated perch offsets
    rear_spring_perch_baseline_mm: float = 30.0

    # Motion ratios (spring-to-wheel)
    # k_wheel = k_spring * MR^2
    # Front torsion bar: MR is already baked into the C*OD^4 calibration,
    # so front_motion_ratio = 1.0 (the formula already gives wheel rate).
    # Rear coil spring: iRacing reports spring rate at the damper, not at
    # the wheel. MR converts spring rate to wheel rate for roll stiffness.
    front_motion_ratio: float = 1.0   # Already in wheel-rate form
    rear_motion_ratio: float = 1.0    # Needs calibration per car

    # Track width (mm) for roll stiffness calculation
    track_width_mm: float = 1600.0

    # CG height estimate (mm) for lateral load transfer
    cg_height_mm: float = 350.0

    # Heave-to-corner ratio guideline
    heave_corner_ratio_range: tuple[float, float] = (1.5, 3.5)

    # Frequency isolation: corner freq should be < bump_freq / min_freq_ratio
    min_freq_isolation_ratio: float = 2.5

    def torsion_bar_rate(self, od_mm: float) -> float:
        """Wheel rate (N/mm) from torsion bar OD."""
        return self.front_torsion_c * od_mm ** 4

    def torsion_bar_od_for_rate(self, k_wheel_nmm: float) -> float:
        """Torsion bar OD (mm) needed for a target wheel rate."""
        return (k_wheel_nmm / self.front_torsion_c) ** 0.25

    def snap_torsion_od(self, od_mm: float) -> float:
        """Snap OD to nearest discrete garage option (or step if no options)."""
        if self.front_torsion_od_options:
            return min(self.front_torsion_od_options,
                       key=lambda x: abs(x - od_mm))
        step = self.front_torsion_od_step_mm
        return round(round(od_mm / step) * step, 2)

    def snap_rear_rate(self, k_nmm: float) -> float:
        """Snap rear spring rate to nearest garage step."""
        step = self.rear_spring_step_nmm
        return round(round(k_nmm / step) * step, 0)


@dataclass
class ARBModel:
    """Anti-roll bar definitions for a car."""
    front_size_labels: list[str]
    front_stiffness_nmm_deg: list[float]
    front_blade_count: int = 5
    front_baseline_size: str = "Soft"
    front_baseline_blade: int = 1
    rear_size_labels: list[str] = field(default_factory=lambda: ["Soft", "Medium", "Stiff"])
    rear_stiffness_nmm_deg: list[float] = field(default_factory=lambda: [5000.0, 10000.0, 15000.0])
    rear_blade_count: int = 5
    rear_baseline_size: str = "Medium"
    rear_baseline_blade: int = 3
    track_width_front_mm: float = 1730.0
    track_width_rear_mm: float = 1650.0

    def blade_factor(self, blade: int, max_blade: int) -> float:
        return 0.30 + 0.70 * (blade - 1) / max(max_blade - 1, 1)

    def front_roll_stiffness(self, size_label: str, blade: int) -> float:
        if size_label not in self.front_size_labels:
            size_label = self.front_baseline_size
        idx = self.front_size_labels.index(size_label)
        return self.front_stiffness_nmm_deg[idx] * self.blade_factor(blade, self.front_blade_count)

    def rear_roll_stiffness(self, size_label: str, blade: int) -> float:
        if size_label not in self.rear_size_labels:
            size_label = self.rear_baseline_size
        idx = self.rear_size_labels.index(size_label)
        return self.rear_stiffness_nmm_deg[idx] * self.blade_factor(blade, self.rear_blade_count)


@dataclass
class WheelGeometryModel:
    """Wheel alignment model (camber and toe)."""
    front_camber_range_deg: tuple[float, float] = (-5.0, 0.0)
    rear_camber_range_deg: tuple[float, float] = (-4.0, 0.0)
    front_camber_step_deg: float = 0.1
    rear_camber_step_deg: float = 0.1
    front_camber_baseline_deg: float = -2.9
    rear_camber_baseline_deg: float = -1.9
    front_roll_gain: float = 0.6
    rear_roll_gain: float = 0.5
    front_toe_range_mm: tuple[float, float] = (-3.0, 3.0)
    rear_toe_range_mm: tuple[float, float] = (-2.0, 3.0)
    front_toe_step_mm: float = 0.1
    rear_toe_step_mm: float = 0.1
    front_toe_baseline_mm: float = -0.4
    rear_toe_baseline_mm: float = 0.0
    front_toe_heating_coeff: float = 2.5
    rear_toe_heating_coeff: float = 1.8


@dataclass
class DamperModel:
    """Damper model parameterized in garage clicks."""
    ls_comp_range: tuple[int, int] = (1, 11)   # BMW/Dallara default; Ferrari overrides
    ls_rbd_range: tuple[int, int] = (1, 11)
    hs_comp_range: tuple[int, int] = (1, 11)
    hs_rbd_range: tuple[int, int] = (1, 11)
    hs_slope_range: tuple[int, int] = (1, 11)
    # Force-per-click calibrated by reverse-engineering from physics:
    # c_damping * v_ref / clicks = fpc
    # Front LS: 5060 * 0.025 / 7 = 18.1 N/click
    # Rear LS: 4358 * 0.025 / 6 = 18.2 N/click ← remarkably consistent!
    # Front HS: 2586 * 0.15 / 5 = 77.6 N/click
    # Rear HS: 2034 * 0.15 / 3 = 101.7 N/click
    ls_force_per_click_n: float = 18.0     # N per click at 25 mm/s
    hs_force_per_click_n: float = 80.0     # N per click at 150 mm/s
    # Calibrated from BMW Sebring Setup 2 ("locked platform")
    front_ls_comp_baseline: int = 7
    front_ls_rbd_baseline: int = 6
    front_hs_comp_baseline: int = 5
    front_hs_rbd_baseline: int = 8
    front_hs_slope_baseline: int = 10
    rear_ls_comp_baseline: int = 6
    rear_ls_rbd_baseline: int = 7
    rear_hs_comp_baseline: int = 3
    rear_hs_rbd_baseline: int = 9
    rear_hs_slope_baseline: int = 10
    rbd_comp_ratio_target: float = 1.6  # HS rbd:comp from S2 front (8/5)
    ls_threshold_mps: float = 0.05

    def snap_click(self, value: float, param: str) -> int:
        lo, hi = getattr(self, f"{param}_range")
        return max(lo, min(hi, round(value)))


@dataclass
class RideHeightVariance:
    """Model for ride height oscillation at speed from track surface bumps.

    Converts shock velocity percentiles to estimated ride height excursion
    using: excursion = shock_vel / (2 * pi * dominant_freq)

    The dominant frequency is the characteristic bump frequency of the
    track surface, estimated from the shock velocity spectrum.
    """
    dominant_bump_freq_hz: float     # Characteristic bump frequency


@dataclass
class CarModel:
    """Physical model for a GTP/Hypercar car."""

    name: str
    canonical_name: str              # "bmw", "ferrari", etc.

    # Mass properties
    mass_car_kg: float               # Dry car mass
    mass_driver_kg: float = 75.0     # Driver mass
    fuel_density_kg_per_l: float = 0.742  # Fuel density (E10 gasoline)

    # Weight distribution
    weight_dist_front: float = 0.47  # Static front weight fraction

    # Brake bias — calibrated from real IBT/LDX data per car.
    # iRacing BrakePressureBias = hydraulic front pressure split (%).
    # NOT dynamic weight transfer ratio. Rear MC is physically larger,
    # which handles dynamic compensation. This parameter stays near static
    # weight distribution with small forward correction for stability.
    brake_bias_pct: float = 46.0     # Default — calibrated below per car

    # Chassis geometry — needed for handling dynamics (understeer, slip angle)
    wheelbase_m: float = 2.740       # Wheelbase (m). BMW/Dallara = 2.740
    steering_ratio: float = 17.8     # Steering wheel to road wheel ratio
    # Calibrated from IBT: ratio * wheelbase = 48.65m at low-speed cornering
    # BMW 48.65 / 2.74 = 17.76 => 17.8:1

    # Aero map axis convention
    # True means aero map "front_rh" axis = actual REAR ride height
    aero_axes_swapped: bool = True

    # Valid ride height ranges (actual front/rear, in mm)
    min_front_rh_static: float = 30.0  # iRacing enforced floor for GTP
    max_front_rh_static: float = 80.0
    min_rear_rh_static: float = 30.0
    max_rear_rh_static: float = 80.0

    # Valid dynamic RH ranges (from aero map grid bounds, actual orientation)
    min_front_rh_dynamic: float = 5.0
    max_front_rh_dynamic: float = 50.0
    min_rear_rh_dynamic: float = 25.0
    max_rear_rh_dynamic: float = 75.0

    # Vortex burst threshold (mm) — front dynamic RH must stay above this
    vortex_burst_threshold_mm: float = 2.0

    # Suspension
    front_heave_spring_nmm: float = 50.0   # N/mm at spring
    rear_third_spring_nmm: float = 530.0   # N/mm at spring

    # Calibrated compression model
    aero_compression: AeroCompression = field(default_factory=lambda: AeroCompression(
        ref_speed_kph=230.0, front_compression_mm=15.0, rear_compression_mm=8.0
    ))

    # Pushrod geometry
    pushrod: PushrodGeometry = field(default_factory=lambda: PushrodGeometry(
        # Front RH is sim-pinned at 30mm for all GTP cars.
        # Rear: calibrated from 2-point fit across sessions:
        #   pushrod -29 → 49.3mm (avg), -16.5 → 48.1mm
        #   base=46.52, ratio=-0.096
        # Pushrod primarily sets damper/third preload, not static RH.
        front_pinned_rh_mm=30.0,
        front_pushrod_default_mm=-25.5,
        rear_base_rh_mm=46.52,
        rear_pushrod_to_rh=-0.096,
    ))

    # Ride height variance model
    rh_variance: RideHeightVariance = field(default_factory=lambda: RideHeightVariance(
        dominant_bump_freq_hz=5.0
    ))

    # Heave spring physics model
    heave_spring: HeaveSpringModel = field(default_factory=lambda: HeaveSpringModel(
        front_m_eff_kg=228.0, rear_m_eff_kg=2395.3
    ))

    # Corner spring physics model
    corner_spring: CornerSpringModel = field(default_factory=lambda: CornerSpringModel(
        front_torsion_c=0.0008036, front_torsion_od_ref_mm=13.9
    ))

    # ARB model
    arb: ARBModel = field(default_factory=lambda: ARBModel(
        front_size_labels=["Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[1200.0, 2400.0, 3600.0],
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],
    ))

    # Wheel geometry model
    geometry: WheelGeometryModel = field(default_factory=lambda: WheelGeometryModel())

    # Damper model
    damper: DamperModel = field(default_factory=lambda: DamperModel())

    # Multi-variable ride height prediction model
    ride_height_model: RideHeightModel = field(default_factory=lambda: RideHeightModel())

    # Available wing angles
    wing_angles: list[float] = field(default_factory=list)

    def total_mass(self, fuel_load_l: float) -> float:
        """Total car mass including driver and fuel (kg)."""
        return self.mass_car_kg + self.mass_driver_kg + fuel_load_l * self.fuel_density_kg_per_l

    def to_aero_coords(self, actual_front_rh: float, actual_rear_rh: float) -> tuple[float, float]:
        """Convert actual front/rear RH to aero map query coordinates.

        Returns (aero_front_rh, aero_rear_rh) for use with AeroSurface.query().
        """
        if self.aero_axes_swapped:
            return actual_rear_rh, actual_front_rh
        return actual_front_rh, actual_rear_rh

    def from_aero_coords(self, aero_front_rh: float, aero_rear_rh: float) -> tuple[float, float]:
        """Convert aero map coordinates back to actual front/rear RH.

        Returns (actual_front_rh, actual_rear_rh).
        """
        if self.aero_axes_swapped:
            return aero_rear_rh, aero_front_rh
        return aero_front_rh, aero_rear_rh

    def rh_excursion_p99(self, shock_vel_p99_mps: float) -> float:
        """Estimate p99 ride height excursion (mm) from shock velocity.

        Uses: excursion = shock_vel / (2 * pi * dominant_freq)
        Converts from m/s to mm.
        """
        import math
        freq = self.rh_variance.dominant_bump_freq_hz
        excursion_m = shock_vel_p99_mps / (2 * math.pi * freq)
        return excursion_m * 1000  # Convert to mm

    def estimate_confidence(self) -> dict[str, str]:
        """Return confidence level for key model parameters.

        BMW: all values calibrated from IBT telemetry — high confidence.
        Other cars: values marked ESTIMATE have lower confidence until
        calibrated from that car's own IBT sessions.

        Returns:
            Dict mapping parameter name → confidence level string.
        """
        if self.canonical_name == "bmw":
            return {
                "aero_compression": "calibrated",
                "m_eff_front": "calibrated",
                "m_eff_rear": "calibrated",
                "front_roll_gain": "calibrated",
                "rear_roll_gain": "calibrated",
                "pushrod_geometry": "calibrated",
            }

        # For all other cars, flag parameters that need IBT calibration
        flags: dict[str, str] = {}

        # Parameters that are confirmed same as BMW for Dallara LMDh platform
        dallara_confirmed = self.canonical_name in ("cadillac", "acura")

        flags["aero_compression"] = (
            "confirmed_dallara" if dallara_confirmed else "ESTIMATE"
        )
        flags["m_eff_front"] = "ESTIMATE — needs IBT calibration"
        flags["m_eff_rear"] = "ESTIMATE — needs IBT calibration"
        flags["front_roll_gain"] = "ESTIMATE — needs IBT calibration"
        flags["rear_roll_gain"] = "ESTIMATE — needs IBT calibration"
        flags["pushrod_geometry"] = (
            "confirmed_dallara" if dallara_confirmed else "ESTIMATE"
        )

        return flags


# ─── Car definitions ─────────────────────────────────────────────────────────

BMW_M_HYBRID_V8 = CarModel(
    name="BMW M Hybrid V8",
    canonical_name="bmw",
    mass_car_kg=1030.0,       # GTP minimum ~1030 kg dry
    mass_driver_kg=75.0,
    weight_dist_front=0.47,
    brake_bias_pct=46.0,      # Calibrated: IBT=46.0%, S1=46.5%, S2=46.0%
    aero_axes_swapped=True,
    min_front_rh_static=30.0,  # sim-enforced floor for all GTP
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,  # aero map "rear_rh" axis
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,  # aero map "front_rh" axis
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # minimum safe at Sebring
    rear_third_spring_nmm=530.0,
    aero_compression=AeroCompression(
        # Calibrated from iRacing AeroCalculator (NOT IBT sensor readings).
        # AeroCalc coordinates are what the aero maps are parameterized in.
        # Static front 30.0mm → AeroCalc dynamic 15mm → compression 15.0mm
        # Static rear 49.5mm → AeroCalc dynamic 40mm → compression 9.5mm
        # NOTE: Rear compression varies with setup (7.8mm with heave 60/third 450,
        # 9.5mm with heave 50/third 540). This is a known limitation of a
        # single-constant model. Using latest calibrated value.
        # Reference speed: iRacing's internal aero reference ~230 kph
        ref_speed_kph=230.0,
        front_compression_mm=15.0,
        rear_compression_mm=9.5,
    ),
    pushrod=PushrodGeometry(
        # Calibrated from 6 BMW Sebring sessions:
        # Front: sim-pinned at 30.0mm. Pushrod -23.0/-25.0/-25.5 all → 30.0mm.
        # Rear: weak pushrod effect. Best fit across sessions:
        #   pushrod -29 → 49.3mm (avg), -16.5 → 48.1mm
        #   2-point fit: base=46.52, ratio=-0.096
        # Pushrod primarily controls damper/third preload, not static RH.
        front_pinned_rh_mm=30.0,
        front_pushrod_default_mm=-25.5,
        rear_base_rh_mm=46.52,
        rear_pushrod_to_rh=-0.096,
    ),
    rh_variance=RideHeightVariance(
        # Sebring dominant bump frequency estimated at ~5 Hz
        # from shock velocity spectrum (p50 ~25 mm/s, significant energy in 3-10 Hz)
        dominant_bump_freq_hz=5.0,
    ),
    heave_spring=HeaveSpringModel(
        # Calibrated from BMW Sebring telemetry (2 sessions):
        # Session 1 (17-38-43): v_p99_f=0.2597, exc=15.0mm, k=50 → m_eff=166.8
        # Session 2 (19-21-44): v_p99_f=0.2537, exc=17.1mm, k=50 → m_eff=228.0
        # Using Session 2 (more recent, longer stint, better conditioned):
        #   front m_eff = 50000 * (17.1 / 253.7)^2 = 228.0 kg
        # Rear Session 2: v_p99=0.3245, exc=21.6mm, k=540
        #   rear m_eff = 540000 * (21.6 / 324.5)^2 = 2395.3 kg
        front_m_eff_kg=228.0,
        rear_m_eff_kg=2395.3,
        front_spring_range_nmm=(20.0, 200.0),
        rear_spring_range_nmm=(100.0, 1000.0),
        sigma_target_mm=10.0,   # SKILL.md: sigma > 5mm at >200 kph = unstable
        perch_offset_front_baseline_mm=-13.0,
        perch_offset_rear_baseline_mm=42.0,  # Verified from 2026-03-11 session
        front_heave_hard_range_nmm=(30.0, 50.0),
        front_heave_hard_range_exempt_tracks=["daytona", "le_mans"],
    ),
    corner_spring=CornerSpringModel(
        # Front torsion bar: OD 13.9mm -> ~30 N/mm wheel rate
        # Calibrated: k_wheel = C * OD^4, C = 30.0 / 13.9^4 = 0.0008036
        # The C constant already includes the suspension motion ratio,
        # so front_motion_ratio = 1.0 (formula already gives wheel rate).
        front_torsion_c=0.0008036,
        front_torsion_od_ref_mm=13.9,
        front_torsion_od_range_mm=(13.90, 18.20),
        # Discrete OD options from iRacing garage (14 values, 13.90-18.20mm)
        front_torsion_od_options=[
            13.90, 14.34, 14.76, 15.14, 15.51, 15.86,
            16.19, 16.51, 16.81, 17.11, 17.39, 17.67, 17.94, 18.20,
        ],
        # Rear coil spring: iRacing reports spring rate at the damper (170 N/mm).
        # To get wheel rate: k_wheel = k_spring * MR^2 = 170 * 0.36 = 61.2 N/mm
        # Third/corner ratio: 530/170 = 3.1x (within 1.5-3.5x guideline)
        rear_spring_range_nmm=(100.0, 300.0),
        rear_spring_step_nmm=10.0,
        rear_spring_perch_baseline_mm=30.0,
        # Motion ratios (spring-to-wheel conversion for roll stiffness)
        # Front: torsion bar C*OD^4 already gives wheel rate
        front_motion_ratio=1.0,
        # Rear: derived from measured LLTD (49.8%) and body roll (1.67 deg at 2g).
        # MR=0.60 gives K_springs_rear=1454 N*m/deg. Combined with FARB Soft/1
        # (1650) and RARB Soft/3 (975), total K=4867 → roll=1.64° at 2g. The
        # MR=0.60 is consistent with a highly leveraged GTP pushrod suspension.
        rear_motion_ratio=0.60,
        track_width_mm=1600.0,
        cg_height_mm=350.0,
    ),
    arb=ARBModel(
        # BMW uses descriptive labels (Soft/Medium/Stiff), not numeric.
        #
        # ARB stiffness derived from measured LLTD and body roll:
        #   - Measured LLTD from IBT ride height deflection: 49.8% (at Soft F/1, Soft R/3)
        #   - Measured body roll at p95 lateral G (2.02g): 1.67 deg
        #   - K_springs_front (30 N/mm @ 1730mm) = 784 N*m/deg (MR_front=1.0)
        #   - K_springs_rear (170 N/mm * 0.60^2 @ 1650mm) = 1454 N*m/deg (MR_rear=0.60)
        #   - For LLTD = 50%: K_front_total = K_rear_total
        #     784 + FARB*0.30 = 1454 + RARB*0.65
        #     With FARB_soft=5500: K_FARB=1650, K_front=2434
        #     RARB_soft = (2434 - 1454) / 0.65 = 1508 ≈ 1500
        #   - Total K_roll = 4868, roll@2g = 1.64° (measured 1.67°) ✓
        #
        # FARB stays large relative to RARB because the front springs are soft
        # (30 N/mm wheel rate) — the front ARB is the primary roll stiffness
        # contributor at the front axle. This is typical for GTP cars.
        front_size_labels=["Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[5500.0, 11000.0, 16500.0],
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],
        front_blade_count=5,
        front_baseline_size="Soft",
        front_baseline_blade=1,
        rear_blade_count=5,
        rear_baseline_size="Medium",
        rear_baseline_blade=3,
        track_width_front_mm=1730.0,
        track_width_rear_mm=1650.0,
    ),
    geometry=WheelGeometryModel(
        # Verified BMW Sebring baseline from per-car-quirks.md
        # Calibrated from real BMW Sebring setups (S1: -2.8/-1.9, S2: -2.9/-1.8)
        front_camber_baseline_deg=-2.9,
        rear_camber_baseline_deg=-1.8,
        front_toe_baseline_mm=-0.4,     # slight toe-out (S1: -0.5, S2: -0.4)
        rear_toe_baseline_mm=0.0,
        front_roll_gain=0.62,           # deg camber recovery per deg body roll
        rear_roll_gain=0.50,
        front_toe_heating_coeff=2.5,
        rear_toe_heating_coeff=1.8,
    ),
    damper=DamperModel(
        # BMW damper scale — all clicks max at 11. Different from Ferrari.
        # Do NOT transfer values between cars.
        # Verified from real LDX: HS slope 11, HS Rbd rear 11 (at max).
        ls_comp_range=(1, 11),
        ls_rbd_range=(1, 11),
        hs_comp_range=(1, 11),
        hs_rbd_range=(1, 11),
        hs_slope_range=(1, 11),
        ls_force_per_click_n=18.0,  # calibrated: c*v/clicks matches real data
        hs_force_per_click_n=80.0,
        # Calibrated from real BMW Sebring Setup 2 (locked platform)
        front_ls_comp_baseline=7,
        front_ls_rbd_baseline=6,
        front_hs_comp_baseline=5,
        front_hs_rbd_baseline=8,
        front_hs_slope_baseline=10,
        rear_ls_comp_baseline=6,
        rear_ls_rbd_baseline=7,
        rear_hs_comp_baseline=3,
        rear_hs_rbd_baseline=9,
        rear_hs_slope_baseline=10,
    ),
    ride_height_model=RideHeightModel(
        # Calibrated from 13 BMW Sebring sessions (March 2026).
        # Front model (2 features): LOO-CV RMSE = 0.066 mm
        #   front_static_rh = 30.1458 + 0.001614*heave_nmm + 0.074486*camber_deg
        front_intercept=30.1458,
        front_coeff_heave_nmm=0.001614,
        front_coeff_camber_deg=0.074486,
        front_loo_rmse_mm=0.066,
        # Rear model (4 features): R² = 0.9655, LOO-CV RMSE = 0.845 mm
        rear_intercept=48.1799,
        rear_coeff_pushrod=0.399463,
        rear_coeff_third_nmm=0.008965,
        rear_coeff_rear_spring=0.065140,
        rear_coeff_heave_perch=0.255291,
        rear_r_squared=0.9655,
        rear_loo_rmse_mm=0.845,
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
)


# ─── Cadillac V-Series.R ─────────────────────────────────────────────────────
# Dallara LMDh chassis (shared platform with BMW, Acura)
# Naturally aspirated 5.5L V8. Best all-rounder.
# Parameter structure matches BMW exactly (same Dallara platform).
# No verified telemetry calibration — values marked ESTIMATE.

CADILLAC_VSERIES_R = CarModel(
    name="Cadillac V-Series.R",
    canonical_name="cadillac",
    mass_car_kg=1030.0,           # GTP minimum — confirmed same as BMW
    mass_driver_kg=75.0,
    weight_dist_front=0.47,       # ESTIMATE — needs Cadillac IBT calibration
    aero_axes_swapped=True,       # Dallara aero map convention — confirmed same as BMW
    min_front_rh_static=30.0,     # iRacing floor for all GTP cars — confirmed
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # ESTIMATE — needs Cadillac IBT calibration
    rear_third_spring_nmm=530.0,  # ESTIMATE — needs Cadillac IBT calibration
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=15.0,  # Dallara platform — same as BMW verified
        rear_compression_mm=8.0,    # ESTIMATE — Cadillac aero package differs
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,        # iRacing GTP floor — confirmed all cars
        front_pushrod_default_mm=-25.5, # Dallara LMDh geometry — same as BMW
        rear_base_rh_mm=46.7,           # Dallara geometry — same as BMW
        rear_pushrod_to_rh=-0.096,      # Dallara geometry — same as BMW verified
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=176.0,   # ESTIMATE — needs Cadillac IBT calibration
        rear_m_eff_kg=2870.0,   # ESTIMATE — needs Cadillac IBT calibration
        front_spring_range_nmm=(20.0, 200.0),
        rear_spring_range_nmm=(100.0, 1000.0),
    ),
    corner_spring=CornerSpringModel(
        # Cadillac uses same Dallara torsion bar front + coil rear
        front_torsion_c=0.0008036,      # Dallara platform — same as BMW verified
        front_torsion_od_ref_mm=13.9,
        front_torsion_od_range_mm=(11.0, 16.0),
        rear_spring_range_nmm=(100.0, 300.0),
        rear_spring_step_nmm=10.0,
        front_motion_ratio=1.0,
        rear_motion_ratio=0.60,         # Dallara geometry — same as BMW confirmed
        track_width_mm=1600.0,          # ESTIMATE — needs Cadillac IBT calibration
        cg_height_mm=350.0,             # ESTIMATE — needs Cadillac IBT calibration
    ),
    arb=ARBModel(
        # Same Soft/Medium/Stiff labels as BMW (Dallara)
        front_size_labels=["Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[5500.0, 11000.0, 16500.0],  # ESTIMATE — needs Cadillac calibration
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],     # ESTIMATE — needs Cadillac calibration
        front_blade_count=5,
        rear_blade_count=5,
        track_width_front_mm=1730.0,  # ESTIMATE — needs Cadillac IBT calibration
        track_width_rear_mm=1650.0,   # ESTIMATE — needs Cadillac IBT calibration
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.9,  # ESTIMATE — needs Cadillac IBT calibration
        rear_camber_baseline_deg=-1.8,
        front_roll_gain=0.62,            # ESTIMATE — needs Cadillac IBT calibration
        rear_roll_gain=0.50,
    ),
    damper=DamperModel(
        # Same Dallara damper scale as BMW — all clicks max at 11
        ls_comp_range=(1, 11),
        ls_rbd_range=(1, 11),
        hs_comp_range=(1, 11),
        hs_rbd_range=(1, 11),
        hs_slope_range=(1, 11),
        ls_force_per_click_n=18.0,
        hs_force_per_click_n=80.0,
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
)


# ─── Ferrari 499P ────────────────────────────────────────────────────────────
# Bespoke LMH chassis. 3.0L twin-turbo V6 + 200 kW front hybrid.
# VERY different parameter structure from Dallara:
# - Rear uses torsion bars (indexed OD, not mm)
# - ARBs use letter indices (A, B, C)
# - Damper clicks are on a DIFFERENT scale (6-40 range vs BMW 1-20)
# - Has BOTH front and rear diffs
# Has verified Sebring S1 setup for partial calibration.

FERRARI_499P = CarModel(
    name="Ferrari 499P",
    canonical_name="ferrari",
    mass_car_kg=1030.0,           # GTP minimum — confirmed same as LMDh
    mass_driver_kg=75.0,
    weight_dist_front=0.47,       # ESTIMATE — LMH may differ from LMDh
    aero_axes_swapped=True,       # Ferrari aero map uses same axis convention as Dallara
                                  # (per-car-quirks.md doesn't document a difference;
                                  #  if telemetry reveals axes are swapped, update here)
    min_front_rh_static=30.0,
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # ESTIMATE — indexed in reality (needs IBT calibration)
    rear_third_spring_nmm=530.0,  # ESTIMATE — indexed in reality (needs IBT calibration)
    aero_compression=AeroCompression(
        # From verified S1: static front 30.5mm, rear 48.3mm
        # Dynamic ride heights need telemetry to calibrate
        ref_speed_kph=230.0,
        front_compression_mm=15.5,  # ESTIMATE from S1 static
        rear_compression_mm=8.3,    # ESTIMATE
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,        # iRacing GTP floor — same for LMH
        front_pushrod_default_mm=-25.5, # ESTIMATE — LMH may differ from Dallara
        rear_base_rh_mm=46.7,           # ESTIMATE — needs Ferrari IBT calibration
        rear_pushrod_to_rh=-0.09,       # ESTIMATE — needs Ferrari IBT calibration
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=176.0,   # ESTIMATE — needs telemetry calibration
        rear_m_eff_kg=2870.0,   # ESTIMATE
    ),
    corner_spring=CornerSpringModel(
        # Ferrari uses torsion bars for BOTH front and rear
        # The C constant and OD range are different from BMW
        # S1: front TorsionBarOD = 3 (indexed), rear = 8 (indexed)
        # Until we decode the index-to-mm mapping, use BMW-like values
        front_torsion_c=0.0008036,  # ESTIMATE — needs Ferrari-specific calibration
        front_torsion_od_ref_mm=13.9,  # ESTIMATE
        front_torsion_od_range_mm=(11.0, 16.0),  # ESTIMATE
        # WARNING: Ferrari rear is actually a torsion bar (indexed values in iRacing),
        # but we model it as a coil spring range until the index→OD mapping is decoded.
        # Corner spring and LLTD outputs for Ferrari are UNRELIABLE until calibrated.
        rear_spring_range_nmm=(100.0, 300.0),  # PLACEHOLDER — needs torsion bar calibration
        rear_spring_step_nmm=10.0,
        front_motion_ratio=1.0,
        rear_motion_ratio=0.65,  # ESTIMATE — bespoke suspension (needs Ferrari IBT calibration)
        track_width_mm=1600.0,   # ESTIMATE — needs Ferrari IBT calibration
        cg_height_mm=340.0,      # ESTIMATE — LMH rules allow lower CoG than LMDh
    ),
    arb=ARBModel(
        # Ferrari uses letter indices: A, B, C
        front_size_labels=["A", "B", "C"],
        front_stiffness_nmm_deg=[5000.0, 10000.0, 15000.0],  # ESTIMATE
        rear_size_labels=["A", "B", "C"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],     # ESTIMATE
        front_blade_count=5,
        front_baseline_size="A",
        front_baseline_blade=1,
        rear_blade_count=5,
        rear_baseline_size="B",
        rear_baseline_blade=2,
        track_width_front_mm=1730.0,  # ESTIMATE
        track_width_rear_mm=1650.0,   # ESTIMATE
    ),
    geometry=WheelGeometryModel(
        # From verified S1: front camber -2.9°, rear -1.8°
        front_camber_baseline_deg=-2.9,
        rear_camber_baseline_deg=-1.8,
        front_toe_baseline_mm=-2.0,   # Ferrari S1: -2.0mm (aggressive toe-out)
        rear_toe_baseline_mm=0.0,
        front_roll_gain=0.60,         # ESTIMATE
        rear_roll_gain=0.48,          # ESTIMATE
        front_toe_heating_coeff=2.5,
        rear_toe_heating_coeff=1.8,
    ),
    damper=DamperModel(
        # Ferrari damper click scale is DIFFERENT from BMW (6-40 range)
        ls_comp_range=(1, 50),
        ls_rbd_range=(1, 50),
        hs_comp_range=(1, 50),
        hs_rbd_range=(1, 50),
        hs_slope_range=(1, 20),
        # Force-per-click needs calibration from Ferrari telemetry
        ls_force_per_click_n=7.0,   # ESTIMATE — smaller per click (more clicks)
        hs_force_per_click_n=30.0,  # ESTIMATE
        # From verified S1
        front_ls_comp_baseline=15,
        front_ls_rbd_baseline=25,
        front_hs_comp_baseline=15,
        front_hs_rbd_baseline=6,
        front_hs_slope_baseline=8,
        rear_ls_comp_baseline=18,
        rear_ls_rbd_baseline=10,
        rear_hs_comp_baseline=40,
        rear_hs_rbd_baseline=40,
        rear_hs_slope_baseline=11,
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
)


# ─── Porsche 963 ─────────────────────────────────────────────────────────────
# Multimatic LMDh chassis (NOT Dallara). DSSV dampers (spool valve, not shims).
# Aero-dominant car. Highest top speed. Best traction.
# Same parameter naming as BMW/Cadillac but different platform response.

PORSCHE_963 = CarModel(
    name="Porsche 963",
    canonical_name="porsche",
    mass_car_kg=1030.0,
    mass_driver_kg=75.0,
    weight_dist_front=0.47,  # ESTIMATE
    aero_axes_swapped=True,
    min_front_rh_static=30.0,
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # ESTIMATE
    rear_third_spring_nmm=530.0,  # ESTIMATE
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=15.0,  # ESTIMATE
        rear_compression_mm=8.0,    # ESTIMATE
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,       # ESTIMATE — Multimatic platform
        front_pushrod_default_mm=-25.5, # ESTIMATE
        rear_base_rh_mm=46.7,          # ESTIMATE
        rear_pushrod_to_rh=-0.09,      # ESTIMATE
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=176.0,   # ESTIMATE — Multimatic chassis may differ
        rear_m_eff_kg=2870.0,   # ESTIMATE
    ),
    corner_spring=CornerSpringModel(
        # Porsche uses torsion bar front + coil rear (like Dallara)
        front_torsion_c=0.0008036,  # ESTIMATE — Multimatic geometry differs
        front_torsion_od_ref_mm=13.9,
        front_torsion_od_range_mm=(11.0, 16.0),
        rear_spring_range_nmm=(100.0, 300.0),
        rear_spring_step_nmm=10.0,
        front_motion_ratio=1.0,
        rear_motion_ratio=0.60,  # ESTIMATE
        track_width_mm=1600.0,   # ESTIMATE
        cg_height_mm=345.0,      # ESTIMATE
    ),
    arb=ARBModel(
        # Soft/Medium/Stiff labels like BMW (LMDh standard naming)
        front_size_labels=["Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[5500.0, 11000.0, 16500.0],  # ESTIMATE
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],     # ESTIMATE
        front_blade_count=5,
        rear_blade_count=5,
        track_width_front_mm=1730.0,  # ESTIMATE
        track_width_rear_mm=1650.0,   # ESTIMATE
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.9,  # ESTIMATE
        rear_camber_baseline_deg=-1.8,
        front_roll_gain=0.62,            # ESTIMATE
        rear_roll_gain=0.50,
        # Porsche is gentle on tyres — can run more aggressive geometry
    ),
    damper=DamperModel(
        # DSSV spool-valve dampers — more progressive response than shim stacks.
        # Same click range as BMW but different force characteristics.
        # DSSV: only 4% force degradation over temperature range (vs 14-16% shim).
        ls_force_per_click_n=18.0,  # ESTIMATE — needs DSSV calibration
        hs_force_per_click_n=80.0,  # ESTIMATE
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
)


# ─── Acura ARX-06 ────────────────────────────────────────────────────────────
# Dallara LMDh chassis (same as BMW, Cadillac). 2.4L twin-turbo V6.
# Sharpest front end in class. Diff preload IS THE setup parameter.
# Narrow wing range (6-10°). Best at technical tracks.

ACURA_ARX06 = CarModel(
    name="Acura ARX-06",
    canonical_name="acura",
    mass_car_kg=1030.0,
    mass_driver_kg=75.0,
    weight_dist_front=0.47,  # ESTIMATE — Dallara LMDh
    aero_axes_swapped=True,
    min_front_rh_static=30.0,
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # ESTIMATE
    rear_third_spring_nmm=530.0,  # ESTIMATE
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=15.0,  # ESTIMATE
        rear_compression_mm=8.0,    # ESTIMATE
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,       # ESTIMATE — same Dallara platform
        front_pushrod_default_mm=-25.5, # ESTIMATE
        rear_base_rh_mm=46.7,          # ESTIMATE
        rear_pushrod_to_rh=-0.09,      # ESTIMATE
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=176.0,   # ESTIMATE
        rear_m_eff_kg=2870.0,   # ESTIMATE
    ),
    corner_spring=CornerSpringModel(
        # Same Dallara torsion bar front + coil rear
        front_torsion_c=0.0008036,  # ESTIMATE
        front_torsion_od_ref_mm=13.9,
        front_torsion_od_range_mm=(11.0, 16.0),
        rear_spring_range_nmm=(100.0, 300.0),
        rear_spring_step_nmm=10.0,
        front_motion_ratio=1.0,
        rear_motion_ratio=0.60,  # ESTIMATE
        track_width_mm=1600.0,
        cg_height_mm=350.0,
    ),
    arb=ARBModel(
        # Same Soft/Medium/Stiff as BMW/Cadillac (Dallara)
        front_size_labels=["Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[5500.0, 11000.0, 16500.0],  # ESTIMATE
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],     # ESTIMATE
        front_blade_count=5,
        rear_blade_count=5,
        track_width_front_mm=1730.0,
        track_width_rear_mm=1650.0,
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.9,  # ESTIMATE
        rear_camber_baseline_deg=-1.8,
        front_roll_gain=0.62,            # ESTIMATE
        rear_roll_gain=0.50,
    ),
    damper=DamperModel(
        # Same Dallara damper scale as BMW/Cadillac — all clicks max at 11
        ls_comp_range=(1, 11),
        ls_rbd_range=(1, 11),
        hs_comp_range=(1, 11),
        hs_rbd_range=(1, 11),
        hs_slope_range=(1, 11),
        ls_force_per_click_n=18.0,
        hs_force_per_click_n=80.0,
    ),
    # Acura has narrower, lower wing range than other GTP cars
    wing_angles=[6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0],
)


# ─── Registry ────────────────────────────────────────────────────────────────

_CARS = {
    "bmw": BMW_M_HYBRID_V8,
    "cadillac": CADILLAC_VSERIES_R,
    "ferrari": FERRARI_499P,
    "porsche": PORSCHE_963,
    "acura": ACURA_ARX06,
}


def get_car(name: str) -> CarModel:
    """Get car model by canonical name."""
    key = name.lower().strip()
    if key not in _CARS:
        available = ", ".join(_CARS.keys())
        raise KeyError(f"Unknown car '{name}'. Available: {available}")
    return _CARS[key]
