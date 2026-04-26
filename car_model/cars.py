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

from car_model.garage import GarageOutputModel
from vertical_dynamics import damped_excursion_mm


# ─── Ferrari indexed-control lookup tables ────────────────────────────────────

@dataclass
class IndexedLookupPoint:
    """A single calibration point mapping a garage index to a physical value.

    Attributes:
        index:          Integer garage index (0-based).
        physical_value: Physical value at this index (e.g. N/mm for springs).
        unit:           Unit string for display (e.g. "N/mm", "mm").
        confidence:     Data confidence tier:
                          "validated"  — confirmed from IBT or garage screenshot
                          "estimated"  — derived from analytic fit (e.g. k^(1/4))
                          "derived"    — extrapolated beyond calibration range
        source:         Free-text description of how this point was obtained.
    """
    index: int
    physical_value: float
    unit: str
    confidence: str       # "validated" | "estimated" | "derived"
    source: str


@dataclass
class FerrariIndexedControlModel:
    """Typed indexed-control lookup tables for the Ferrari 499P.

    Ferrari 499P exposes heave springs (front 0–8, rear 0–9), front torsion
    bar OD (0–18), and rear torsion bar OD (0–18) as integer garage indices
    instead of physical N/mm or mm values.

    This model provides the authoritative physical-value ↔ index conversion
    for each of those four controls.  Linear interpolation is applied between
    calibrated anchor points.

    Calibration sources:
      front_heave  — IBT session anchor: idx 1 → 50 N/mm; slope 20 N/mm/idx
      rear_heave   — IBT session anchor: idx 2 → 530 N/mm; slope 60 N/mm/idx
      front_torsion — 6-point garage screenshot sweep: fit k^(1/4) = 3.7829 + 0.04201×idx
      rear_torsion  — 4-point garage screenshot sweep: fit k^(1/4) = 4.3685 + 0.03108×idx
    """
    front_heave: list[IndexedLookupPoint]
    rear_heave: list[IndexedLookupPoint]
    front_torsion: list[IndexedLookupPoint]
    rear_torsion: list[IndexedLookupPoint]

    def front_heave_rate_from_index(self, index: float) -> float:
        """Decode front heave index → physical spring rate (N/mm)."""
        return self._interpolate(self.front_heave, index)

    def rear_heave_rate_from_index(self, index: float) -> float:
        """Decode rear heave index → physical spring rate (N/mm)."""
        return self._interpolate(self.rear_heave, index)

    def front_torsion_rate_from_index(self, index: float) -> float:
        """Decode front torsion index → physical wheel rate (N/mm)."""
        return self._interpolate(self.front_torsion, index)

    def rear_torsion_rate_from_index(self, index: float) -> float:
        """Decode rear torsion index → physical bar rate (N/mm)."""
        return self._interpolate(self.rear_torsion, index)

    def front_heave_index_from_rate(self, rate_nmm: float) -> float:
        """Encode physical front heave rate → nearest garage index."""
        return self._inverse(self.front_heave, rate_nmm)

    def rear_heave_index_from_rate(self, rate_nmm: float) -> float:
        """Encode physical rear heave rate → nearest garage index."""
        return self._inverse(self.rear_heave, rate_nmm)

    def front_torsion_index_from_rate(self, rate_nmm: float) -> float:
        """Encode physical front torsion wheel rate → nearest garage index."""
        return self._inverse(self.front_torsion, rate_nmm)

    def rear_torsion_index_from_rate(self, rate_nmm: float) -> float:
        """Encode physical rear torsion bar rate → nearest garage index."""
        return self._inverse(self.rear_torsion, rate_nmm)

    def _interpolate(self, points: list[IndexedLookupPoint], index: float) -> float:
        """Linear interpolation between calibrated anchor points."""
        if not points:
            return 0.0
        sorted_pts = sorted(points, key=lambda p: p.index)
        if index <= sorted_pts[0].index:
            return float(sorted_pts[0].physical_value)
        if index >= sorted_pts[-1].index:
            return float(sorted_pts[-1].physical_value)
        for i in range(len(sorted_pts) - 1):
            lo, hi = sorted_pts[i], sorted_pts[i + 1]
            if lo.index <= index <= hi.index:
                t = (index - lo.index) / (hi.index - lo.index)
                return float(lo.physical_value + t * (hi.physical_value - lo.physical_value))
        return float(sorted_pts[-1].physical_value)

    def _inverse(self, points: list[IndexedLookupPoint], value: float) -> float:
        """Find nearest garage index for a target physical value (clamp to range)."""
        if not points:
            return 0.0
        sorted_pts = sorted(points, key=lambda p: p.index)
        # Clamp to extremes if outside calibrated range
        if value <= sorted_pts[0].physical_value:
            return float(sorted_pts[0].index)
        if value >= sorted_pts[-1].physical_value:
            return float(sorted_pts[-1].index)
        # Nearest-physical-value search
        best = sorted_pts[0]
        best_dist = abs(best.physical_value - value)
        for pt in sorted_pts[1:]:
            d = abs(pt.physical_value - value)
            if d < best_dist:
                best_dist = d
                best = pt
        return float(best.index)


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
    is_calibrated: bool = False      # True when derived from real IBT data

    def front_at_speed(self, speed_kph: float) -> float:
        """Front aero compression (mm) at a given speed."""
        return self.front_compression_mm * (speed_kph / self.ref_speed_kph) ** 2

    def rear_at_speed(self, speed_kph: float) -> float:
        """Rear aero compression (mm) at a given speed."""
        return self.rear_compression_mm * (speed_kph / self.ref_speed_kph) ** 2


@dataclass
class PushrodGeometry:
    """Pushrod offset to ride height relationship.

    The front pushrod affects static ride height — the relationship is
    car-specific and must be calibrated from garage data.

    BMW (62 sessions, pushrod range -28 to -22.5mm):
      Front RH = 30.0 ± 0.4mm across the entire measured range.
      In this range, the car sits at iRacing's 30mm GTP floor regardless
      of pushrod. front_pushrod_to_rh = 0.0 (no measurable sensitivity
      in the tested range; slope may exist beyond it — not yet characterized).

    Cadillac (2 garage data points, ESTIMATE — needs more calibration):
      pushrod=-25.0mm → RH=41.8mm (default reference)
      pushrod=-33.5mm → RH=30.0mm (in-session target)
      front_pushrod_to_rh = 1.388 mm/mm (positive: less negative → higher RH)
      front_base_rh_mm  = 41.8mm (RH at front_pushrod_default_mm = -25.0mm)

    Rear relationship (all cars):
        static_rh = rear_base_rh + pushrod_offset * rear_pushrod_to_rh
    The rear ratio is weak (~-0.096 mm/mm); primary rear RH control is the
    spring perch.
    """
    front_pinned_rh_mm: float        # Target front static RH (iRacing min = 30mm for all GTP)
    front_pushrod_default_mm: float  # Default/reference pushrod offset
    rear_base_rh_mm: float           # Rear RH with pushrod at 0 offset
    rear_pushrod_to_rh: float        # mm RH change per mm pushrod (weak, ~-0.096)

    # Front pushrod-to-RH sensitivity.
    # 0.0 = no measured sensitivity (BMW in tested range; may change beyond -28mm).
    # Cadillac = 1.388 mm/mm (3 garage data points — ESTIMATE).
    front_pushrod_to_rh: float = 0.0
    # Front static RH at (front_pushrod_default_mm, front_heave_perch_ref_mm).
    front_base_rh_mm: float = 30.0

    # Front heave-perch-to-RH sensitivity.
    # 0.0 = no term (BMW; not yet measured).
    # Cadillac = -1.51 mm/mm (more positive perch = lower front RH; 3 points — ESTIMATE).
    front_heave_perch_to_rh: float = 0.0
    # Heave perch reference at which front_base_rh_mm was measured.
    front_heave_perch_ref_mm: float = 0.0

    # Calibration flag: True when pushrod coefficients come from measured
    # garage data (screenshots or IBT analysis), False when estimated/default.
    is_calibrated: bool = False

    def front_offset_for_rh(self, target_rh: float,
                             heave_perch_mm: float | None = None) -> float:
        """Front pushrod offset to achieve target static RH at the given heave perch.

        For BMW (front_pushrod_to_rh=0): returns default (no measured sensitivity).
        For Cadillac: solves for pushrod accounting for both pushrod and perch terms.
        """
        if abs(self.front_pushrod_to_rh) < 1e-6:
            return self.front_pushrod_default_mm
        # Model: rh = base + pushrod_coeff*(pushrod - pushrod_ref) + perch_coeff*(perch - perch_ref)
        # → pushrod = (target - base - perch_coeff*(perch - perch_ref)) / pushrod_coeff + pushrod_ref
        perch = heave_perch_mm if heave_perch_mm is not None else self.front_heave_perch_ref_mm
        perch_adjustment = self.front_heave_perch_to_rh * (perch - self.front_heave_perch_ref_mm)
        return ((target_rh - self.front_base_rh_mm - perch_adjustment)
                / self.front_pushrod_to_rh + self.front_pushrod_default_mm)

    # Default rear pushrod when sensitivity is zero (set per-car in pushrod definition)
    rear_pushrod_default_mm: float = -29.0

    def rear_offset_for_rh(self, target_rh: float) -> float:
        """Pushrod offset needed to achieve target rear static RH."""
        if abs(self.rear_pushrod_to_rh) < 1e-6:
            return self.rear_pushrod_default_mm
        return (target_rh - self.rear_base_rh_mm) / self.rear_pushrod_to_rh

    def front_rh_for_offset(self, offset: float,
                             heave_perch_mm: float | None = None) -> float:
        """Front static RH resulting from a given pushrod offset (and optional heave perch)."""
        if abs(self.front_pushrod_to_rh) < 1e-6:
            return self.front_pinned_rh_mm  # No measured sensitivity: return target RH
        perch = heave_perch_mm if heave_perch_mm is not None else self.front_heave_perch_ref_mm
        perch_term = self.front_heave_perch_to_rh * (perch - self.front_heave_perch_ref_mm)
        return (self.front_base_rh_mm
                + (offset - self.front_pushrod_default_mm) * self.front_pushrod_to_rh
                + perch_term)

    def rear_rh_for_offset(self, offset: float) -> float:
        """Rear static RH resulting from a given pushrod offset."""
        return self.rear_base_rh_mm + offset * self.rear_pushrod_to_rh


@dataclass
class RideHeightModel:
    """Multi-variable static RH prediction from calibration regression.

    Rear: rear_static_rh = intercept + Σ(coeff_i * param_i)
    Front: front_static_rh = front_intercept + Σ(coeff_i * param_i)

    Calibrated from 31 unique BMW Sebring configs (41 sessions, March 2026).

    Front model (6 features): R²=0.15, RMSE=0.16mm — front RH nearly constant (~30mm)
    Rear model (6 features): R²=0.52, RMSE=0.68mm, MaxErr=2.1mm

    NOTE: This model is BMW-specific. Non-BMW cars should use uncalibrated().
    """
    is_calibrated: bool = False  # Must be set True explicitly after fitting; defaults False so careless instantiation does not appear calibrated

    # --- Front static RH regression ---
    front_intercept: float = 30.0   # fallback: acts as pinned value when coeffs are 0
    front_coeff_heave_nmm: float = 0.0     # mm RH per N/mm heave spring rate (linear)
    front_coeff_inv_heave: float = 0.0    # mm RH per (1/heave_nmm) — compliance model
    front_coeff_camber_deg: float = 0.0    # mm RH per deg front camber
    front_coeff_pushrod: float = 0.0       # mm RH per mm front pushrod offset
    front_coeff_perch: float = 0.0         # mm RH per mm front heave perch offset
    front_coeff_torsion_od: float = 0.0   # mm RH per mm front torsion bar OD (Ferrari)
    front_pushrod_ref_mm: float = 0.0      # pushrod value at which intercept was measured
    front_perch_ref_mm: float = 0.0        # perch value at which intercept was measured
    front_loo_rmse_mm: float = 0.0

    # --- Rear static RH regression ---
    rear_intercept: float = 0.0
    rear_coeff_pushrod: float = 0.0        # mm RH per mm pushrod offset
    rear_coeff_third_nmm: float = 0.0      # mm RH per N/mm third spring rate (linear)
    rear_coeff_inv_third: float = 0.0     # mm RH per (1/third) — compliance model
    rear_coeff_inv_spring: float = 0.0    # mm RH per (1/rear_spring) — compliance model
    rear_coeff_rear_spring: float = 0.0    # mm RH per N/mm rear spring rate
    rear_coeff_heave_perch: float = 0.0    # mm RH per mm front heave perch offset
    rear_coeff_fuel_l: float = 0.0         # mm RH per L fuel
    rear_coeff_spring_perch: float = 0.0   # mm RH per mm rear spring perch offset
    rear_r_squared: float = 0.0
    rear_loo_rmse_mm: float = 0.0

    @property
    def front_is_calibrated(self) -> bool:
        """Whether the front RH model has non-trivial calibration (coefficients non-zero)."""
        return self.is_calibrated and (
            abs(self.front_coeff_heave_nmm) + abs(self.front_coeff_camber_deg)
            + abs(self.front_coeff_inv_heave) + abs(self.front_coeff_pushrod)
            + abs(self.front_coeff_torsion_od)
        ) > 1e-9

    def predict_front_static_rh(
        self, heave_nmm: float, camber_deg: float,
        pushrod_mm: float | None = None, perch_mm: float | None = None,
    ) -> float:
        """Predict front static RH from setup parameters.

        Supports both linear heave (BMW) and compliance heave (Porsche,
        physics-correct: spring compression ∝ 1/k under aero load).
        """
        rh = (self.front_intercept
              + self.front_coeff_heave_nmm * heave_nmm
              + self.front_coeff_camber_deg * camber_deg)
        if abs(self.front_coeff_inv_heave) > 1e-9 and heave_nmm > 0:
            rh += self.front_coeff_inv_heave / heave_nmm
        if pushrod_mm is not None and abs(self.front_coeff_pushrod) > 1e-9:
            rh += self.front_coeff_pushrod * pushrod_mm
        if perch_mm is not None and abs(self.front_coeff_perch) > 1e-9:
            rh += self.front_coeff_perch * perch_mm
        return rh

    def predict_rear_static_rh(
        self, pushrod_mm: float, third_nmm: float,
        rear_spring_nmm: float, heave_perch_mm: float,
        fuel_l: float = 0.0, spring_perch_mm: float = 0.0,
    ) -> float:
        """Predict rear static RH from setup parameters.

        Supports both linear spring terms (BMW) and compliance terms
        (Porsche, physics-correct: compression ∝ 1/k under aero load).
        """
        inv_third = 1.0 / max(third_nmm, 1.0) if abs(self.rear_coeff_inv_third) > 1e-9 else 0.0
        inv_spring = 1.0 / max(rear_spring_nmm, 1.0) if abs(self.rear_coeff_inv_spring) > 1e-9 else 0.0
        return (self.rear_intercept
                + self.rear_coeff_pushrod * pushrod_mm
                + self.rear_coeff_third_nmm * third_nmm
                + self.rear_coeff_inv_third * inv_third
                + self.rear_coeff_rear_spring * rear_spring_nmm
                + self.rear_coeff_inv_spring * inv_spring
                + self.rear_coeff_heave_perch * heave_perch_mm
                + self.rear_coeff_fuel_l * fuel_l
                + self.rear_coeff_spring_perch * spring_perch_mm)

    def pushrod_for_target_rh(
        self, target_rh_mm: float, third_nmm: float,
        rear_spring_nmm: float, heave_perch_mm: float,
        fuel_l: float = 0.0, spring_perch_mm: float = 0.0,
    ) -> float:
        """Solve for the pushrod offset that achieves a target rear static RH.

        Raises ValueError if the rear pushrod has no calibrated effect on
        rear ride height — this is a calibration gap, not a fallback.
        Callers should detect this and surface it as a calibration block.
        """
        if abs(self.rear_coeff_pushrod) < 1e-6:
            raise ValueError(
                "Cannot solve pushrod_for_target_rh: rear_coeff_pushrod is zero. "
                "The car's RideHeightModel does not include rear_pushrod as a "
                "feature. Calibrate via auto_calibrate with garage screenshots "
                "that vary the rear pushrod offset."
            )
        inv_third = 1.0 / max(third_nmm, 1.0) if abs(self.rear_coeff_inv_third) > 1e-9 else 0.0
        inv_spring = 1.0 / max(rear_spring_nmm, 1.0) if abs(self.rear_coeff_inv_spring) > 1e-9 else 0.0
        other = (self.rear_intercept
                 + self.rear_coeff_third_nmm * third_nmm
                 + self.rear_coeff_inv_third * inv_third
                 + self.rear_coeff_rear_spring * rear_spring_nmm
                 + self.rear_coeff_inv_spring * inv_spring
                 + self.rear_coeff_heave_perch * heave_perch_mm
                 + self.rear_coeff_fuel_l * fuel_l
                 + self.rear_coeff_spring_perch * spring_perch_mm)
        return (target_rh_mm - other) / self.rear_coeff_pushrod

    @classmethod
    def uncalibrated(cls) -> RideHeightModel:
        """Return an uncalibrated RideHeightModel with all coefficients zeroed.

        Use this for non-BMW cars where the BMW-specific regression coefficients
        would produce incorrect results.
        """
        return cls(
            is_calibrated=False,
            front_intercept=30.0,  # Keep default front RH
            front_coeff_heave_nmm=0.0,
            front_coeff_camber_deg=0.0,
            front_loo_rmse_mm=0.0,
            rear_intercept=0.0,
            rear_coeff_pushrod=0.0,
            rear_coeff_third_nmm=0.0,
            rear_coeff_rear_spring=0.0,
            rear_coeff_heave_perch=0.0,
            rear_coeff_fuel_l=0.0,
            rear_coeff_spring_perch=0.0,
            rear_r_squared=0.0,
            rear_loo_rmse_mm=0.0,
        )


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
    front_m_eff_kg: float            # Calibrated front effective heave mass (scalar fallback)
    rear_m_eff_kg: float             # Calibrated rear effective heave mass (scalar fallback)
    # Rate-dependent m_eff tables (populated by apply_to_car when calibration
    # data shows significant variation with spring rate). Each entry:
    # {"setting": spring_rate_nmm, "m_eff_kg": effective_mass_kg}
    # The lookup interpolates linearly between known points, clamping at the
    # edges. Empty list → use scalar fallback.
    #
    # m_eff_rate_lookup_enabled: Gate for the rate-dependent lookup. Defaults to
    # False so existing solver output is preserved; flip to True per-car after
    # validating the rate table produces sensible output. When False, the solver
    # uses the scalar m_eff even if a table is populated.
    m_eff_rate_lookup_enabled: bool = False
    m_eff_front_rate_table: list[dict] = field(default_factory=list)
    m_eff_rear_rate_table: list[dict] = field(default_factory=list)
    front_spring_range_nmm: tuple[float, float] = (20.0, 200.0)  # Valid garage range
    rear_spring_range_nmm: tuple[float, float] = (100.0, 1000.0)
    # Realistic operating window for objective function realism penalty.
    # Distinct from garage range: this is where competitive setups actually run.
    # If None, falls back to front_spring_range_nmm.
    front_realistic_range_nmm: tuple[float, float] | None = None
    sigma_target_mm: float = 10.0    # Platform stability threshold
    # WARNING: These defaults are BMW Sebring calibrated. Every car MUST override
    # perch_offset_*_baseline_mm with car-specific values from garage screenshots.
    perch_offset_front_baseline_mm: float = -13.0  # BMW default — override per car
    perch_offset_rear_baseline_mm: float = 43.0    # BMW default — override per car
    # HeaveSpringDeflMax calibration: DeflMax = defl_max_intercept + defl_max_slope * spring_rate
    # BMW Sebring calibrated (R²=0.985). Non-BMW cars should set to 0.0 if uncalibrated.
    heave_spring_defl_max_intercept_mm: float = 106.43
    heave_spring_defl_max_slope: float = -0.310  # mm per N/mm of spring rate
    # Rear third spring DeflMax (mm) — approximate constant for extract.py travel budget.
    # For precise values, use DeflectionModel.third_spring_defl_max(rate, perch).
    # Default is for BMW at typical settings (third=450, perch=42).
    rear_third_defl_max_mm: float = 50.0
    # Torsion bar turns calibration (OD=13.9 baseline):
    #   Turns = turns_intercept + turns_heave_coeff / heave_spring_nmm
    torsion_bar_turns_intercept: float = 0.0856
    torsion_bar_turns_heave_coeff: float = 0.668
    torsion_bar_turns_baseline_od_mm: float = 13.9
    front_heave_hard_range_nmm: tuple[float, float] | None = None  # Car-specific hard clamp
    front_heave_hard_range_exempt_tracks: list[str] = field(default_factory=list)
    # Slider position model: SliderStatic = slider_intercept + slider_heave_coeff * heave_nmm
    #                                        + slider_perch_coeff * perch_mm
    # Calibrated from 19 BMW sessions: SliderStatic = 46.2 + 0.012*Heave + 0.251*Perch
    # Perch dominates slider position (21.8:1 ratio vs heave rate)
    slider_intercept: float = 46.2
    slider_heave_coeff: float = 0.012
    slider_perch_coeff: float = 0.251
    # Spring static deflection model: DeflStatic = defl_static_intercept + defl_static_heave_coeff * heave_nmm
    # Calibrated: DeflStatic ≈ 24.0 + (heave - 30) * (-0.55)
    # Simplified: DeflStatic = 40.5 - 0.55 * heave_nmm
    defl_static_intercept: float = 40.5
    defl_static_heave_coeff: float = -0.55
    # Minimum static deflection to keep spring loaded (mm)
    min_static_defl_mm: float = 3.0
    # Maximum allowed slider position (spring nearly unloaded above this)
    max_slider_mm: float = 45.0
    # Optional indexed-control decode for cars whose garage exposes raw indices
    # instead of physical spring rates. When unset, the solver treats the setting
    # value itself as the physical rate.
    front_setting_index_range: tuple[float, float] | None = None
    front_setting_anchor_index: float | None = None
    front_rate_at_anchor_nmm: float | None = None
    front_rate_per_index_nmm: float | None = None
    rear_setting_index_range: tuple[float, float] | None = None
    rear_setting_anchor_index: float | None = None
    rear_rate_at_anchor_nmm: float | None = None
    rear_rate_per_index_nmm: float | None = None
    # Validation flags for unverified heave spring index mappings
    heave_index_unvalidated: bool = False

    def m_eff_at_rate(self, axle: str, spring_rate_nmm: float) -> float:
        """Look up effective heave mass at a given spring rate.

        When a rate table is populated AND m_eff_rate_lookup_enabled is True,
        linearly interpolates between known points (clamping at the edges).
        Otherwise returns the scalar fallback m_eff.

        The enable flag is off by default so existing solver output is
        preserved. Flip to True per-car after validating the rate table
        produces sensible output at the car's operating point.

        This is physics-correct: m_eff in the heave excursion model depends on
        how the spring compresses under aero load, which is a function of
        compliance (1/k). The calibration data for some cars shows strong
        rate dependence (e.g. Porsche: 690-2058 kg across 150-250 N/mm).
        """
        if axle == "front":
            table = self.m_eff_front_rate_table
            scalar = self.front_m_eff_kg
        else:
            table = self.m_eff_rear_rate_table
            scalar = self.rear_m_eff_kg
        if not table or not self.m_eff_rate_lookup_enabled:
            return scalar
        # Group entries by setting and average within each group — the raw
        # table often has multiple samples at the same spring rate due to
        # different operating conditions (lap time, tire state, etc).
        groups: dict[float, list[float]] = {}
        for entry in table:
            try:
                s = float(entry["setting"])
                m = float(entry["m_eff_kg"])
            except (KeyError, TypeError, ValueError):
                continue
            groups.setdefault(s, []).append(m)
        if not groups:
            return scalar
        sorted_settings = sorted(groups.keys())
        averaged = [(s, sum(groups[s]) / len(groups[s])) for s in sorted_settings]
        if spring_rate_nmm <= averaged[0][0]:
            return averaged[0][1]
        if spring_rate_nmm >= averaged[-1][0]:
            return averaged[-1][1]
        for i in range(len(averaged) - 1):
            s0, m0 = averaged[i]
            s1, m1 = averaged[i + 1]
            if s0 <= spring_rate_nmm <= s1:
                if s1 == s0:
                    return m0
                frac = (spring_rate_nmm - s0) / (s1 - s0)
                return m0 + frac * (m1 - m0)
        return scalar

    def front_rate_from_setting(self, setting_value: float) -> float:
        """Decode a garage setting into a physical front heave rate."""
        if (
            self.front_setting_index_range is None
            or self.front_setting_anchor_index is None
            or self.front_rate_at_anchor_nmm is None
            or self.front_rate_per_index_nmm is None
        ):
            return float(setting_value)
        return float(
            self.front_rate_at_anchor_nmm
            + (float(setting_value) - self.front_setting_anchor_index) * self.front_rate_per_index_nmm
        )

    def rear_rate_from_setting(self, setting_value: float) -> float:
        """Decode a garage setting into a physical rear heave/third-spring rate."""
        if (
            self.rear_setting_index_range is None
            or self.rear_setting_anchor_index is None
            or self.rear_rate_at_anchor_nmm is None
            or self.rear_rate_per_index_nmm is None
        ):
            return float(setting_value)
        return float(
            self.rear_rate_at_anchor_nmm
            + (float(setting_value) - self.rear_setting_anchor_index) * self.rear_rate_per_index_nmm
        )

    def front_setting_from_rate(self, rate_nmm: float, *, resolution: float = 1.0) -> float:
        """Encode a physical front heave rate back to the exposed garage setting."""
        if (
            self.front_setting_index_range is None
            or self.front_setting_anchor_index is None
            or self.front_rate_at_anchor_nmm is None
            or self.front_rate_per_index_nmm in (None, 0.0)
        ):
            return float(rate_nmm)
        setting = (
            (float(rate_nmm) - self.front_rate_at_anchor_nmm) / self.front_rate_per_index_nmm
            + self.front_setting_anchor_index
        )
        lo, hi = self.front_setting_index_range
        setting = max(lo, min(hi, setting))
        if resolution > 0:
            setting = round(setting / resolution) * resolution
        return float(max(lo, min(hi, setting)))

    def rear_setting_from_rate(self, rate_nmm: float, *, resolution: float = 1.0) -> float:
        """Encode a physical rear heave/third-spring rate back to the exposed garage setting."""
        if (
            self.rear_setting_index_range is None
            or self.rear_setting_anchor_index is None
            or self.rear_rate_at_anchor_nmm is None
            or self.rear_rate_per_index_nmm in (None, 0.0)
        ):
            return float(rate_nmm)
        setting = (
            (float(rate_nmm) - self.rear_rate_at_anchor_nmm) / self.rear_rate_per_index_nmm
            + self.rear_setting_anchor_index
        )
        lo, hi = self.rear_setting_index_range
        setting = max(lo, min(hi, setting))
        if resolution > 0:
            setting = round(setting / resolution) * resolution
        return float(max(lo, min(hi, setting)))


@dataclass
class DeflectionModel:
    """Calibrated static deflection models for .sto garage display values.

    Predicts iRacing's computed deflection values from setup parameters.
    Calibrated from BMW Sebring LDX ground truth (S1/S2) and
    per-car-quirks 10-point dataset (March 2026).

    Front parameters use multi-variable regressions (11 data points).
    Rear parameters use physics-based force-balance models (exact on S1/S2).
    Shock deflections use pushrod-offset-based models (exact on S1/S2).

    NOTE: This model is BMW-specific. Non-BMW cars should use uncalibrated()
    or pass `is_calibrated=False` explicitly.
    """
    # Default False: any car that constructs `DeflectionModel()` without an
    # explicit `is_calibrated=True` should not silently claim calibration.
    # Cars that ARE calibrated (BMW, Ferrari, Porsche) pass `is_calibrated=True`
    # at their construction site. This mirrors the `RideHeightModel` default
    # change made in LOW-7 / 2026-04-10.
    is_calibrated: bool = False

    # --- Shock deflection: defl = intercept + coeff * pushrod_offset ---
    # Calibrated from 31 unique setups across 41 BMW sessions (March 2026)
    shock_front_intercept: float = 21.228
    shock_front_pushrod_coeff: float = 0.226
    # Direct front-shock model with heave_perch and torsion_od terms (used when
    # front_shock_defl_direct=True). Captures load sharing between heave spring
    # and corner shock via perch offset and torsion bar stiffness.
    front_shock_defl_direct: bool = False
    front_shock_defl_heave_perch_coeff: float = 0.0
    front_shock_defl_torsion_od_coeff: float = 0.0
    front_shock_defl_heave_coeff: float = 0.0

    shock_rear_intercept: float = 25.924
    shock_rear_pushrod_coeff: float = 0.266
    # Direct rear-shock model with compliance + perches (used when
    # rear_shock_defl_direct=True). Captures the effect of spring/third
    # compliance and perch offsets on rear shock static deflection in addition
    # to the pushrod term.
    rear_shock_defl_direct: bool = False
    rear_shock_defl_inv_third_coeff: float = 0.0
    rear_shock_defl_inv_spring_coeff: float = 0.0
    rear_shock_defl_third_perch_coeff: float = 0.0
    rear_shock_defl_spring_perch_coeff: float = 0.0

    # --- TorsionBarDefl ---
    # TBDefl = (load_intercept + load_heave*heave + load_perch*perch) / k_torsion
    # where k_torsion = C_torsion * OD^4 (from CornerSpringModel)
    # Calibrated from 31 unique setups, R²=0.905
    # Raw fit: defl * OD^4 = 1256447 - 4803*heave + 12547*perch
    # Scaled by C_torsion (0.0008036) to get load units for division by k_torsion
    tb_load_intercept: float = 1009.9
    tb_load_heave_coeff: float = -3.860
    tb_load_perch_coeff: float = 10.083

    # --- HeaveSpringDeflStatic ---
    # SprDS = intercept + inv_heave/heave + perch_coeff*perch + inv_od4/OD^4
    # Calibrated from 31 unique setups, R²=0.953, RMSE=0.97mm
    heave_defl_intercept: float = -20.756
    heave_defl_inv_heave_coeff: float = 7.030
    heave_defl_perch_coeff: float = -0.9146
    heave_defl_inv_od4_coeff: float = 666311.0

    # --- HeaveSliderDeflStatic ---
    # SldrS = intercept + heave_coeff*heave + perch_coeff*perch + od_coeff*OD
    # Calibrated from 31 unique setups across 41 BMW sessions, R²=0.688
    slider_intercept: float = 102.04
    slider_heave_coeff: float = -0.000303
    slider_perch_coeff: float = 0.091
    slider_od_coeff: float = -4.108

    # --- Rear SpringDeflStatic (force-balance) ---
    # defl = (load - perch_coeff * spring_perch) / spring_rate
    # Calibrated from 31 unique setups across 41 BMW sessions, R²=0.828
    # Regression: defl*rate = 6091.76 - 115.89*perch → perch_coeff stored as positive
    rear_spring_eff_load: float = 6091.76
    rear_spring_perch_coeff: float = 115.89
    # Direct model (used when rear_spring_defl_direct=True). Supports both
    # linear and compliance terms for spring/third (compliance is physics-correct
    # for static deflection under aero load: defl = F/k → ∝ 1/k). Perch and
    # pushrod terms are linear.
    # defl = intercept + c_rate*spring + c_inv_rate/spring
    #               + c_third*third + c_inv_third/third
    #               + c_spring_perch*spring_perch + c_third_perch*third_perch
    #               + c_pushrod*pushrod
    rear_spring_defl_direct: bool = False
    rear_spring_defl_intercept: float = 0.0
    rear_spring_defl_rate_coeff: float = 0.0
    rear_spring_defl_inv_rate_coeff: float = 0.0
    rear_spring_defl_third_coeff: float = 0.0
    rear_spring_defl_inv_third_coeff: float = 0.0
    rear_spring_defl_perch_coeff: float = 0.0
    rear_spring_defl_third_perch_coeff: float = 0.0
    rear_spring_defl_pushrod_coeff: float = 0.0

    # --- ThirdSpringDeflStatic (force-balance) ---
    # defl = (load - perch_coeff * third_perch) / third_rate
    # Calibrated from 31 unique setups across 41 BMW sessions, R²=0.942
    # Regression: defl*rate = 17817.75 - 357.96*perch → perch_coeff stored as positive
    third_spring_eff_load: float = 17817.75
    third_spring_perch_coeff: float = 357.96
    # Direct model (used when third_spring_defl_direct=True). Same shape as
    # rear_spring_defl: linear + compliance + perches + pushrod.
    third_spring_defl_direct: bool = False
    third_spring_defl_intercept: float = 0.0
    third_spring_defl_third_coeff: float = 0.0
    third_spring_defl_inv_third_coeff: float = 0.0
    third_spring_defl_spring_coeff: float = 0.0
    third_spring_defl_inv_spring_coeff: float = 0.0
    third_spring_defl_perch_coeff: float = 0.0
    third_spring_defl_spring_perch_coeff: float = 0.0
    third_spring_defl_pushrod_coeff: float = 0.0

    # --- ThirdSliderDeflStatic ---
    # slider = intercept + coeff * ThirdSpringDeflStatic
    # Links slider travel to spring deflection via geometric lever ratio.
    # Calibrated from 31 unique setups across 41 BMW sessions, R²=0.373
    third_slider_intercept: float = 18.224
    third_slider_spring_defl_coeff: float = 0.283

    # --- Rear SpringDeflMax ---
    # defl_max = intercept + rate_coeff * spring_rate + perch_coeff * spring_perch
    # Calibrated from 31 unique setups, R²=0.998
    rear_spring_defl_max_intercept: float = 104.59
    rear_spring_defl_max_rate_coeff: float = -0.157
    rear_spring_defl_max_perch_coeff: float = 0.009

    # --- Third SpringDeflMax ---
    # defl_max = intercept + rate_coeff * third_rate + perch_coeff * third_perch
    # Calibrated from 31 unique setups, R²=0.996
    third_spring_defl_max_intercept: float = 85.07
    third_spring_defl_max_rate_coeff: float = -0.072
    third_spring_defl_max_perch_coeff: float = -0.036

    def shock_defl_front(self, pushrod_offset_mm: float,
                        heave_perch_mm: float = 0.0,
                        torsion_od_mm: float = 0.0,
                        heave_nmm: float = 0.0) -> float:
        base = (self.shock_front_intercept
                + self.shock_front_pushrod_coeff * pushrod_offset_mm)
        if self.front_shock_defl_direct:
            base += self.front_shock_defl_heave_perch_coeff * heave_perch_mm
            base += self.front_shock_defl_torsion_od_coeff * torsion_od_mm
            base += self.front_shock_defl_heave_coeff * heave_nmm
        return base

    def shock_defl_rear(self, pushrod_offset_mm: float,
                        third_rate_nmm: float = 0.0,
                        spring_rate_nmm: float = 0.0,
                        third_perch_mm: float = 0.0,
                        spring_perch_mm: float = 0.0) -> float:
        base = (self.shock_rear_intercept
                + self.shock_rear_pushrod_coeff * pushrod_offset_mm)
        if not self.rear_shock_defl_direct:
            return base
        inv_third = 1.0 / max(third_rate_nmm, 1.0) if third_rate_nmm > 0 else 0.0
        inv_spring = 1.0 / max(spring_rate_nmm, 1.0) if spring_rate_nmm > 0 else 0.0
        return (base
                + self.rear_shock_defl_inv_third_coeff * inv_third
                + self.rear_shock_defl_inv_spring_coeff * inv_spring
                + self.rear_shock_defl_third_perch_coeff * third_perch_mm
                + self.rear_shock_defl_spring_perch_coeff * spring_perch_mm)

    def torsion_bar_defl(self, heave_nmm: float, perch_mm: float, k_torsion: float) -> float:
        load = (self.tb_load_intercept
                + self.tb_load_heave_coeff * heave_nmm
                + self.tb_load_perch_coeff * perch_mm)
        return load / max(k_torsion, 0.1)

    def heave_spring_defl_static(self, heave_nmm: float, perch_mm: float, od_mm: float) -> float:
        return (self.heave_defl_intercept
                + self.heave_defl_inv_heave_coeff / max(heave_nmm, 1.0)
                + self.heave_defl_perch_coeff * perch_mm
                + self.heave_defl_inv_od4_coeff / max(od_mm ** 4, 1.0))

    def heave_slider_defl_static(self, heave_nmm: float, perch_mm: float, od_mm: float) -> float:
        return (self.slider_intercept
                + self.slider_heave_coeff * heave_nmm
                + self.slider_perch_coeff * perch_mm
                + self.slider_od_coeff * od_mm)

    def rear_spring_defl_static(self, spring_rate_nmm: float, spring_perch_mm: float,
                                third_rate_nmm: float = 0.0,
                                third_perch_mm: float = 0.0,
                                pushrod_mm: float = 0.0) -> float:
        if self.rear_spring_defl_direct:
            inv_rate = 1.0 / max(spring_rate_nmm, 1.0)
            inv_third = 1.0 / max(third_rate_nmm, 1.0) if third_rate_nmm > 0 else 0.0
            return (self.rear_spring_defl_intercept
                    + self.rear_spring_defl_rate_coeff * spring_rate_nmm
                    + self.rear_spring_defl_inv_rate_coeff * inv_rate
                    + self.rear_spring_defl_third_coeff * third_rate_nmm
                    + self.rear_spring_defl_inv_third_coeff * inv_third
                    + self.rear_spring_defl_perch_coeff * spring_perch_mm
                    + self.rear_spring_defl_third_perch_coeff * third_perch_mm
                    + self.rear_spring_defl_pushrod_coeff * pushrod_mm)
        return ((self.rear_spring_eff_load - self.rear_spring_perch_coeff * spring_perch_mm)
                / max(spring_rate_nmm, 1.0))

    def third_spring_defl_static(self, third_rate_nmm: float, third_perch_mm: float,
                                 spring_rate_nmm: float = 0.0,
                                 spring_perch_mm: float = 0.0,
                                 pushrod_mm: float = 0.0) -> float:
        if self.third_spring_defl_direct:
            inv_third = 1.0 / max(third_rate_nmm, 1.0)
            inv_spring = 1.0 / max(spring_rate_nmm, 1.0) if spring_rate_nmm > 0 else 0.0
            return (self.third_spring_defl_intercept
                    + self.third_spring_defl_third_coeff * third_rate_nmm
                    + self.third_spring_defl_inv_third_coeff * inv_third
                    + self.third_spring_defl_spring_coeff * spring_rate_nmm
                    + self.third_spring_defl_inv_spring_coeff * inv_spring
                    + self.third_spring_defl_perch_coeff * third_perch_mm
                    + self.third_spring_defl_spring_perch_coeff * spring_perch_mm
                    + self.third_spring_defl_pushrod_coeff * pushrod_mm)
        return ((self.third_spring_eff_load - self.third_spring_perch_coeff * third_perch_mm)
                / max(third_rate_nmm, 1.0))

    def third_slider_defl_static(self, third_spring_defl_mm: float) -> float:
        return self.third_slider_intercept + self.third_slider_spring_defl_coeff * third_spring_defl_mm

    def rear_spring_defl_max(self, spring_rate_nmm: float, spring_perch_mm: float) -> float:
        """Max rear spring deflection (mm) from rate and perch."""
        return (self.rear_spring_defl_max_intercept
                + self.rear_spring_defl_max_rate_coeff * spring_rate_nmm
                + self.rear_spring_defl_max_perch_coeff * spring_perch_mm)

    def third_spring_defl_max(self, third_rate_nmm: float, third_perch_mm: float) -> float:
        """Max third spring deflection (mm) from rate and perch."""
        return (self.third_spring_defl_max_intercept
                + self.third_spring_defl_max_rate_coeff * third_rate_nmm
                + self.third_spring_defl_max_perch_coeff * third_perch_mm)

    @classmethod
    def uncalibrated(cls) -> DeflectionModel:
        """Return an uncalibrated DeflectionModel with all coefficients zeroed.

        Use this for non-BMW cars where the BMW-specific regression coefficients
        would produce incorrect results.
        """
        return cls(
            is_calibrated=False,
            shock_front_intercept=0.0,
            shock_front_pushrod_coeff=0.0,
            shock_rear_intercept=0.0,
            shock_rear_pushrod_coeff=0.0,
            tb_load_intercept=0.0,
            tb_load_heave_coeff=0.0,
            tb_load_perch_coeff=0.0,
            heave_defl_intercept=0.0,
            heave_defl_inv_heave_coeff=0.0,
            heave_defl_perch_coeff=0.0,
            heave_defl_inv_od4_coeff=0.0,
            slider_intercept=0.0,
            slider_heave_coeff=0.0,
            slider_perch_coeff=0.0,
            slider_od_coeff=0.0,
            rear_spring_eff_load=0.0,
            rear_spring_perch_coeff=0.0,
            third_spring_eff_load=0.0,
            third_spring_perch_coeff=0.0,
            third_slider_intercept=0.0,
            third_slider_spring_defl_coeff=0.0,
            rear_spring_defl_max_intercept=0.0,
            rear_spring_defl_max_rate_coeff=0.0,
            rear_spring_defl_max_perch_coeff=0.0,
            third_spring_defl_max_intercept=0.0,
            third_spring_defl_max_rate_coeff=0.0,
            third_spring_defl_max_perch_coeff=0.0,
        )


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

    ═══════════════════════════════════════════════════════════════════════
    SUSPENSION GEOMETRY & TORSION BAR WHEEL RATE DERIVATION
    Research: iRacing GTP BMW M Hybrid V8 LMDh, March 2026
    ═══════════════════════════════════════════════════════════════════════

    ## Real-Car Architecture (BMW M Hybrid V8 LMDh)
    The physical BMW M Hybrid V8 uses pushrod-actuated torsion bars at BOTH
    ends (LMDh regulations require very tight packaging for the hybrid
    drivetrain). iRacing sourced the model from BMW CAD + physics data per
    the 2023 IMSA partnership announcement. The front suspension is a
    double-wishbone pushrod design where:
      - Pushrod connects lower wishbone to an inboard rocker
      - Rocker converts pushrod linear motion into torsion bar twist
      - Torsion bar runs longitudinally inside the monocoque for packaging

    ## Physics Chain: Bar OD → Wheel Rate
    Step 1: Bar angular stiffness (torsional mechanics, Pirate4x4 / Roark's)
        K_t_angular = π * G * d^4 / (32 * L)   [N·mm / rad]
        where:
          G = 77,000 N/mm²  (chromoly steel shear modulus; BMW motorsport bars)
          d = bar OD in mm  (the garage-adjustable parameter)
          L = effective bar length in mm (fixed geometry)

    Step 2: Convert angular stiffness → linear spring rate at rocker output
        k_rocker = K_t_angular / r_arm²          [N/mm at rocker pivot]
        where r_arm = rocker arm length to pushrod attachment (mm)

    Step 3: Apply motion ratio to get wheel-center rate
        k_wheel = k_rocker * MR²                  [N/mm at wheel center]
        where MR = pushrod motion ratio (vertical wheel displacement / pushrod
        compression). For a well-optimized GTP pushrod front, MR ≈ 0.75–0.85.

    Combining steps 1–3:
        k_wheel = [π * G * MR² / (32 * L * r_arm²)] * d^4
                = C_torsion * d^4
        where C_torsion = π * G * MR² / (32 * L * r_arm²)

    ## Back-Calculated Geometry (BMW, C_torsion = 0.0008036)
    Using G = 77,000 N/mm², MR = 0.78 (mid-range GTP pushrod):
        L * r_arm² = π * G * MR² / (32 * C) = 5,723,213 mm³

    Plausible geometry solutions:
        r_arm = 100 mm → L = 572 mm  (bar + lever fits in standard monocoque)
        r_arm = 110 mm → L = 473 mm  ← most likely for BMW M Hybrid V8
        r_arm = 120 mm → L = 397 mm  (more compact)
    All three are physically plausible for a 2023 LMDh prototype.

    ## Calibrated Wheel Rate Range (BMW GTP, iRacing garage)
        OD = 11.0 mm (min)  → k_wheel ≈ 11.8 N/mm  (very soft)
        OD = 13.9 mm (ref)  → k_wheel ≈ 30.0 N/mm  ← verified from IBT data
        OD = 15.0 mm        → k_wheel ≈ 40.7 N/mm
        OD = 16.0 mm        → k_wheel ≈ 52.7 N/mm
        OD = 17.0 mm        → k_wheel ≈ 67.1 N/mm
        OD = 18.2 mm (max)  → k_wheel ≈ 88.2 N/mm  (stiffest legal)

    ## Dual-Duty: Corner Spring + Roll Stiffness Coupling
    CRITICAL: The front torsion bar does double duty.
    In heave (both wheels move equally), BOTH bars compress symmetrically
    → each bar contributes k_wheel to total axle heave stiffness.
    In roll (one wheel up, one down), bars work in OPPOSITION:
    → Roll stiffness from corners: K_roll = k_wheel * t_front² / 2
      where t_front ≈ 1,600 mm (BMW GTP track width)

    Example at OD = 13.9 mm (k_wheel = 30 N/mm):
        K_roll_corner = 30 * 1600² / 2 = 38,400,000 N·mm/rad ≈ 38.4 kN·m/rad

    This means OD changes affect BOTH:
      1. Corner natural frequency (heave dynamics)
      2. Front roll stiffness balance (LLTD, understeer/oversteer)

    The objective.py TORSION_ARB_COUPLING term (γ=0.25) adds a small empirical
    correction: Δ(OD) also slightly scales effective k_arb_front (back-calibrated
    from BMW Sebring IBT LLTD data, not from first principles). The direct
    corner-spring roll effect is the dominant term:
    For every +1 mm OD increase at nominal (13.9→14.9): k_wheel shifts
    ~+9 N/mm → K_roll_front shifts +~11.5 kN·m/rad → LLTD shifts ~+0.4%
    (with ~+0.1% additional from the ARB coupling correction at γ=0.25).

    ## iRacing vs Real Car Note
    iRacing uses a simplified "k = C * OD^4" model that abstracts L and MR
    into the single calibration constant. This is correct for optimization
    purposes because L and MR are fixed geometry (not garage-adjustable).
    The C_torsion = 0.0008036 value was derived by running the verified
    Sebring 2024 race-winning setup through ride height telemetry (IBT).

    ## Pushrod vs Pullrod: Why BMW Chose Pushrod Front
    (Research: iRacing GTP BMW LMDh suspension geometry, March 2026)
    The BMW M Hybrid V8 uses pushrod actuation at the front (compression
    loads the torsion bar) and — unlike pure LMP1 designs — also pushrod
    at the rear. The Dallara-built LMDh platform mandates tight inboard
    packaging to accommodate the shared hybrid drivetrain; pushrod front
    keeps the rocker and torsion bar high in the monocoque, away from the
    crash structure. By contrast, pullrod front places the rocker low and
    forward (favored by F1 and LMP1 for CG benefits), which conflicts with
    the LMDh's mandated Dallara rear subframe geometry.
    Source: coach dave academy BMW M Hybrid V8 guide (2025); iRacing.com
    BMW announcement (Apr 2023); IMSA LMDh technical regulations.

    ## Natural Frequency Sensitivity per OD Step (BMW, m_corner ≈ 258 kg)
    Derived from k_wheel = C_torsion * OD^4, f = (1/2π) * sqrt(k/m):
        OD = 11.0 mm → k=11.8 N/mm → f ≈ 1.07 Hz  (very soft, 1.5× race)
        OD = 13.9 mm → k=30.0 N/mm → f ≈ 1.71 Hz  ← baseline (calibrated)
        OD = 15.0 mm → k=40.7 N/mm → f ≈ 1.99 Hz
        OD = 16.0 mm → k=52.7 N/mm → f ≈ 2.27 Hz
        OD = 18.2 mm → k=88.2 N/mm → f ≈ 2.94 Hz  (very stiff)
    Rear coil (k=120–240 N/mm, m_corner ≈ 258 kg): f ≈ 3.43–4.86 Hz.
    → Front torsion bars operate in 1–3 Hz range; rear coils 3–5 Hz.
    → Frequency isolation ratio (rear/front) stays 1.7–3.0× across legal
      range, with softest fronts risking resonance coupling (ratio < 2.0
      triggers the objective's isolation penalty).

    ## Garage Step Non-Linearity
    Because k ∝ OD^4, equal OD steps produce unequal stiffness steps:
        Δk per 0.1mm at OD=11.0 mm: +0.43 N/mm  (small effect at low end)
        Δk per 0.1mm at OD=13.9 mm: +0.87 N/mm  (baseline sensitivity)
        Δk per 0.1mm at OD=16.0 mm: +1.32 N/mm  (large effect at high end)
    This means a single garage click at a stiff bar has ~3× the stiffness
    effect of the same click at the softest bar. Tuners pushing high-OD
    setups are operating in the sensitive region — small changes, large
    handling delta.
    ═══════════════════════════════════════════════════════════════════════
    """
    # Front torsion bar
    front_torsion_c: float           # Calibration constant: k_wheel = C * OD^4
    front_torsion_od_ref_mm: float   # Reference OD for calibration
    front_torsion_od_range_mm: tuple[float, float] = (11.0, 16.0)
    front_torsion_od_step_mm: float = 0.10  # Garage step size (fallback if no options)
    # Discrete OD options from iRacing garage — if set, snap_torsion_od uses these
    # instead of continuous step rounding.
    front_torsion_od_options: list[float] = field(default_factory=list)

    # Rear corner spring — either coil (BMW/Cadillac) or torsion bar (Ferrari/Acura)
    # Coil spring (rear_torsion_c is None):
    rear_spring_range_nmm: tuple[float, float] = (100.0, 300.0)
    rear_spring_step_nmm: float = 10.0      # Garage step size
    # Rear torsion bar (ORECA/Ferrari — set rear_torsion_c to enable):
    rear_torsion_c: float | None = None     # k_wheel = C * OD^4 (None = coil spring)
    rear_torsion_od_range_mm: tuple[float, float] = (11.0, 16.0)
    rear_torsion_od_step_mm: float = 0.10
    rear_torsion_od_options: list[float] = field(default_factory=list)

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
    # Optional indexed-control decode for cars whose garage exposes torsion bars
    # as raw indices instead of direct OD / rate numbers.
    front_setting_index_range: tuple[float, float] | None = None
    rear_setting_index_range: tuple[float, float] | None = None
    # Validation flag for unverified rear torsion bar model
    rear_torsion_unvalidated: bool = False

    # Roll spring wheel rate (Porsche Multimatic only)
    front_roll_spring_rate_nmm: float = 0.0
    front_roll_spring_range_nmm: tuple[float, float] = (0.0, 0.0)  # (0,0) = not a roll spring car
    front_roll_spring_step_nmm: float = 10.0
    # Roll spring is a SINGLE element (not a pair of corner springs). For roll stiffness,
    # use k*(t/2)² not 2*k*(t/2)². Installation ratio accounts for lever arm geometry.
    front_is_roll_spring: bool = False
    front_roll_spring_installation_ratio: float = 0.882  # CALIBRATED: back-calculated from measured LLTD 50.3% at Algarve

    def torsion_bar_rate(self, od_mm: float) -> float:
        """Wheel rate (N/mm) from torsion bar OD.

        Falls back to front_roll_spring_rate_nmm if no torsion bar (Porsche).
        """
        if self.front_torsion_c > 0:
            return self.front_torsion_c * od_mm ** 4
        return self.front_roll_spring_rate_nmm

    def snap_front_roll_spring(self, k_nmm: float) -> float:
        """Snap roll spring rate to nearest garage step within range."""
        if self.front_roll_spring_range_nmm[1] <= 0:
            return self.front_roll_spring_rate_nmm  # not a roll spring car
        lo, hi = self.front_roll_spring_range_nmm
        step = self.front_roll_spring_step_nmm
        k_snapped = round(round(k_nmm / step) * step, 0)
        return float(max(lo, min(hi, k_snapped)))

    def torsion_bar_od_for_rate(self, k_wheel_nmm: float) -> float:
        """Torsion bar OD (mm) needed for a target wheel rate."""
        if self.front_torsion_c <= 0:
            return 0.0  # No front torsion bar (e.g. Porsche uses Roll Spring)
        return (k_wheel_nmm / self.front_torsion_c) ** 0.25

    def snap_torsion_od(self, od_mm: float) -> float:
        """Snap OD to nearest discrete garage option (or step if no options)."""
        if self.front_torsion_od_options:
            return min(self.front_torsion_od_options,
                       key=lambda x: abs(x - od_mm))
        step = self.front_torsion_od_step_mm
        lo = self.front_torsion_od_range_mm[0]
        # Align to steps from range minimum (e.g., 13.90 + N*0.22 for Acura)
        steps_from_lo = round((od_mm - lo) / step)
        return round(lo + max(0, steps_from_lo) * step, 2)

    def snap_rear_rate(self, k_nmm: float) -> float:
        """Snap rear spring rate to nearest garage step."""
        step = self.rear_spring_step_nmm
        return round(round(k_nmm / step) * step, 0)

    @property
    def rear_is_torsion_bar(self) -> bool:
        """True if rear uses torsion bars instead of coil springs."""
        return self.rear_torsion_c is not None

    def rear_torsion_bar_rate(self, od_mm: float) -> float:
        """Rear wheel rate (N/mm) from torsion bar OD."""
        if self.rear_torsion_c is None:
            raise ValueError("Rear is coil spring, not torsion bar")
        return self.rear_torsion_c * od_mm ** 4

    def rear_torsion_bar_od_for_rate(self, k_wheel_nmm: float) -> float:
        """Rear torsion bar OD (mm) needed for a target wheel rate."""
        if self.rear_torsion_c is None:
            raise ValueError("Rear is coil spring, not torsion bar")
        return (k_wheel_nmm / self.rear_torsion_c) ** 0.25

    def snap_rear_torsion_od(self, od_mm: float) -> float:
        """Snap rear torsion bar OD to nearest discrete garage option."""
        if self.rear_torsion_od_options:
            return min(self.rear_torsion_od_options,
                       key=lambda x: abs(x - od_mm))
        step = self.rear_torsion_od_step_mm
        lo = self.rear_torsion_od_range_mm[0]
        # Align to steps from range minimum (e.g., 13.90 + N*0.22)
        steps_from_lo = round((od_mm - lo) / step)
        return round(lo + max(0, steps_from_lo) * step, 2)

    def front_torsion_od_from_setting(self, setting_value: float) -> float:
        """Decode a garage setting into a physical front torsion-bar OD."""
        if self.front_setting_index_range is None:
            return float(setting_value)
        idx_lo, idx_hi = self.front_setting_index_range
        od_lo, od_hi = self.front_torsion_od_range_mm
        if abs(idx_hi - idx_lo) < 1e-9:
            return float(od_lo)
        t = (float(setting_value) - idx_lo) / (idx_hi - idx_lo)
        t = max(0.0, min(1.0, t))
        return float(od_lo + t * (od_hi - od_lo))

    def front_setting_from_torsion_od(self, od_mm: float, *, resolution: float = 1.0) -> float:
        """Encode a physical front torsion-bar OD back to the exposed garage setting."""
        if self.front_setting_index_range is None:
            return float(od_mm)
        idx_lo, idx_hi = self.front_setting_index_range
        od_lo, od_hi = self.front_torsion_od_range_mm
        if abs(od_hi - od_lo) < 1e-9:
            return float(idx_lo)
        t = (float(od_mm) - od_lo) / (od_hi - od_lo)
        t = max(0.0, min(1.0, t))
        setting = idx_lo + t * (idx_hi - idx_lo)
        if resolution > 0:
            setting = round(setting / resolution) * resolution
        return float(max(idx_lo, min(idx_hi, setting)))

    def rear_bar_rate_from_setting(self, setting_value: float) -> float:
        """Decode a garage setting into a physical rear torsion-bar rate."""
        if self.rear_setting_index_range is None:
            return float(setting_value)
        idx_lo, idx_hi = self.rear_setting_index_range
        rate_lo, rate_hi = self.rear_spring_range_nmm
        if abs(idx_hi - idx_lo) < 1e-9:
            return float(rate_lo)
        t = (float(setting_value) - idx_lo) / (idx_hi - idx_lo)
        t = max(0.0, min(1.0, t))
        return float(rate_lo + t * (rate_hi - rate_lo))

    def rear_setting_from_bar_rate(self, rate_nmm: float, *, resolution: float = 1.0) -> float:
        """Encode a physical rear torsion-bar rate back to the exposed garage setting."""
        if self.rear_setting_index_range is None:
            return float(rate_nmm)
        idx_lo, idx_hi = self.rear_setting_index_range
        rate_lo, rate_hi = self.rear_spring_range_nmm
        if abs(rate_hi - rate_lo) < 1e-9:
            return float(idx_lo)
        t = (float(rate_nmm) - rate_lo) / (rate_hi - rate_lo)
        t = max(0.0, min(1.0, t))
        setting = idx_lo + t * (idx_hi - idx_lo)
        if resolution > 0:
            setting = round(setting / resolution) * resolution
        return float(max(idx_lo, min(idx_hi, setting)))


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
    is_calibrated: bool = False      # True when stiffness values are derived from measured data

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
    front_camber_range_deg: tuple[float, float] = (-2.9, 0.0)   # iRacing GTP legal max
    rear_camber_range_deg: tuple[float, float] = (-1.9, 0.0)    # iRacing GTP legal max
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
    camber_is_derived: bool = False  # True if camber comes from suspension geometry (not independently settable)
    roll_gains_calibrated: bool = False  # True when roll gains are derived from IBT telemetry


@dataclass
class DamperModel:
    """Damper model parameterized in garage clicks.

    iRacing BMW M Hybrid V8 damper physics (from official user manual V2, 2025):
    ────────────────────────────────────────────────────────────────────────
    • Click system: 0 = fully closed (max damping), higher clicks = softer.
      iRacing DOES NOT publish N/click or force-velocity curves.
    • LS Comp: Controls load transfer rate under driver inputs (steering,
      braking, throttle). Higher = faster weight transfer → more understeer.
    • HS Comp: Controls bump absorption for curb strikes and track bumps.
      Higher = stiffer platform but worse bump absorption.
    • HS Slope: Shape of high-speed compression curve.
      Lower slope = more digressive (flatter at high velocities, better bump absorption).
      Higher slope = more linear (aggressive, higher HS force at high velocities).
      "Higher slope values producing a higher overall force for high-speed compression."
      → Slope controls the degree of digression in the force-velocity curve.
    • LS Rebound: Controls shock extension rate. Higher = resists extension more.
      Front: higher → more on-throttle understeer but less splitter lift.
      Rear: higher → more off-throttle understeer but less rear-end lift.
    • HS Rebound: Extension over bumps/curbs. Less handling effect than LS.

    Force-per-click values below are ESTIMATED from reverse-engineering,
    NOT from official data. iRacing does not publish force curves.
    """
    ls_comp_range: tuple[int, int] = (0, 11)   # BMW/Dallara default; Ferrari overrides
    ls_rbd_range: tuple[int, int] = (0, 11)
    hs_comp_range: tuple[int, int] = (0, 11)
    hs_rbd_range: tuple[int, int] = (0, 11)
    hs_slope_range: tuple[int, int] = (0, 11)
    hs_slope_rbd_range: tuple[int, int] | None = None  # Ferrari only (lfHSSlopeRbdDampSetting)
    # Force-per-click ESTIMATED by reverse-engineering from physics:
    # c_damping * v_ref / clicks = fpc
    # Front LS: 5060 * 0.025 / 7 = 18.1 N/click
    # Rear LS: 4358 * 0.025 / 6 = 18.2 N/click ← remarkably consistent!
    # Front HS: 2586 * 0.15 / 5 = 77.6 N/click
    # Rear HS: 2034 * 0.15 / 3 = 101.7 N/click
    # WARNING: These are estimates. iRacing does not publish force curves.
    ls_force_per_click_n: float = 18.0     # N per click at 25 mm/s (estimated)
    hs_force_per_click_n: float = 80.0     # N per click at 150 mm/s (estimated)
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
    # Shock force-velocity curve parameters (for combined spring+shock modeling)
    # F = c * v; c varies between LS and HS regimes with digressive transition
    front_ls_coefficient_nsm: float = 5060.0   # Front LS damping coefficient (N·s/m)
    front_hs_coefficient_nsm: float = 2586.0   # Front HS damping coefficient (N·s/m)
    rear_ls_coefficient_nsm: float = 4358.0    # Rear LS damping coefficient (N·s/m)
    rear_hs_coefficient_nsm: float = 2034.0    # Rear HS damping coefficient (N·s/m)
    knee_velocity_mps: float = 0.050           # LS/HS transition velocity (50 mm/s)

    # Digressive exponent: F = c * v^n. n=1.0 is linear, n<1.0 is digressive.
    # iRacing's damper model is suspected to be digressive (n ≈ 0.7-0.9).
    # Default 1.0 means no change to existing behaviour until calibrated per car.
    digressive_exponent: float = 1.0

    # ORECA heave+roll damper architecture (Acura ARX-06)
    # When True, the car has separate roll dampers (FrontRoll/RearRoll)
    # instead of per-corner dampers. The main LS/HS fields above apply
    # to the heave dampers; roll dampers have their own LS/HS clicks.
    has_roll_dampers: bool = False
    # Per-axle roll damper presence — Porsche has FRONT roll damper but NO
    # rear roll damper (rear roll motion is implicit in per-corner shocks).
    # Acura has BOTH front and rear roll dampers. Default False — opt-in.
    has_front_roll_damper: bool = False
    has_rear_roll_damper: bool = False
    roll_ls_range: tuple[int, int] = (1, 11)
    roll_hs_range: tuple[int, int] = (1, 11)
    # Roll damper baselines (LS and HS for front/rear roll dampers)
    front_roll_ls_baseline: int = 2
    front_roll_hs_baseline: int = 3
    rear_roll_ls_baseline: int = 5
    rear_roll_hs_baseline: int = 5

    # Ferrari heave damper architecture (separate from corner dampers)
    # When True, the car has separate heave dampers (FrontHeave/RearHeave)
    # in addition to per-corner dampers. Each has LS comp, HS comp, LS rbd,
    # HS rbd, and HS slope.
    has_heave_dampers: bool = False
    heave_ls_range: tuple[int, int] = (0, 40)
    heave_hs_range: tuple[int, int] = (0, 40)
    heave_hs_slope_range: tuple[int, int] = (0, 11)
    # Heave damper baselines (5 params each: LS comp, HS comp, LS rbd, HS rbd, HS slope)
    front_heave_baseline: dict | None = None
    rear_heave_baseline: dict | None = None

    # Damping ratio targets (ζ) — calibrated from IBT for BMW, conservative defaults otherwise
    # LEGACY per-mode fields (kept for backward compatibility with damper solver):
    zeta_ls_comp: float = 0.55    # Conservative default for uncalibrated cars
    zeta_hs_comp: float = 0.20
    zeta_ls_rbd: float = 0.40
    zeta_hs_rbd: float = 0.18
    zeta_is_calibrated: bool = False  # True only when IBT-calibrated

    # Per-axle zeta targets for objective function scoring.
    # These are the correct targets for comparing physics.zeta_ls_front/rear
    # and physics.zeta_hs_front/rear in the objective function.
    # BMW values from IBT calibration (top-15 fastest sessions, 2026-03-26).
    # Other cars use conservative defaults until IBT-calibrated.
    zeta_target_ls_front: float = 0.55   # LS compression damping ratio, front axle
    zeta_target_ls_rear: float = 0.40    # LS compression damping ratio, rear axle
    zeta_target_hs_front: float = 0.20   # HS compression damping ratio, front axle
    zeta_target_hs_rear: float = 0.18    # HS compression damping ratio, rear axle

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
class GarageRanges:
    """iRacing-legal parameter ranges for .sto validation and clamping.

    Each range is (min, max) inclusive.  Resolution fields define the
    quantisation step that iRacing's garage enforces for each parameter.
    """
    front_pushrod_mm: tuple[float, float] = (-40.0, 40.0)
    rear_pushrod_mm: tuple[float, float] = (-40.0, 40.0)
    front_heave_nmm: tuple[float, float] = (0.0, 900.0)
    rear_third_nmm: tuple[float, float] = (10.0, 900.0)
    front_heave_perch_mm: tuple[float, float] = (-100.0, 100.0)
    rear_third_perch_mm: tuple[float, float] = (20.0, 55.0)
    front_torsion_od_mm: tuple[float, float] = (13.9, 18.2)
    front_torsion_od_discrete: list[float] = field(default_factory=list)  # discrete garage options
    rear_spring_nmm: tuple[float, float] = (100.0, 300.0)
    rear_spring_perch_mm: tuple[float, float] = (25.0, 45.0)
    static_rh_mm: tuple[float, float] = (30.0, 80.0)  # GTP legal minimum 30mm both axles
    arb_blade: tuple[int, int] = (1, 5)
    damper_click: tuple[int, int] = (0, 11)  # BMW verified; Ferrari overrides
    camber_front_deg: tuple[float, float] = (-2.9, 0.0)   # iRacing GTP legal max
    camber_rear_deg: tuple[float, float] = (-1.9, 0.0)    # iRacing GTP legal max
    toe_front_mm: tuple[float, float] = (-3.0, 3.0)
    toe_rear_mm: tuple[float, float] = (-2.0, 3.0)
    torsion_bar_turns_range: tuple[float, float] = (0.0, 0.0)  # No torsion bar turns by default
    # Resolution (quantisation step sizes)
    pushrod_resolution_mm: float = 0.5
    torsion_bar_turns_resolution: float = 0.125  # 1/8 turn steps
    heave_spring_resolution_nmm: float = 10.0  # iRacing garage steps in 10 N/mm
    rear_spring_resolution_nmm: float = 5.0    # iRacing garage steps in 5 N/mm
    # Perch resolutions differ by control on BMW: front heave is 0.5 mm,
    # rear third is integer-only. Keep the old shared field for compatibility.
    perch_resolution_mm: float = 1.0
    front_heave_perch_resolution_mm: float = 1.0
    rear_third_perch_resolution_mm: float = 1.0
    rear_spring_perch_resolution_mm: float = 0.5  # rear spring perch uses 0.5 mm steps

    # Differential
    diff_preload_nm: tuple[float, float] = (0.0, 150.0)
    diff_preload_step_nm: float = 5.0
    front_diff_preload_nm: tuple[float, float] = (0.0, 0.0)  # default = no front diff
    front_diff_preload_step_nm: float = 5.0
    diff_coast_drive_ramp_options: list[tuple[int, int]] = field(
        default_factory=lambda: [(40, 65), (45, 70), (50, 75)]
    )
    diff_clutch_plates_options: list[int] = field(
        default_factory=lambda: [2, 4, 6]
    )

    # Brakes
    brake_master_cyl_options_mm: list[float] = field(
        default_factory=lambda: [15.9, 16.8, 17.8, 19.1, 20.6, 22.2, 23.8]
    )
    brake_pad_compound_options: list[str] = field(
        default_factory=lambda: ["Low", "Medium", "High"]
    )
    # Brake Bias Target (BBT): Offset from base Brake Pressure Bias.
    # Each click = 0.5% shift. Positive = more forward, negative = more rearward.
    # In-car adjustable (dc parameter). NOT available on BMW M Hybrid V8.
    # Formula: Effective_Bias = BBM + (BBT × 0.5%)
    brake_bias_target: tuple[float, float] = (-5.0, 5.0)
    # Brake Bias Migration (MIG): Dynamic bias shift as function of brake pedal travel.
    # Each click = 1% shift. Positive = bias migrates forward as pedal releases
    # (compensates for downforce loss during deceleration, prevents rear lockup
    # in trail-braking). At full pedal: base bias applies. At zero pedal: full
    # migration offset applies. Linear interpolation between.
    # NOT available on BMW M Hybrid V8. Present on Cadillac, Porsche, Acura, Ferrari.
    # Source: Cadillac V-Series.R GTP User Manual V2, iRacing official.
    brake_bias_migration: tuple[float, float] = (-5.0, 5.0)

    # Fuel
    max_fuel_l: float = 89.0

    # Deflection validation limits (iRacing calculated fields)
    heave_spring_defl_mm: tuple[float, float] = (0.6, 25.0)
    heave_slider_defl_mm: tuple[float, float] = (25.0, 45.0)
    front_torsion_defl_max_mm: float = 24.9
    front_shock_defl_max_mm: float = 19.9
    rear_shock_defl_min_mm: float = 15.0
    rear_spring_defl_max_mm: float = 24.9


@dataclass
class CarModel:
    """Physical model for a GTP/Hypercar car."""

    name: str
    canonical_name: str              # "bmw", "ferrari", etc.

    # Mass properties
    mass_car_kg: float               # Dry car mass
    mass_driver_kg: float = 75.0     # Driver mass
    fuel_density_kg_per_l: float = 0.742  # Fuel density (E10 gasoline)
    fuel_capacity_l: float = 88.96       # Max fuel load (L). All LMDh GTP = 23.5 gal = 88.96L
    fuel_stint_end_l: float = 20.0       # Typical end-of-stint fuel (L)

    # Weight distribution
    weight_dist_front: float = 0.47  # Static front weight fraction (at max fuel)
    # Fuel tank CG position as fraction of wheelbase from the FRONT axle.
    # 0.0 = at front axle, 1.0 = at rear axle, 0.50 = mid-wheelbase.
    # LMDh regulations mandate the fuel cell within the central survival cell,
    # between the axles. For all LMDh cars: p_fuel ≈ 0.50 (central placement).
    # Used by front_weight_at_fuel() to compute dynamic W_f during a stint.
    # The front axle load fraction from fuel = (1.0 - fuel_cg_frac) per simple
    # lever arm: fuel at 0.50 means 50% of fuel weight on front, 50% on rear.
    # Physics-notes.md: 2026-04-02 Topic B — per-10L W_f shift = -0.0018%
    # for BMW (p_fuel=0.50), full-stint shift (89L→0L) ≈ -0.16%.
    fuel_cg_frac: float = 0.50  # Fuel CG position: 0=front axle, 1=rear axle

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

    # Tyre vertical compliance (loaded-radius loss contributes directly to RH)
    tyre_vertical_rate_front_nmm: float = 300.0
    tyre_vertical_rate_rear_nmm: float = 320.0

    # Default DF balance target (%) — car-specific, derived from weight
    # distribution + aero characteristics. Used when no --balance override.
    default_df_balance_pct: float = 50.0

    # Tyre load sensitivity — grip coefficient degrades as vertical load increases.
    # load_sensitivity = 0.0 means linear (no sensitivity).
    # Typical racing tyres: 0.15-0.30. Higher = more grip loss under heavy load.
    # Used by ARB solver to compute optimal LLTD from physics instead of +5% rule.
    tyre_load_sensitivity: float = 0.20

    # Multi-variable ride height prediction model
    ride_height_model: RideHeightModel = field(default_factory=lambda: RideHeightModel())

    # Deflection model for .sto display values
    deflection: DeflectionModel = field(default_factory=lambda: DeflectionModel())

    # iRacing-legal parameter ranges for .sto validation
    garage_ranges: GarageRanges = field(default_factory=GarageRanges)

    # Unified garage-output model (track-specific authoritative garage truth)
    garage_output_model: GarageOutputModel | None = None

    # Default DF balance target (%) — car-specific, used when --balance not set.
    # Derived from weight distribution + aero characteristics.
    default_df_balance_pct: float = 50.14

    # Default diff preload (Nm) — car-specific operating-point baseline.
    # Used by diff_solver as the floor below which preload won't drop. Generic
    # 12 Nm is BMW-derived and far too low for cars like Porsche where the
    # driver-validated preload is 75-100 Nm. Set per-car from telemetry.
    default_diff_preload_nm: float = 12.0

    # Tyre load sensitivity — grip coefficient degradation per unit vertical load.
    # 0.0 = linear (no sensitivity). Typical racing tyres: 0.15–0.30.
    # Used by ARB solver: LLTD_target = Wf + (λ / λ_ref) * 0.05
    # where λ_ref = 0.20 is the calibration point (recovers OptimumG +5% rule).
    tyre_load_sensitivity: float = 0.20

    # Torsion-ARB coupling factor — empirical correction for LLTD model.
    # Standard RCVD parallel-element model gives coupling = 0.0.
    # BMW/Sebring IBT data (73 sessions) required γ=0.25 to match measured LLTD=50.99%.
    # May compensate for rocker flex or other non-modelled compliance.
    # DEFAULT = 0.0 (no coupling) — only set to non-zero for cars with IBT calibration.
    # Used by ObjectiveFunction._torsion_arb_coupling_factor().
    torsion_arb_coupling: float = 0.0

    # ── Tyre thermal operating window ────────────────────────────────────────
    # Michelin GTP/Hypercar Pilot Sport Endurance compound (Ken Payne, Michelin NA
    # technical director, Sportscar365 / IMSA GTLM Insider):
    #   Target hot tyre temperature: 180–220 °F = 82–104 °C
    # General iRacing community consensus (simracingsetup.com, Coach Dave):
    #   Peak grip window: 85–105 °C (consistent with Michelin prototype data).
    # Michelin compound naming: "cold," "medium," "hot" — not soft/medium/hard.
    # Compounds are optimised for different ambient/track temperature ranges,
    # not different hardness levels. Selection driven by expected operating temp.
    #
    # Lateral stiffness penalty model (Pacejka MF thermal scaling, arxiv 2305.18422):
    #   Below T_min: Ky degrades ~1.0%/°C (rubber too stiff, poor contact conformance)
    #   Above T_max: Ky degrades ~1.5%/°C (rubber overheats, compound breakdown faster)
    #   Asymmetry: hot degradation is more severe than cold — once hot, cannot recover.
    #   At 20°C below T_min: ~20% lateral grip loss (out-lap / cold tyre condition)
    #   At 10°C above T_max: ~15% lateral grip loss + accelerated wear
    #
    # GTP-specific (Coach Dave, Cadillac manual): NO tyre warmers in GTP class.
    # Cold-tyre risk on out-lap is the primary handling concern for long stints.
    # Tyre warmup to operating window: 1–2 laps depending on track temp + driving style.
    #
    # Source: research/physics-notes.md 2026-03-26 Topic D
    tyre_opt_temp_min_c: float = 82.0   # °C — Michelin 180°F lower bound (Payne, Michelin NA)
    tyre_opt_temp_max_c: float = 104.0  # °C — Michelin 220°F upper bound (Payne, Michelin NA)
    tyre_temp_sens_cold: float = 0.010  # lateral Ky loss per °C below tyre_opt_temp_min_c
    tyre_temp_sens_hot:  float = 0.015  # lateral Ky loss per °C above tyre_opt_temp_max_c

    # Available wing angles
    wing_angles: list[float] = field(default_factory=list)

    # Ferrari 499P indexed-control lookup tables.
    # Provides physical↔index conversion for heave springs and torsion bars.
    # None for all non-Ferrari cars.
    ferrari_indexed_controls: FerrariIndexedControlModel | None = None

    # Explicit track support for calibration authority. If non-empty, the
    # calibration gate blocks unsupported tracks instead of silently reusing
    # another track's evidence.
    supported_track_keys: tuple[str, ...] = field(default_factory=tuple)

    # LLTD target used by the ARB solver. This may come from track-observed
    # hand calibration or a physics formula; proxy-derived values from
    # analyzer/extract.py:lltd_measured must never be persisted back here.
    measured_lltd_target: float | None = None
    lltd_target_source: str = "physics_formula"

    # Shock velocity percentile used for vortex stall margin calculation.
    # P99 is appropriate for bottoming (we must survive worst-case bumps).
    # P95 is more appropriate for vortex burst (sustained floor dynamics,
    # not extreme isolated hits). Using p99 causes false vetoes on real setups.
    # Validation: BMW Sebring, 46 sessions — 43% false veto rate at p99.
    #             Switching to p95 reduces false veto rate to ~5%.
    # Source: objective_validation.md Section 2 + sprint analysis 2026-03-20
    vortex_excursion_pctile: str = "p95"  # "p95" | "p99"

    def __post_init__(self) -> None:
        # Auto-populate garage_ranges discrete torsion OD options from corner spring model
        if not self.garage_ranges.front_torsion_od_discrete and self.corner_spring.front_torsion_od_options:
            self.garage_ranges.front_torsion_od_discrete = list(self.corner_spring.front_torsion_od_options)

    def total_mass(self, fuel_load_l: float) -> float:
        """Total car mass including driver and fuel (kg)."""
        return self.mass_car_kg + self.mass_driver_kg + fuel_load_l * self.fuel_density_kg_per_l

    def front_weight_at_fuel(self, fuel_load_l: float) -> float:
        """Dynamic front weight fraction at a given fuel load.

        fuel_cg_frac is the CG *position* as fraction of wheelbase from
        the front axle (0.0 = front, 1.0 = rear). The front axle load
        fraction contributed by the fuel mass is therefore:
            fuel_front_frac = 1.0 - fuel_cg_frac

        Computes W_f(fuel) using:
            W_f_dry = (W_f_full * m_full - m_fuel_full * fuel_front_frac) / m_dry
            W_f(fuel) = (W_f_dry * m_dry + m_fuel * fuel_front_frac) / m_total

        For BMW M Hybrid V8 (fuel_cg_frac=0.50 → fuel_front_frac=0.50):
          - Full tank (89L): W_f = 47.27%  (matches IBT calibration)
          - Empty tank (0L): W_f = 47.11%
          - Shift per 10L burned: ~-0.018% (rearward as fuel burns)
          - Full-stint shift: ~-0.16% (small, but correct for theory)

        Note: The shift direction is rearward because fuel_front_frac (0.50) >
        W_f_full (0.4727). Burning fuel removes mass from slightly ahead of the
        vehicle CG → car balance shifts rearward.

        Args:
            fuel_load_l: Current fuel load in litres (clamped to [0, max_fuel]).

        Returns:
            Front weight fraction [0.0–1.0] at the given fuel load.
        """
        max_fuel = self.garage_ranges.max_fuel_l
        fuel_load_l = max(0.0, min(fuel_load_l, max_fuel))

        fuel_front_frac = 1.0 - self.fuel_cg_frac  # CG position → front load fraction

        m_fuel_max = max_fuel * self.fuel_density_kg_per_l
        m_dry = self.mass_car_kg + self.mass_driver_kg
        m_full = m_dry + m_fuel_max
        # Back-derive dry front fraction from calibrated full-tank value
        w_f_dry = (self.weight_dist_front * m_full - m_fuel_max * fuel_front_frac) / max(m_dry, 1e-9)
        m_fuel = fuel_load_l * self.fuel_density_kg_per_l
        m_total = m_dry + m_fuel
        result = (w_f_dry * m_dry + m_fuel * fuel_front_frac) / max(m_total, 1e-9)
        return max(0.0, min(result, 1.0))

    def active_garage_output_model(self, track_name: str | None = None) -> GarageOutputModel | None:
        """Return the authoritative garage-output model for the given track."""
        model = self.garage_output_model
        if model is None:
            return None
        if model.applies_to_track(track_name):
            return model
        return None

    def supports_track(self, track_name: str | None) -> bool:
        """Return whether this car has explicit calibration support for the track."""
        if not self.supported_track_keys:
            return True
        if not track_name:
            return False
        from car_model.registry import track_key

        return track_key(track_name) in set(self.supported_track_keys)

    def supported_tracks_label(self) -> str:
        """Human-readable list of supported base tracks."""
        if not self.supported_track_keys:
            return "all tracks"
        return ", ".join(self.supported_track_keys)

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

    def rh_excursion_p99(
        self,
        shock_vel_p99_mps: float,
        *,
        axle: str = "front",
        spring_rate_nmm: float | None = None,
        damper_coeff_nsm: float | None = None,
    ) -> float:
        """Estimate p99 ride-height excursion (mm) using the shared vertical model.

        This uses the BMW-calibrated effective heave mass and a baseline axle
        spring/damper state so Step 1 and Step 2 are at least directionally
        consistent about how bumps consume ride-height budget.
        """
        is_front = axle.lower().startswith("f")
        if is_front:
            m_eff = self.heave_spring.front_m_eff_kg
            k_nmm = spring_rate_nmm if spring_rate_nmm is not None else self.front_heave_spring_nmm
            c_nsm = damper_coeff_nsm if damper_coeff_nsm is not None else self.damper.front_hs_coefficient_nsm
            tyre_rate_nmm = self.tyre_vertical_rate_front_nmm
        else:
            m_eff = self.heave_spring.rear_m_eff_kg
            k_nmm = spring_rate_nmm if spring_rate_nmm is not None else self.rear_third_spring_nmm
            c_nsm = damper_coeff_nsm if damper_coeff_nsm is not None else self.damper.rear_hs_coefficient_nsm
            tyre_rate_nmm = self.tyre_vertical_rate_rear_nmm

        return damped_excursion_mm(
            shock_vel_p99_mps,
            m_eff,
            k_nmm,
            tyre_vertical_rate_nmm=tyre_rate_nmm,
            damper_coeff_nsm=c_nsm,
        )

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

        # Check actual calibration state from the car object (set by apply_to_car at runtime)
        flags: dict[str, str] = {}

        # Aero compression: check is_calibrated flag (set by apply_to_car from models.json)
        flags["aero_compression"] = (
            "calibrated" if self.aero_compression.is_calibrated else "ESTIMATE"
        )
        # Ride height model: check is_calibrated flag
        flags["pushrod_geometry"] = (
            "calibrated" if self.ride_height_model.is_calibrated else "ESTIMATE"
        )
        # m_eff: no explicit flag, but check if value looks like a non-default (> 100 kg)
        flags["m_eff_front"] = (
            "calibrated" if self.heave_spring.front_m_eff_kg > 100 else "ESTIMATE — needs IBT calibration"
        )
        flags["m_eff_rear"] = (
            "calibrated" if self.heave_spring.rear_m_eff_kg > 100 else "ESTIMATE — needs IBT calibration"
        )
        # Roll gains: check roll_gains_calibrated flag
        flags["front_roll_gain"] = (
            "calibrated" if self.geometry.roll_gains_calibrated else "ESTIMATE — needs IBT calibration"
        )
        flags["rear_roll_gain"] = (
            "calibrated" if self.geometry.roll_gains_calibrated else "ESTIMATE — needs IBT calibration"
        )

        return flags


# ─── Car definitions ─────────────────────────────────────────────────────────

BMW_M_HYBRID_V8 = CarModel(
    name="BMW M Hybrid V8",
    canonical_name="bmw",
    supported_track_keys=("sebring",),
    mass_car_kg=1050.3,       # Calibrated from 41 sessions (corner weights)
    mass_driver_kg=75.0,
    weight_dist_front=0.4727,  # Calibrated from 41 sessions (corner weights) at full fuel
    fuel_cg_frac=0.50,        # LMDh reg: central fuel cell. Analysis: per-10L W_f shift=-0.018%
    brake_bias_pct=46.0,      # Calibrated: IBT=46.0%, S1=46.5%, S2=46.0%
    default_df_balance_pct=50.14,  # Validated from BMW Sebring telemetry
    tyre_load_sensitivity=0.22,    # BMW Michelin GTP compound — moderate sensitivity
    torsion_arb_coupling=0.25,     # Back-calibrated from 73 IBT sessions at Sebring (LLTD=50.99%)
    # IBT-calibrated LLTD target: 46 BMW Sebring sessions show 38-43% actual balance.
    # Theoretical W_front + λ*0.05 = 0.4727 + 0.055 = 0.528 is ~10-14% too high.
    # This override cuts false LLTD penalty by ~10x for real BMW setups.
    # Source: validation/objective_validation.md Section 6, March 2026.
    measured_lltd_target=0.41,    # Track-observed hand calibration, midpoint of 38-43% range
    lltd_target_source="track_observation",
    vortex_excursion_pctile="p95", # p99 caused 43% false veto rate on real BMW setups
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
        is_calibrated=True,
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
        is_calibrated=True,
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
        front_spring_range_nmm=(0.0, 900.0),
        front_realistic_range_nmm=(30.0, 100.0),  # BMW Sebring competitive window
        rear_spring_range_nmm=(100.0, 900.0),
        sigma_target_mm=10.0,   # SKILL.md: sigma > 5mm at >200 kph = unstable
        perch_offset_front_baseline_mm=-13.0,
        perch_offset_rear_baseline_mm=42.0,  # Verified from 2026-03-11 session
        front_heave_hard_range_nmm=(0.0, 900.0),
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
        front_size_labels=["Disconnected", "Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[0.0, 5500.0, 11000.0, 16500.0],
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
        is_calibrated=True,
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
        roll_gains_calibrated=True,
    ),
    damper=DamperModel(
        # BMW damper scale — all clicks max at 11. Different from Ferrari.
        # Do NOT transfer values between cars.
        # Verified from real LDX: HS slope 11, HS Rbd rear 11 (at max).
        ls_comp_range=(0, 11),
        ls_rbd_range=(0, 11),
        hs_comp_range=(0, 11),
        hs_rbd_range=(0, 11),
        hs_slope_range=(0, 11),
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
        # IBT-calibrated damping ratio targets (top-15 fastest BMW/Sebring sessions, 2026-03-26)
        zeta_ls_comp=0.68,
        zeta_hs_comp=0.23,
        zeta_ls_rbd=0.47,
        zeta_hs_rbd=0.20,
        zeta_is_calibrated=True,
        # Per-axle targets for objective scoring (from same IBT calibration)
        zeta_target_ls_front=0.68,
        zeta_target_ls_rear=0.47,
        zeta_target_hs_front=0.23,
        zeta_target_hs_rear=0.20,
    ),
    ride_height_model=RideHeightModel(
        is_calibrated=True,
        # Calibrated from 31 unique BMW Sebring configs (41 sessions, March 2026).
        # Front model (6 features): R²=0.15, RMSE=0.16mm — front nearly pinned at 30mm
        front_intercept=30.5834,
        front_coeff_heave_nmm=-0.002137,
        front_coeff_camber_deg=0.236605,
        front_loo_rmse_mm=0.163,
        # Rear model (6 features): R²=0.52, RMSE=0.68mm, MaxErr=2.1mm
        rear_intercept=48.9601,
        rear_coeff_pushrod=0.226407,
        rear_coeff_third_nmm=0.010214,
        rear_coeff_rear_spring=0.010012,
        rear_coeff_heave_perch=0.138723,
        rear_coeff_fuel_l=-0.005877,
        rear_coeff_spring_perch=0.068718,
        rear_r_squared=0.5155,
        rear_loo_rmse_mm=0.675,
    ),
    garage_output_model=GarageOutputModel(
        name="BMW Sebring garage truth",
        track_keywords=("sebring",),
        default_front_pushrod_mm=-25.5,
        default_rear_pushrod_mm=-29.0,
        default_front_heave_nmm=50.0,
        default_front_heave_perch_mm=-13.0,
        default_rear_third_nmm=530.0,
        default_rear_third_perch_mm=42.0,
        default_front_torsion_od_mm=13.9,
        default_rear_spring_nmm=170.0,
        default_rear_spring_perch_mm=30.0,
        default_front_camber_deg=-2.9,
        default_front_shock_defl_max_mm=100.0,
        default_rear_shock_defl_max_mm=150.0,
        front_rh_floor_mm=30.0,
        max_slider_mm=45.0,
        min_static_defl_mm=3.0,
        max_torsion_bar_defl_mm=25.0,
        torsion_bar_defl_safety_margin_mm=0.2,
        torsion_bar_rate_c=0.0008036,
        heave_spring_defl_max_intercept_mm=96.019667,
        heave_spring_defl_max_slope=-0.082843,
        # Front RH: R²=0.896, RMSE=0.174mm (N=38, 2026-03-16)
        front_intercept=31.637911,
        front_coeff_pushrod=0.028537,
        front_coeff_heave_nmm=0.003811,
        front_coeff_heave_perch_mm=-0.008468,
        front_coeff_torsion_od_mm=-0.045199,
        front_coeff_camber_deg=0.161470,
        front_coeff_fuel_l=-0.001455,
        # Rear RH: R²=0.914, RMSE=0.295mm (N=51, refit 2026-03-17)
        rear_intercept=100.177587,
        rear_coeff_pushrod=0.362407,
        rear_coeff_third_nmm=0.013013,
        rear_coeff_third_perch_mm=-0.820799,
        rear_coeff_rear_spring_nmm=0.038857,
        rear_coeff_rear_spring_perch_mm=-0.621372,
        rear_coeff_front_heave_perch_mm=0.031530,
        rear_coeff_fuel_l=-0.007617,
        torsion_turns_intercept=0.113040865,
        torsion_turns_coeff_heave_nmm=-0.000161078,
        torsion_turns_coeff_heave_perch_mm=0.000540572,
        torsion_turns_coeff_torsion_od_mm=-0.001730502,
        torsion_turns_coeff_front_rh_mm=0.000862398,
        heave_defl_intercept=71.067844,
        heave_defl_coeff_heave_nmm=-0.015976,
        heave_defl_coeff_heave_perch_mm=-0.937587,
        heave_defl_coeff_torsion_od_mm=-4.015412,
        heave_defl_coeff_front_pushrod_mm=0.336211,
        heave_defl_coeff_front_rh_mm=-0.310258,
        heave_defl_coeff_torsion_turns=0.0,
        # Slider: R²=0.802, RMSE=1.128mm (N=38, 2026-03-16)
        slider_intercept=92.921639,
        slider_coeff_heave_nmm=-0.013364,
        slider_coeff_heave_perch_mm=0.117376,
        slider_coeff_torsion_od_mm=-3.387887,
        slider_coeff_front_pushrod_mm=0.0,
        slider_coeff_front_rh_mm=0.0,
        slider_coeff_torsion_turns=0.0,
        # BMW is the historical calibration source — the dataclass defaults ARE
        # the BMW Sebring regression coefficients. Pass is_calibrated=True
        # explicitly so that any cross-car copy of this construction pattern
        # does not silently inherit a "calibrated" flag.
        deflection=DeflectionModel(is_calibrated=True),
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    garage_ranges=GarageRanges(
        # BMW iRacing legal limits (verified 2026-03-18 by Taylor Funk)
        camber_front_deg=(-2.9, 0.0),   # max negative: -2.9°
        camber_rear_deg=(-1.9, 0.0),    # max negative: -1.9°
        front_heave_perch_resolution_mm=0.5,
        rear_third_perch_resolution_mm=1.0,
    ),
)


# ─── Cadillac V-Series.R ─────────────────────────────────────────────────────
# Dallara LMDh chassis (shared platform with BMW, Acura)
# Naturally aspirated 5.5L V8. Best all-rounder.
# Parameter structure matches BMW exactly (same Dallara platform).
# No verified telemetry calibration — values marked ESTIMATE.

CADILLAC_VSERIES_R = CarModel(
    name="Cadillac V-Series.R",
    canonical_name="cadillac",
    supported_track_keys=("silverstone",),
    mass_car_kg=1030.0,           # GTP minimum — confirmed same as BMW
    mass_driver_kg=75.0,
    weight_dist_front=0.485,      # CALIBRATED: IBT corner weights 5500/(5500+5840 N)
    brake_bias_pct=47.5,          # CALIBRATED: IBT BrakePressureBias = 47.5%
    default_df_balance_pct=52.0,  # CALIBRATED from aero map sweep: at dyn front RH 21.5mm,
                                    # min achievable balance is 51.9% (wing 12) → 48.9% (wing 17).
                                    # 50.14% (BMW baseline) was unachievable at wing 12-14.
                                    # 52.0% is safely within range for all wing settings.
    tyre_load_sensitivity=0.20,   # ESTIMATE — Michelin GTP compound (Dallara platform)
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
    rear_third_spring_nmm=600.0,  # UPDATED: 680 N/mm observed at Silverstone; anchor raised from 530
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=12.0,  # CALIBRATED: learner mean 11.98mm across 2 sessions
        rear_compression_mm=18.5,   # CALIBRATED: learner mean 18.53mm; was 8mm ESTIMATE (2.3× underestimate)
        is_calibrated=True,
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,            # Target front static RH
        front_pushrod_default_mm=-25.0,     # Reference pushrod for front_base_rh_mm
        front_pushrod_to_rh=1.28,           # CALIBRATED: LS fit over 4 garage data points
        front_base_rh_mm=41.34,            # CALIBRATED: model prediction at (pushrod=-25, perch=-20.5)
        front_heave_perch_to_rh=-1.955,    # CALIBRATED: LS fit over 4 garage data points
        front_heave_perch_ref_mm=-20.5,    # Reference heave perch for front_base_rh_mm
        rear_base_rh_mm=46.85,              # CALIBRATED: intercept from IBT data
        rear_pushrod_to_rh=0.042,           # CALIBRATED: 2 data points (+0.5→46.8, -6.0→46.6)
                                            # Positive and very weak — DIFFERENT from BMW (-0.096)
                                            # -0.096 gives +2.5mm pushrod; 0.042 gives -6.0mm (correct)
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=266.0,   # CALIBRATED: learner mean 266kg (Cadillac-specific)
        rear_m_eff_kg=2200.0,   # ESTIMATE: Cadillac DPi-derived, lighter rear than BMW; needs heave sweep
        front_spring_range_nmm=(20.0, 200.0),
        rear_spring_range_nmm=(100.0, 1000.0),
        perch_offset_front_baseline_mm=-20.5,   # CORRECTED: from Cadillac PushrodGeometry (was inheriting BMW -13.0)
        perch_offset_rear_baseline_mm=43.0,     # ESTIMATE: close to BMW (42.0) but explicitly set for Cadillac
    ),
    corner_spring=CornerSpringModel(
        # Cadillac uses same Dallara torsion bar front + coil rear
        front_torsion_c=0.0008036,      # Dallara platform — same as BMW verified
        front_torsion_od_ref_mm=13.9,
        front_torsion_od_range_mm=(11.0, 16.0),
        front_torsion_od_options=[13.90, 14.34, 14.76],  # from Cadillac manual (3 discrete options)
        rear_spring_range_nmm=(105.0, 300.0),  # CALIBRATED: 105 N/mm is the Cadillac minimum
        rear_spring_step_nmm=5.0,              # CALIBRATED: 5 N/mm steps (not 10)
        front_motion_ratio=1.0,
        rear_motion_ratio=0.60,         # Dallara geometry — same as BMW confirmed
        track_width_mm=1600.0,          # ESTIMATE — needs Cadillac IBT calibration
        cg_height_mm=350.0,             # ESTIMATE — needs Cadillac IBT calibration
    ),
    arb=ARBModel(
        # Same Dallara chassis as BMW — identical ARB hardware and geometry.
        # Stiffness values propagated from BMW calibrated model.
        front_size_labels=["Disconnected", "Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[0.0, 5500.0, 11000.0, 16500.0],  # Dallara platform — same as BMW
        front_baseline_size="Soft",
        front_baseline_blade=1,
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],     # Dallara platform — same as BMW
        rear_baseline_size="Medium",
        rear_baseline_blade=3,
        front_blade_count=5,
        rear_blade_count=5,
        track_width_front_mm=1730.0,  # Dallara LMDh chassis — same as BMW
        track_width_rear_mm=1650.0,   # Dallara LMDh chassis — same as BMW
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.8,  # CALIBRATED: IBT LeftFront.Camber = -2.8°
        rear_camber_baseline_deg=-1.9,   # CALIBRATED: IBT LeftRear.Camber = -1.9°
        front_roll_gain=0.60,            # Dallara platform — same as BMW
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
    ride_height_model=RideHeightModel.uncalibrated(),
    deflection=DeflectionModel.uncalibrated(),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    # NOTE: Cadillac ride height model is NOT YET CALIBRATED.
    # Front RH depends on: pushrod (payload length), heave perch, torsion bar OD,
    # torsion bar turns, camber, and fuel weight. The PushrodGeometry above provides
    # a 2-variable approximation (pushrod + heave_perch) calibrated from 4 data points.
    # ACCURACY: ±1.5mm. Run auto_calibrate --car cadillac --ibt-dir <dir> with varied setups
    # (different OD, turns, camber, perch) to build a proper 6-variable model.
    # Rear RH depends on: pushrod, third spring rate/perch, rear spring rate/perch,
    # heave perch, fuel. BMW coefficients are completely wrong for Cadillac.
    # Both models will be populated automatically once calibration data is accumulated.
)


# ─── Ferrari 499P indexed-control calibration data ───────────────────────────
# Instantiated here (not inside FERRARI_499P) so it can be imported standalone.

_F = "N/mm"                 # unit shorthand
_VALIDATED = "validated"    # confidence tier: confirmed from IBT / garage screenshot
_ESTIMATED = "estimated"    # confidence tier: derived from analytic fit

FERRARI_499P_INDEXED_CONTROLS = FerrariIndexedControlModel(
    # ── Front heave spring (indices 0–8, physical rate in N/mm) ─────────────
    # Anchor: idx 1 → 50 N/mm (confirmed from IBT sessions Mar19/Mar20).
    # Linear slope: 20 N/mm/idx. Range: 30–190 N/mm.
    # heave_index_unvalidated=True — slope is ESTIMATED until full sweep run.
    front_heave=[
        IndexedLookupPoint(index=0, physical_value=30.0,  unit=_F, confidence=_ESTIMATED, source="extrapolated from anchor idx1=50, slope=20"),
        IndexedLookupPoint(index=1, physical_value=50.0,  unit=_F, confidence=_VALIDATED, source="IBT sessions ferrari_hockenheim Mar19/Mar20"),
        IndexedLookupPoint(index=2, physical_value=70.0,  unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=3, physical_value=90.0,  unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=4, physical_value=110.0, unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=5, physical_value=130.0, unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=6, physical_value=150.0, unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=7, physical_value=170.0, unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
        IndexedLookupPoint(index=8, physical_value=190.0, unit=_F, confidence=_ESTIMATED, source="linear 20 N/mm/idx from anchor"),
    ],
    # ── Rear heave spring (indices 0–9, physical rate in N/mm) ──────────────
    # Anchor: idx 2 → 530 N/mm (confirmed from IBT sessions Mar19/Mar20).
    # Linear slope: 60 N/mm/idx. heave_index_unvalidated=True.
    rear_heave=[
        IndexedLookupPoint(index=0, physical_value=410.0, unit=_F, confidence=_ESTIMATED, source="extrapolated from anchor idx2=530, slope=60"),
        IndexedLookupPoint(index=1, physical_value=470.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=2, physical_value=530.0, unit=_F, confidence=_VALIDATED, source="IBT sessions ferrari_hockenheim Mar19/Mar20"),
        IndexedLookupPoint(index=3, physical_value=590.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=4, physical_value=650.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=5, physical_value=710.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=6, physical_value=770.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=7, physical_value=830.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=8, physical_value=890.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
        IndexedLookupPoint(index=9, physical_value=950.0, unit=_F, confidence=_ESTIMATED, source="linear 60 N/mm/idx from anchor"),
    ],
    # ── Front torsion bar (indices 0–18, physical wheel rate N/mm) ──────────
    # Fit: k^(1/4) = 3.7829 + 0.04201×idx  (6-point garage sweep, max err 5.2%)
    # Calibrated indices: 2, 5, 9, 11, 15, 18.  Others estimated from fit.
    front_torsion=[
        IndexedLookupPoint(index=0,  physical_value=204.7, unit=_F, confidence=_ESTIMATED, source="fit k^(1/4)=3.7829+0.04201*0"),
        IndexedLookupPoint(index=2,  physical_value=220.6, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=12.1mm, corner_weight=2669N"),
        IndexedLookupPoint(index=5,  physical_value=266.9, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=10.0mm, corner_weight=2669N"),
        IndexedLookupPoint(index=9,  physical_value=296.6, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=9.0mm, corner_weight=2669N"),
        IndexedLookupPoint(index=11, physical_value=317.7, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=8.4mm, corner_weight=2669N"),
        IndexedLookupPoint(index=15, physical_value=360.7, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=7.4mm, corner_weight=2669N"),
        IndexedLookupPoint(index=18, physical_value=444.8, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=6.0mm, pure-torsion anchor"),
    ],
    # ── Rear torsion bar (indices 0–18, physical bar rate N/mm) ─────────────
    # Fit: k^(1/4) = 4.3685 + 0.03108×idx  (4-point garage sweep, max err 3.2%)
    # Calibrated indices: 3, 7, 12, 18.  idx 0 estimated from fit.
    rear_torsion=[
        IndexedLookupPoint(index=0,  physical_value=364.2, unit=_F, confidence=_ESTIMATED, source="fit k^(1/4)=4.3685+0.03108*0"),
        IndexedLookupPoint(index=3,  physical_value=399.7, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=7.35mm, corner_weight=2938N"),
        IndexedLookupPoint(index=7,  physical_value=445.2, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=6.6mm, corner_weight=2938N"),
        IndexedLookupPoint(index=12, physical_value=489.7, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=6.0mm, corner_weight=2938N"),
        IndexedLookupPoint(index=18, physical_value=599.6, unit=_F, confidence=_VALIDATED, source="garage screenshot: torsion_defl=4.9mm, corner_weight=2938N"),
    ],
)


# ─── Ferrari 499P ────────────────────────────────────────────────────────────
# Bespoke LMH chassis. 3.0L twin-turbo V6 + 200 kW front hybrid.
# VERY different parameter structure from Dallara:
# - Rear uses torsion bars (indexed OD, not mm)
# - ARBs use letter indices (A, B, C)
# - Damper clicks: 0-40 comp/rbd, 0-11 HS slope (BMW is 0-11 all)
# - Has BOTH front and rear diffs
# Has verified Sebring S1 setup for partial calibration.

FERRARI_499P = CarModel(
    name="Ferrari 499P",
    canonical_name="ferrari",
    supported_track_keys=("sebring",),
    mass_car_kg=1030.0,           # GTP minimum — confirmed same as LMDh
    mass_driver_kg=75.0,
    weight_dist_front=0.476,      # CALIBRATED from IBT corner weights: 2725F/2997R = 47.6%
    brake_bias_pct=49.0,          # VALIDATED: best lap 87.575s SYSTEMS tab screenshot 2026-04-02 → 49.00%
                                  # Prior value 54.0% was from an older IBT session — overridden by garage screenshot
    default_df_balance_pct=48.3,  # CALIBRATED 2026-04-02 from IBT observed operating points:
                                    # 17 Hockenheim sessions at wing=17 run 46.97–48.26% balance naturally.
                                    # Fastest session (87.575s): 47.82%. Mean across session range: 48.3%.
                                    # Prior value of 51.5% was aero-map-theoretical and was biasing the
                                    # solver toward lower wing angles (wing=12 scored 63ms better than
                                    # wing=17 on balance alone — opposite of IBT evidence).
                                    # 48.3% reflects Ferrari's actual high-downforce aero behavior
                                    # at competitive ride heights with high wing angles.
    tyre_load_sensitivity=0.25,   # Ferrari bespoke LMH compound — estimated higher sensitivity
    aero_axes_swapped=True,       # Ferrari aero map uses same axis convention as Dallara
    min_front_rh_static=30.0,
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=2.0,
    front_heave_spring_nmm=50.0,  # ESTIMATE — indexed in reality (index 1 in reference IBTs)
    rear_third_spring_nmm=530.0,  # ESTIMATE — indexed in reality (index 2 in reference IBTs)
    aero_compression=AeroCompression(
        # CALIBRATED from IBT: static 30.1mm - dynamic 15.0mm = 15.1mm front
        # Rear: avg of (47.9-40.0, 48.8-40.0) = 8.3mm
        ref_speed_kph=230.0,
        front_compression_mm=15.1,  # CALIBRATED from IBT aero calculator
        rear_compression_mm=8.3,    # CALIBRATED from IBT aero calculator
        is_calibrated=True,
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,        # iRacing GTP floor — confirmed from IBT (30.1mm)
        front_pushrod_default_mm=2.0,   # VALIDATED: best lap (87.575s) garage screenshot 2026-04-02 → pushrod=+2.0mm, RH=30.1mm
        rear_base_rh_mm=42.5,           # CALIBRATED: intercept from 2 IBTs (12→47.9, 14→48.8)
        rear_pushrod_to_rh=0.45,        # CALIBRATED: slope = (48.8-47.9)/(14-12) = 0.45
        is_calibrated=True,
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=1439.3,  # CALIBRATED from 7 Ferrari IBT sessions (mean, constant model)
        rear_m_eff_kg=1500.0,   # CALIBRATED from 20 Ferrari IBT sessions (Hockenheim+Sebring):
                                # median=1571kg, mean=1833kg. High variance due to pitch coupling
                                # on rear RH std signal. Using conservative 1500kg (below median)
                                # to avoid over-constraining soft rear heave recommendations.
                                # Range observed: 1093-3674kg. Previous BMW value (2870kg) was wrong.
        # CALIBRATED from 5 IBT sessions (Mar19-Mar20): rear heave perch is
        # always negative (-101 to -112.5mm). Default of +43mm (BMW) is wrong.
        # Using -103.5mm from the fastest recent session (Mar20-C, heave idx 7).
        perch_offset_rear_baseline_mm=-104.0,  # VALIDATED: best lap 87.575s garage screenshot 2026-04-02 → rear perch=-104.0mm
        # Ferrari garage exposes raw heave indices, not physical N/mm.
        # Use the existing anchors from observed Ferrari sessions:
        #   front idx 1 ≈ 50 N/mm
        #   rear  idx 2 ≈ 530 N/mm
        # The per-index slopes are approximate until a full Ferrari sweep is run,
        # but they keep the sequential solver monotonic and reversible.
        front_setting_index_range=(0.0, 8.0),
        front_setting_anchor_index=1.0,
        front_rate_at_anchor_nmm=50.0,
        front_rate_per_index_nmm=20.0,
        rear_setting_index_range=(0.0, 9.0),
        rear_setting_anchor_index=2.0,
        rear_rate_at_anchor_nmm=530.0,
        rear_rate_per_index_nmm=60.0,
        heave_index_unvalidated=True,
        front_spring_range_nmm=(30.0, 190.0),  # idx 0 → 30 N/mm, idx 8 → 190 N/mm (validated)
    ),
    corner_spring=CornerSpringModel(
        # Ferrari uses torsion bars for BOTH front and rear (indexed 0-18)
        #
        # ═══════════════════════════════════════════════════════════════════
        # CALIBRATED 2026-03-19/20 from 9 garage screenshots (OD sweep):
        #
        # FRONT TORSION BAR (6-point sweep, corner_weight=2669N always):
        #   OD idx  2: torsion_defl=12.1mm → k=220.6 N/mm
        #   OD idx  5: torsion_defl=10.0mm → k=266.9 N/mm
        #   OD idx  9: torsion_defl= 9.0mm → k=296.6 N/mm
        #   OD idx 11: torsion_defl= 8.4mm → k=317.7 N/mm
        #   OD idx 15: torsion_defl= 7.4mm → k=360.7 N/mm
        #   OD idx 18: torsion_defl= 6.0mm → k=444.8 N/mm ← PURE TORSION (heave_defl=0)
        #   Fit: k^(1/4) = 3.7829 + 0.04201×idx  (max err 5.2% at idx 15)
        #   Implies: C = 0.001282, OD range = 20.0–24.0 mm
        #
        # REAR TORSION BAR (4-point sweep, corner_weight=2938N always):
        #   OD idx  3: torsion_defl=7.35mm → k_bar=399.7 N/mm
        #   OD idx  7: torsion_defl=6.6mm  → k_bar=445.2 N/mm
        #   OD idx 12: torsion_defl=6.0mm  → k_bar=489.7 N/mm
        #   OD idx 18: torsion_defl=4.9mm  → k_bar=599.6 N/mm
        #   Fit: k^(1/4) = 4.3685 + 0.03108×idx  (max err 3.2% at idx 12)
        #   Implies: C = 0.001282 (SAME as front!), OD range = 23.1–26.0 mm
        #   Physical insight: front/rear share identical C constant (same material,
        #   same bar geometry); rear runs thicker OD (23.1 vs 20.0mm at index 0)
        #   → rear is stiffer not because of different geometry but bigger bars.
        #
        # REAR MOTION RATIO (back-solved from IBT LLTD):
        #   Measured LLTD = 50.99% at (front idx 3, rear idx 8, FARB A/1, RARB B/4)
        #   k_bar_rear(8) from 4-pt fit = 454.5 N/mm (was 594.2 with single-point — 31% off)
        #   MR_rear = sqrt(169.9 / 454.5) = 0.612 → LLTD check = 50.9900% ✓ EXACT MATCH
        #   rear_spring_range_nmm stores BAR RATES; rear_motion_ratio converts to wheel rate.
        #
        # HEAVE SPRING NOTES:
        #   Front MR_heave ≈ 1.9 (heave deflects 1.9mm per 1mm of wheel travel)
        #   Front idx 18: heave_defl=0.0mm → confirmed pure-torsion anchor for calibration
        #   Rear heave remains loaded across full OD range (large -112.5mm perch preload)
        # ═══════════════════════════════════════════════════════════════════
        front_torsion_c=0.001282,           # CALIBRATED: 6-pt front + 4-pt rear share same C
        front_torsion_od_ref_mm=22.0,       # Midpoint of calibrated front OD range
        front_torsion_od_range_mm=(20.0, 24.0),  # CALIBRATED: 6-pt fit (max err 5.2%)
        # 19 discrete ODs for indices 0-18, from k^(1/4) = 3.7829 + 0.04201×idx
        front_torsion_od_options=[
            19.99, 20.21, 20.44, 20.66, 20.88, 21.10, 21.32, 21.55, 21.77, 21.99,
            22.21, 22.43, 22.66, 22.88, 23.10, 23.32, 23.54, 23.77, 23.99,
        ],
        # Rear torsion bar: k_bar(idx) from 4-pt fit, same C=0.001282, OD range 23.1–26.0mm
        # Wheel rate = k_bar × rear_motion_ratio^2 (applied in LLTD and ζ calculations)
        rear_spring_range_nmm=(364.0, 590.0),   # CALIBRATED: bar rates idx 0→18 (4-pt fit)
        rear_spring_step_nmm=1.0,               # Indexed: step by 1
        front_motion_ratio=1.0,    # Front torsion: C already gives wheel rate, MR=1.0
        rear_motion_ratio=0.612,   # CALIBRATED: LLTD back-solve → 50.990% ✓ (was 0.536, corrected by 4-pt rear fit)
        track_width_mm=1600.0,     # ESTIMATE — needs Ferrari IBT calibration
        cg_height_mm=340.0,        # ESTIMATE — LMH rules allow lower CoG than LMDh
        front_setting_index_range=(0.0, 18.0),
        rear_setting_index_range=(0.0, 18.0),
        # ── IBT validation 2026-04-11 ─────────────────────────────────────
        # Previous flag rear_torsion_unvalidated=True was set because of a
        # suspected "3.5x rate error" before PR #57 fixed the index/physical-OD
        # domain mismatch in garage_validator._clamp_step3.  After that fix,
        # IBT-controlled-group analysis (60 sessions, same turns+pushrod,
        # different spring index) confirms the rear bar model is within ~10–22%
        # of the IBT-derived wheel rates:
        #   idx=3: k_wheel_model=150.6 N/mm  vs  IBT k_apparent=144.0 N/mm  (4%)
        #   idx=8: k_wheel_model=174.0 N/mm  vs  IBT k_apparent=148–197 N/mm (~10%)
        # Front torsion (same C=0.001282) validated to 2% at idx=2 (220.6 vs 224).
        # Model accuracy is comparable to other calibrated cars; blocking Steps 2–6
        # is no longer warranted.  The ~20% uncertainty at the extreme indices is
        # flagged implicitly by the weak-calibration provenance path.
        rear_torsion_unvalidated=False,
        # ── REAR TORSION BAR (was missing — Bug fix 2026-03-31) ──────────
        # Ferrari rear IS a torsion bar (not coil spring). Same C constant as front,
        # calibrated from 4-pt garage sweep (indices 3, 7, 12, 18).
        # k^(1/4) = 4.3685 + 0.03108×idx → C = 0.001282, OD range 23.1–26.0 mm.
        rear_torsion_c=0.001282,                    # CALIBRATED: same C as front
        rear_torsion_od_range_mm=(23.1, 26.0),      # CALIBRATED: from 4-pt rear fit
        # 19 discrete ODs for indices 0-18, from k^(1/4) = 4.3685 + 0.03108×idx
        rear_torsion_od_options=[
            23.09, 23.25, 23.42, 23.58, 23.74, 23.91, 24.07, 24.24, 24.40, 24.56,
            24.73, 24.89, 25.06, 25.22, 25.39, 25.55, 25.71, 25.88, 26.04,
        ],
    ),
    arb=ARBModel(
        # Ferrari uses: Disconnected, A, B, C, D, E (6 sizes)
        front_size_labels=["Disconnected", "A", "B", "C", "D", "E"],
        front_stiffness_nmm_deg=[0.0, 3000.0, 6000.0, 9000.0, 12000.0, 15000.0],  # ESTIMATE — provisional 3000 N/mm·deg per step; needs LLTD sweep session to validate
        rear_size_labels=["Disconnected", "A", "B", "C", "D", "E"],
        rear_stiffness_nmm_deg=[0.0, 1500.0, 3000.0, 4500.0, 6000.0, 9000.0],     # ESTIMATE — provisional; 8 Hockenheim sessions show LLTD=0.510±0.002 (too narrow to back-calc stiffness)
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
        # iRacing legality limits for Ferrari 499P
        front_camber_range_deg=(-2.9, 0.0),   # hard garage limit
        rear_camber_range_deg=(-1.9, 0.0),     # hard garage limit (iRacing GTP legal max)
        front_toe_range_mm=(-3.0, 3.0),
        rear_toe_range_mm=(-2.0, 3.0),
        # From verified S1: front camber -2.9°, rear -1.9° CALIBRATED from IBT sessions Mar20A/B/C
        front_camber_baseline_deg=-2.9,
        rear_camber_baseline_deg=-1.8,  # VALIDATED: best lap 87.575s garage screenshot 2026-04-02
        front_toe_baseline_mm=-0.5,   # VALIDATED: best lap 87.575s garage screenshot → toe=-0.5mm
        rear_toe_baseline_mm=0.0,
        front_roll_gain=0.60,         # ESTIMATE
        rear_roll_gain=0.48,          # ESTIMATE
        front_toe_heating_coeff=2.5,
        rear_toe_heating_coeff=1.8,
        camber_is_derived=False,      # Ferrari camber IS user-settable (confirmed: varies -1.3 to -2.9 across 31 sessions)
    ),
    damper=DamperModel(
        # Ferrari damper click scale: 0-40 comp/rbd, 0-11 HS slope (BMW is 0-11 all)
        ls_comp_range=(0, 40),
        ls_rbd_range=(0, 40),
        hs_comp_range=(0, 40),
        hs_rbd_range=(0, 40),
        hs_slope_range=(0, 11),
        hs_slope_rbd_range=(0, 11),  # Ferrari-specific: HS rebound slope
        # Force-per-click: physics-derived from m_eff + typical Hockenheim spring rates.
        # c_crit_front = 2*sqrt(k_front * m_eff_front) = 2*sqrt(90000*1439.3) = 22,763 N·s/m
        # LS: baseline 20 clicks, v_ls=0.05 m/s, target zeta=0.55 → (0.55*22763*0.05)/20 = 31.3
        # HS: baseline 15 clicks, v_hs=0.50 m/s, target zeta=0.20 → (0.20*22763*0.50)/15 = 151.8
        # IMPROVED ESTIMATE — derived from m_eff=1439kg + front heave idx=3 (90 N/mm, typical Hockenheim)
        # Needs dedicated click-sweep IBT session (vary LS/HS clicks, measure suspension freq) to validate.
        ls_force_per_click_n=31.3,   # IMPROVED ESTIMATE: physics-derived (was 7.0 — too small)
        hs_force_per_click_n=151.8,  # IMPROVED ESTIMATE: physics-derived (was 30.0 — too small)
        # Baseline clicks derived from force_per_click + zeta targets:
        # LS front: zeta=0.55, c=0.55*22763=12520 N·s/m, F_ls=31.3*20=626 N @ v=0.05 → consistent ✓
        # HS front: zeta=0.20, c=0.20*22763=4553 N·s/m, F_hs=151.8*15=2277 N @ v=0.50 → consistent ✓
        # Rear: spring stiffer (530 N/mm vs 90), c_crit_rear=56391 → more clicks needed for same zeta
        # IMPROVED ESTIMATE — all baselines are physics-consistent (not BMW copies)
        # zeta_is_calibrated=False — targets are physics estimates (m_eff+spring at typical Hockenheim RH)
        # force_per_click derived: ls=31.3 N/click (baseline 20/40), hs=151.8 (baseline 15/40)
        # Needs dedicated click-sweep IBT session to validate
        # VALIDATED from IBT: Hockenheim best lap 87.575s (2026-04-02 garage screenshot)
        # Front corner dampers run at minimum — ground effect aero provides front stability,
        # mechanical damping at front is counterproductive at high DF levels.
        # Rear corner dampers maxed — controls rear platform / prevents rear aero bounce.
        front_ls_comp_baseline=0,   # VALIDATED: IBT best lap — front corner nearly undamped
        front_ls_rbd_baseline=0,    # VALIDATED: IBT best lap
        front_hs_comp_baseline=0,   # VALIDATED: IBT best lap
        front_hs_rbd_baseline=0,    # VALIDATED: IBT best lap
        front_hs_slope_baseline=7,  # VALIDATED: IBT best lap
        rear_ls_comp_baseline=40,   # VALIDATED: IBT best lap — rear maxed out (full compression control)
        rear_ls_rbd_baseline=35,    # VALIDATED: IBT best lap
        rear_hs_comp_baseline=40,   # VALIDATED: IBT best lap — rear HS also maxed
        rear_hs_rbd_baseline=0,     # VALIDATED: IBT best lap — rear HS rbd at minimum
        rear_hs_slope_baseline=10,  # VALIDATED: IBT best lap
        # Damper coefficients for physics calculations (c_crit, zeta).
        # Scaled from BMW calibrated values by sqrt(k_ratio * m_ratio):
        # scale = sqrt(k_ferrari/k_bmw * m_ferrari/m_bmw) = sqrt(90/50 * 1439/228) = sqrt(11.36) = 3.37
        # BMW front LS=5060, HS=2586; BMW rear LS=4358, HS=2034.
        # IMPROVED ESTIMATE — scaled from BMW; needs Ferrari-specific click-sweep to validate.
        front_ls_coefficient_nsm=17050,  # IMPROVED ESTIMATE: BMW 5060 * 3.37 (k+m scaling)
        front_hs_coefficient_nsm=8714,   # IMPROVED ESTIMATE: BMW 2586 * 3.37
        rear_ls_coefficient_nsm=14627,   # IMPROVED ESTIMATE: BMW 4358 * 3.37 (rear scaling)
        rear_hs_coefficient_nsm=6855,    # IMPROVED ESTIMATE: BMW 2034 * 3.37
        # Ferrari has separate heave dampers
        has_heave_dampers=True,
        front_heave_baseline={"ls_comp": 10, "hs_comp": 40, "ls_rbd": 5, "hs_rbd": 10, "hs_slope": 40},
        rear_heave_baseline={"ls_comp": 10, "hs_comp": 40, "ls_rbd": 5, "hs_rbd": 10, "hs_slope": 40},
    ),
    garage_ranges=GarageRanges(
        damper_click=(0, 40),
        front_pushrod_mm=(-40.0, 40.0),
        rear_pushrod_mm=(-40.0, 40.0),
        front_heave_nmm=(0.0, 8.0),            # indexed 0-8 (not N/mm)
        rear_third_nmm=(0.0, 9.0),             # rear heave spring indexed 0-9 (no third spring)
        front_heave_perch_mm=(-150.0, 100.0),
        rear_third_perch_mm=(-150.0, 100.0),   # rear heave perch offset
        front_torsion_od_mm=(0.0, 18.0),        # indexed 0-18 (not mm)
        rear_spring_nmm=(0.0, 18.0),            # rear torsion bar OD indexed 0-18 (no coil spring)
        rear_spring_perch_mm=(0.0, 0.0),        # N/A — Ferrari has no rear coil spring perch
        arb_blade=(1, 5),
        # iRacing legality limits for Ferrari 499P
        camber_front_deg=(-2.9, 0.0),           # hard garage limit
        camber_rear_deg=(-1.9, 0.0),            # hard garage limit (iRacing GTP legal max)
        toe_front_mm=(-3.0, 3.0),
        toe_rear_mm=(-2.0, 3.0),
        torsion_bar_turns_range=(-0.250, 0.250),  # Ferrari has torsion bar turns at all 4 corners
        brake_bias_migration=(-6.0, 6.0),
        diff_clutch_plates_options=[2, 4, 6],
        front_diff_preload_nm=(-50.0, 50.0),    # Ferrari has front AND rear diffs
        front_diff_preload_step_nm=5.0,
        heave_spring_resolution_nmm=1.0,        # indexed: step by 1
        rear_spring_resolution_nmm=1.0,         # rear torsion bar OD: step by 1
        front_heave_perch_resolution_mm=0.5,
        rear_third_perch_resolution_mm=0.5,
        torsion_bar_turns_resolution=0.125,     # 1/8 turn steps
    ),
    ride_height_model=RideHeightModel(
        # CALIBRATED from 22 unique Ferrari setups (data/calibration/ferrari/models.json)
        is_calibrated=True,
        # Front model (6 features): R²=0.696, RMSE=0.65mm
        front_intercept=29.828,
        front_coeff_heave_nmm=0.0405,    # mm RH per unit heave setting
        front_coeff_camber_deg=0.864,    # mm RH per deg front camber
        front_loo_rmse_mm=1.044,
        # Rear model (6 features): R²=0.613, RMSE=1.28mm
        rear_intercept=22.868,
        rear_coeff_pushrod=0.274,        # mm RH per mm pushrod offset
        rear_coeff_third_nmm=0.031,      # mm RH per unit rear heave setting
        rear_coeff_rear_spring=0.152,    # mm RH per unit rear torsion setting
        rear_coeff_heave_perch=-0.168,   # mm RH per mm rear heave perch
        rear_coeff_fuel_l=0.031,         # mm RH per L fuel
        rear_coeff_spring_perch=-0.091,  # mm RH per mm rear spring perch
        rear_r_squared=0.613,
        rear_loo_rmse_mm=4.764,
    ),
    deflection=DeflectionModel(
        # CALIBRATED from 22 unique Ferrari setups (data/calibration/ferrari/models.json)
        # Ferrari-specific models — NOT BMW coefficients.
        is_calibrated=True,
        # Shock deflection: front/rear from pushrod regression
        shock_front_intercept=15.564,
        shock_front_pushrod_coeff=0.191,    # R²=0.65
        shock_rear_intercept=35.939,
        shock_rear_pushrod_coeff=0.129,     # R²=0.60
        # Torsion bar deflection: load model from heave + perch regression (R²=0.97)
        tb_load_intercept=-91638.1,
        tb_load_heave_coeff=17990.2,
        tb_load_perch_coeff=-4206.3,
        # Heave spring deflection static: quadratic model (R²=0.88)
        heave_defl_intercept=4.288,
        heave_defl_inv_heave_coeff=0.735,   # linear heave term
        heave_defl_perch_coeff=0.341,       # linear perch term
        heave_defl_inv_od4_coeff=0.0,       # not used in Ferrari model
        # Heave slider: from heave + perch + torsion (R²=0.54)
        slider_intercept=44.058,
        slider_heave_coeff=0.279,
        slider_perch_coeff=0.237,
        slider_od_coeff=-0.837,
        # Rear spring: force-balance from perch (R²=0.88)
        rear_spring_eff_load=36858.5,       # rear_spring_defl_static_load model
        rear_spring_perch_coeff=73050.2,
        # Third spring: force-balance from perch (R²=0.90)
        third_spring_eff_load=2282.3,       # third_spring_defl_static_load model
        third_spring_perch_coeff=21.856,
        # Third slider: from third spring defl (R²=0.007 — near useless)
        third_slider_intercept=23.599,
        third_slider_spring_defl_coeff=-0.064,
        # Rear spring max: from spring + perch (R²=0.99)
        rear_spring_defl_max_intercept=-2.917,
        rear_spring_defl_max_rate_coeff=0.500,
        rear_spring_defl_max_perch_coeff=-0.132,
        # Third spring max: from third + perch (R²=0.76)
        third_spring_defl_max_intercept=89.781,
        third_spring_defl_max_rate_coeff=-0.101,
        third_spring_defl_max_perch_coeff=0.186,
    ),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    ferrari_indexed_controls=FERRARI_499P_INDEXED_CONTROLS,
    measured_lltd_target=0.510,  # CALIBRATED: mean of 19 sessions (Hockenheim+Sebring).
                                  # Range 0.508-0.514, stdev 0.0016. Theoretical formula = 0.475 (WRONG).
                                  # CRITICAL: Ferrari LLTD is effectively CONSTANT at 0.510±0.002
                                  # despite torsion bars ranging idx 2-8 and ARBs from A/1 to E/5.
                                  # The front/rear torsion bars scale proportionally, locking LLTD.
                                  # DO NOT attempt to optimize LLTD via torsion/ARB changes.
    lltd_target_source="track_observation",
    torsion_arb_coupling=0.0,    # Ferrari torsion bars scale proportionally front/rear — LLTD effectively constant.
    # ── SYSTEMS tab VALIDATED (87.575s best lap, 2026-04-02 screenshot) ─────────────────
    # Hybrid: rear drive enabled, corner pct = 90% (strong rear bias in corners)
    # Brake: pad=Low, front MC=17.8mm, rear MC=19.1mm, bias=49.0%, migration=1, gain=0.00
    # TC: TC2=3 (gain), TC1=4 (slip)
    # Front diff: preload = 5 Nm
    # Rear diff: More Locking, friction plates = 6, preload = 20 Nm
    # Gear stack: Short — speeds (km/h): 1st=121.7, 2nd=157.5, 3rd=190.0, 4th=222.7, 5th=256.6, 6th=291.0, 7th=329.2
    # (Hockenheim is primarily 3rd–5th gear — braking from 291 km/h at end of main straight into stadium)
    # ─────────────────────────────────────────────────────────────────────────────────────
                                  # Lower than BMW 0.25 (calibrated) because Ferrari's indexed torsion bars
                                  # are stiffer → smaller coupling fraction. Needs ARB+OD sweep to validate.
                                  # Standard parallel model (RCVD) applies. γ=0.25 was BMW-only.
)


# ─── Porsche 963 ─────────────────────────────────────────────────────────────
# Multimatic LMDh chassis (NOT Dallara). DSSV dampers (spool valve, not shims).
# Aero-dominant car. Highest top speed. Best traction.
# Same parameter naming as BMW/Cadillac but different platform response.

PORSCHE_963 = CarModel(
    name="Porsche 963",
    canonical_name="porsche",
    supported_track_keys=("algarve",),
    mass_car_kg=1030.0,
    mass_driver_kg=75.0,
    # fuel_capacity_l=88.96 (class default, same as all LMDh GTP — 23.5 gal)
    weight_dist_front=0.471,  # CALIBRATED: from corner weights (2689+2689)/(2689*2+3015*2) = 0.4714
    default_df_balance_pct=46.8,  # CALIBRATED 2026-04-07 from 4 Algarve IBTs (Setup A heavy 320,
    #   Setups B HOT 160 across sessions 13-26-10/13-59-01/14-23-44). Driver-achieved aero balance
    #   at the brake-off >150 kph operating point: A=47.19%, B=46.63/46.60/46.65% (best lap 92.99s).
    #   Median = 46.65%; rounded to 46.8% to give the rake solver slight rear-bias headroom.
    #   Old 50.5% is mathematically unreachable at the sim-min front (30 mm) because the
    #   aero map (axes-swap honored) at dyn_F=17.6 caps at 52.5% balance only at dyn_R≈49.8 mm,
    #   which corresponds to static_R ≈66 mm — beyond the +40 mm rear pushrod cap of 50.6 mm.
    #   The old value forced the rake solver to hit the rear pushrod cap on every run.
    tyre_load_sensitivity=0.18,   # DSSV dampers give better contact — lower effective sensitivity
    brake_bias_pct=44.75,         # CALIBRATED: from user's Algarve baseline (was 46.0 BMW default)
    # LLTD target: PHYSICS-DERIVED via OptimumG/Milliken formula
    # = weight_dist_front + (tyre_sens/0.20) × 0.05 + speed_correction
    # = 0.471 + (0.18/0.20) × 0.05 + ~0.005 = 0.521
    # NOTE 2026-04-07: previously loaded from data/calibration/porsche/models.json
    # which derived it from analyzer/extract.py:roll_distribution_proxy — but
    # that field is a GEOMETRIC PROXY (= t_f³/(t_f³+t_r³) ≈ 0.536 for Porsche),
    # NOT a real LLTD measurement. Verified across 5 IBTs with rear stiffness
    # varying 300%: proxy varied <0.1 pp. Storing this as the LLTD target
    # caused a fake 11 pp model gap and triggered the ARB driver-anchor
    # fallback unnecessarily. Now uses physics formula explicitly. The
    # auto_calibrate "lltd_target" path is disabled (see auto_calibrate.py:1360).
    measured_lltd_target=0.521,
    lltd_target_source="physics_formula",
    aero_axes_swapped=True,
    default_diff_preload_nm=85.0,  # CALIBRATED 2026-04-07: driver runs 90 Nm consistently across
    # 4 Algarve IBTs (Setup A heavy + B HOT). Generic 12 Nm (BMW default) produced pipeline output
    # 30 Nm — far too soft for Porsche's diff geometry. 85 Nm sets the floor near driver-validated
    # operating point while leaving room for telemetry-driven downward adjustments.
    min_front_rh_static=30.0,
    max_front_rh_static=80.0,
    min_rear_rh_static=30.0,
    max_rear_rh_static=80.0,
    min_front_rh_dynamic=5.0,
    max_front_rh_dynamic=50.0,
    min_rear_rh_dynamic=25.0,
    max_rear_rh_dynamic=75.0,
    vortex_burst_threshold_mm=8.0,  # CORRECTED: 2mm never bound (dynamic front RH 15-25mm). 8mm matches BMW and provides meaningful ground-effect stall protection.
    front_heave_spring_nmm=180.0,  # Updated from Algarve starting setup (was 50 estimate)
    rear_third_spring_nmm=120.0,  # From user's Algarve baseline (was 80 from initial setup)
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=12.1,  # CALIBRATED: empirical mean from 2 Sebring sessions
        rear_compression_mm=23.3,   # CALIBRATED: empirical mean from 2 Sebring sessions (was 8.0 estimate)
        is_calibrated=True,
    ),
    pushrod=PushrodGeometry(
        front_pinned_rh_mm=30.0,       # CALIBRATED: garage screenshots show RH=30.0 at pushrod=-39.5
        front_pushrod_default_mm=-39.5, # CALIBRATED: from Algarve starting setup
        front_heave_perch_to_rh=-0.678,  # CALIBRATED: -0.678 mm RH per mm perch (from 13-point regression)
        front_heave_perch_ref_mm=58.0,   # Reference perch at which front_base_rh_mm was measured
        rear_base_rh_mm=35.47,         # CALIBRATED: regression intercept from 13 garage screenshots (R^2=0.972)
        rear_pushrod_to_rh=0.0,        # Porsche rear RH does NOT depend on pushrod (regression coefficient ~0). RH controlled by rear_spring + third via RideHeightModel.
        rear_pushrod_default_mm=24.0,  # Porsche baseline from user's Algarve setup (class default -29.0 is BMW)
        front_pushrod_to_rh=0.549,     # CALIBRATED: 0.549 mm_RH / mm_pushrod from 3-point sweep (R^2=1.0)
        is_calibrated=True,
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        front_m_eff_kg=498.0,   # CALIBRATED: empirical from 2 Sebring sessions
        rear_m_eff_kg=1232.0,   # CALIBRATED: back-calculated from user's validated 120 N/mm at Algarve (v_p99=0.23, sigma_target=10mm). SHO formula gives 886 but underestimates due to damper dissipation.
        front_spring_range_nmm=(150.0, 600.0),  # CORRECTED: real garage range 150–600 N/mm
        rear_spring_range_nmm=(80.0, 800.0),     # CORRECTED: min 80 prevents pathological 10 N/mm recommendation (was 0). Real fix: calibrate rear_m_eff_kg.
        slider_intercept=0.0,     # Porsche Multimatic — no BMW-style slider geometry
        slider_heave_coeff=0.0,   # Porsche Multimatic — no BMW-style slider geometry
        perch_offset_front_baseline_mm=58.0,     # CORRECTED: from Algarve starting setup
        perch_offset_rear_baseline_mm=120.5,     # CORRECTED: from Algarve starting setup
        sigma_target_mm=6.0,    # SKILL.md: σ > 5mm at >200 kph = unstable platform.
        #   Previous value 10.0 was so loose the variance constraint never bound,
        #   making the solver bottoming-constrained only (always picks softest spring).
        #   6.0 is a middle ground: tighter than the 10.0 that never binds, looser
        #   than 5.0 which may overshoot on high-v_p99 sessions.
        #   NOTE 2026-04-07: σ MODEL is sensitive to v_p99_rear_hs which varies 44%
        #   across sessions (0.2387 vs 0.3428 m/s). If this causes overshooting on
        #   high-v_p99 sessions, the deeper fix is a track-surface-derived σ reference.
        # Porsche internal geometry is NOT calibrated — set to 0 to use physics-only path
        # (BMW defaults would produce wrong travel budget calculations for Multimatic chassis)
        heave_spring_defl_max_intercept_mm=0.0,
        heave_spring_defl_max_slope=0.0,
        slider_perch_coeff=0.0,
        defl_static_intercept=0.0,
        defl_static_heave_coeff=0.0,
    ),
    corner_spring=CornerSpringModel(
        # Porsche Multimatic: NO front torsion bar OD adjustment.
        # Front corner stiffness comes from Roll Spring (100 N/mm in starting setup).
        # The torsion_c and OD options are PLACEHOLDERS — solver should use roll spring.
        front_torsion_c=0.0,    # CORRECTED: Porsche has NO front torsion bar OD selection
        front_torsion_od_ref_mm=0.0,
        front_torsion_od_range_mm=(0.0, 0.0),
        front_torsion_od_options=[],  # CORRECTED: empty — no OD options for Porsche
        front_roll_spring_rate_nmm=100.0,  # Baseline from Algarve setup
        front_roll_spring_range_nmm=(100.0, 320.0),  # Real garage range
        front_is_roll_spring=True,  # Multimatic: single roll spring, not paired corner springs
        front_roll_spring_step_nmm=10.0,
        rear_spring_range_nmm=(105.0, 280.0),  # CORRECTED: real garage range 105–280 N/mm (was 100–400)
        rear_spring_step_nmm=10.0,
        rear_spring_perch_baseline_mm=99.0,  # CALIBRATED: from Algarve sessions (was 30.0 BMW default)
        front_motion_ratio=1.0,
        rear_motion_ratio=0.60,  # ESTIMATE — Multimatic pushrod geometry
        track_width_mm=1600.0,   # ESTIMATE — Multimatic chassis
        cg_height_mm=345.0,      # ESTIMATE — Multimatic slightly lower than Dallara
    ),
    arb=ARBModel(
        # Porsche 963 front ARB: Disconnected/Connected + blade 1-5
        # Multimatic platform — ARB hardware differs from Dallara
        front_size_labels=["Disconnected", "Connected"],
        front_stiffness_nmm_deg=[0.0, 600.0],  # CORRECTED from telemetry: LLTD changes <0.5% across full ARB range — ARBs are very weak on Porsche (was 10000 BMW copy)
        front_baseline_size="Connected",     # Porsche front ARB is Connected/Disconnected toggle
        front_baseline_blade=1,              # IBT: ArbAdj = 1
        rear_size_labels=["Disconnected", "Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[0.0, 150.0, 300.0, 450.0],  # CORRECTED from telemetry: 10x lower than BMW — Porsche ARBs have minimal LLTD effect (was 1500/3000/4500)
        rear_baseline_size="Stiff",          # From Algarve starting setup
        rear_baseline_blade=6,               # CORRECTED: actual baseline from latest sessions (was 2)
        front_blade_count=13,  # CORRECTED: real garage 1–13 (was 5)
        rear_blade_count=16,   # CORRECTED: real garage 1–16 (was 5)
        track_width_front_mm=1700.0,  # ESTIMATE — Multimatic narrower front than Dallara
        track_width_rear_mm=1620.0,   # ESTIMATE — Multimatic narrower rear
        is_calibrated=True,  # CALIBRATED 2026-04-04: LLTD changes <0.5% across full ARB range (Disc->Conn/13 front, Soft->Stiff rear). ARBs are very weak on Porsche Multimatic. Values derived from LLTD response analysis, not RG back-solve (RG too noisy).
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.8,  # From user's Algarve baseline (was -2.9 estimate)
        rear_camber_baseline_deg=-1.8,   # From user's Algarve baseline
        front_toe_baseline_mm=-1.2,      # From user's Algarve baseline (was -0.4 BMW default)
        rear_toe_baseline_mm=-1.6,       # From user's Algarve baseline (was 0.0 BMW default)
        front_roll_gain=0.60,
        rear_roll_gain=0.48,
        roll_gains_calibrated=True,
        # toe_heating_coeff: BMW defaults (2.5 front, 1.8 rear) inherited — only affects
        # conditioning-lap estimates, not toe values. Low priority unless tyre temp predictions off.
    ),
    damper=DamperModel(
        # DSSV spool-valve dampers — more progressive response than shim stacks.
        # Real Porsche damper architecture (4 separate systems):
        #   Front heave: LS comp, HS comp, LS rbd, HS rbd (0–11)
        #   Front roll: LS damping, HS damping, HS damp slope (0–11)
        #   L/R rear: LS comp, HS comp, HS comp slope, LS rbd, HS rbd (0–11)
        #   Rear 3rd: LS comp, HS comp, LS rbd, HS rbd (0–5)
        ls_force_per_click_n=10.0,  # ESTIMATE — DSSV, scaled from BMW range. Needs click-sweep to verify.
        hs_force_per_click_n=45.0,  # ESTIMATE — DSSV, scaled from BMW range. Needs click-sweep to verify.
        has_roll_dampers=True,
        # Porsche 963 (Multimatic) has FRONT roll damper but NO rear roll
        # damper. Rear roll motion is implicit in the per-corner LF/RF/LR/RR
        # shocks. Writing CarSetup_Dampers_RearRoll_* fields to .sto would
        # be invalid (those XML IDs don't exist in iRacing's Porsche schema)
        # and the damper solver shouldn't compute rear roll values either.
        has_front_roll_damper=True,
        has_rear_roll_damper=False,
        roll_ls_range=(0, 11),
        roll_hs_range=(0, 11),
        # WARNING: BMW shim-stack coefficients — DSSV spool valves differ (~1.2-1.35x higher).
        # Damper solver bypasses these (uses calibrated zeta targets from models.json).
        # Heave solver uses HS coefficients for excursion — BMW values produce conservative
        # bias (slightly stiffer springs), which is safer than unvalidated DSSV estimates.
        # TODO: Calibrate via DSSV click-sweep when data available.
        front_ls_coefficient_nsm=5060.0,  # BMW DEFAULT — needs DSSV measurement
        front_hs_coefficient_nsm=2586.0,  # BMW DEFAULT
        rear_ls_coefficient_nsm=4358.0,   # BMW DEFAULT
        rear_hs_coefficient_nsm=2034.0,   # BMW DEFAULT
        # Baselines from user's actual Algarve setup (not BMW S2)
        front_ls_comp_baseline=7,
        front_ls_rbd_baseline=7,
        front_hs_comp_baseline=11,
        front_hs_rbd_baseline=11,
        front_hs_slope_baseline=0,
        rear_ls_comp_baseline=7,
        rear_ls_rbd_baseline=5,
        rear_hs_comp_baseline=10,
        rear_hs_rbd_baseline=10,
        rear_hs_slope_baseline=11,
        front_roll_ls_baseline=8,
        front_roll_hs_baseline=11,
        rear_roll_ls_baseline=0,
        rear_roll_hs_baseline=0,
        # Zeta values loaded dynamically from models.json via apply_to_car()
    ),
    ride_height_model=RideHeightModel(
        # CALIBRATED 2026-04-03 from 13 Algarve garage screenshots
        is_calibrated=True,
        # front_rh = 84.07 + 0.540*pushrod + 0.047*heave + -0.678*perch + 0.668*camber
        # R^2=0.9958, RMSE=0.322mm, n=13
        # Porsche front RH: full 4-variable model (pushrod+heave+perch+camber)
        # front_rh = 84.07 + 0.540*pushrod + 0.047*heave - 0.678*perch + 0.668*camber
        # R²=0.9958, RMSE=0.322mm, n=13
        front_intercept=84.0715,        # regression intercept
        front_coeff_heave_nmm=0.04742,  # CALIBRATED: +0.047 mm_RH per N/mm heave
        front_coeff_camber_deg=0.6676,  # CALIBRATED: +0.668 mm_RH per deg camber
        front_coeff_pushrod=0.5401,     # CALIBRATED: +0.540 mm_RH per mm pushrod
        front_coeff_perch=-0.6780,      # CALIBRATED: -0.678 mm_RH per mm perch
        front_pushrod_ref_mm=-39.5,     # Reference pushrod for intercept
        front_perch_ref_mm=58.0,        # Reference perch for intercept
        front_loo_rmse_mm=0.322,
        # rear_rh = 35.47 + 0.068*rear_spring + 0.027*rear_third
        # R^2=0.9722, RMSE=0.294mm, n=13
        rear_intercept=35.4672,
        rear_coeff_pushrod=0.0,
        rear_coeff_third_nmm=0.027451,  # CALIBRATED
        rear_coeff_rear_spring=0.068291,# CALIBRATED
        rear_coeff_heave_perch=0.0,
        rear_coeff_fuel_l=-0.0152,      # CALIBRATED: -0.015 mm/L
        rear_coeff_spring_perch=0.0,
        rear_r_squared=0.9722,
        rear_loo_rmse_mm=0.294,
    ),
    deflection=DeflectionModel.uncalibrated(),
    wing_angles=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    garage_ranges=GarageRanges(
        # Porsche 963 real garage ranges (from user-verified iRacing garage, 2026-04-04)
        # Resolution fields use class defaults (0.5mm pushrod, 10 N/mm heave, 1.0mm perch)
        # — assumed same as other GTP cars; verify from garage screenshots if quantization issues arise
        front_torsion_od_mm=(0.0, 0.0),  # Porsche has NO torsion bar — uses roll spring
        front_heave_nmm=(150.0, 600.0),
        front_heave_perch_mm=(40.0, 90.0),
        rear_third_nmm=(0.0, 800.0),
        rear_third_perch_mm=(-150.0, 150.0),
        rear_spring_nmm=(105.0, 280.0),
        rear_spring_perch_mm=(-150.0, 150.0),
        camber_front_deg=(-2.9, 0.0),
        camber_rear_deg=(-1.9, 0.0),
        arb_blade=(1, 16),  # rear has 1–16; front has 1–13 (model uses single range, take wider)
    ),
)


# ─── Acura ARX-06 ──────────��────────────────────���────────────────────────────
# Dallara LMDh chassis (same as BMW, Cadillac). 2.4L twin-turbo V6.
# Sharpest front end in class. Diff preload IS THE setup parameter.
# Narrow wing range (6-10°). Best at technical tracks.

ACURA_ARX06 = CarModel(
    name="Acura ARX-06",
    canonical_name="acura",
    supported_track_keys=("hockenheim",),
    mass_car_kg=1030.0,               # PDF: dry weight 1030 kg
    mass_driver_kg=75.0,
    weight_dist_front=0.470,          # IBT: (2706+2706)/(2706+2706+3048+3048) = 0.470
    default_df_balance_pct=49.0,      # Sharp front end — risk of snap oversteer
    tyre_load_sensitivity=0.20,       # ESTIMATE — Michelin GTP compound
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
    front_heave_spring_nmm=180.0,     # IBT: "180 N/mm" (Hockenheim baseline)
    rear_third_spring_nmm=120.0,      # IBT: "120 N/mm" (Acura calls rear heave "HeaveSpring")
    aero_compression=AeroCompression(
        ref_speed_kph=230.0,
        front_compression_mm=15.0,    # ESTIMATE — needs aero map calibration
        rear_compression_mm=8.0,      # ESTIMATE
    ),
    pushrod=PushrodGeometry(
        # Front RH dominated by CAMBER: front_rh = 37.55 + 2.388*camber (R²=0.988, RMSE=0.18mm)
        # At camber=-2.8: RH≈30.9mm. At camber=-1.4: RH≈34.3mm.
        front_pinned_rh_mm=30.2,         # CALIBRATED: typical at camber=-2.8 (most common)
        front_pushrod_default_mm=-37.5,  # IBT: PushrodLengthDelta = -37.5mm
        # CALIBRATED from 15 unique garage data points (2026-03-30), deduplicated:
        # Full model: rear_rh = 78.54 + 0.7644*pushrod + 0.0406*heave - 0.4089*perch
        # R²=0.908, RMSE=1.21mm, N=13 (with heave+perch), pushrod dominant
        # Front RH model: front_rh = 37.55 + 2.388*camber (R²=0.988, camber dominant)
        # Front damper defl: defl = 25.46 + 0.714*pushrod (R²=0.998, bottoms at pushrod<-35.6mm)
        # PushrodGeometry can only model pushrod; base_rh at typical operating point
        # (heave=150, perch=85): base = 78.54 + 0.0406*150 - 0.4089*85 = 49.87
        rear_base_rh_mm=49.87,           # CALIBRATED: 13-point regression, R²=0.91
        rear_pushrod_to_rh=0.7644,       # CALIBRATED: positive (less negative pushrod = higher RH)
    ),
    rh_variance=RideHeightVariance(dominant_bump_freq_hz=5.0),
    heave_spring=HeaveSpringModel(
        # m_eff varies with spring rate (nonlinear sim model):
        #   Front: 641kg at 90 N/mm, 319kg at 190 N/mm
        #   Rear:  187kg at 60 N/mm, 254kg at 70 N/mm
        # Using mid-range values; solver should ideally use rate-dependent m_eff.
        front_m_eff_kg=450.0,     # CALIBRATED: midpoint of 319-641kg range (garage screenshots)
        rear_m_eff_kg=220.0,      # CALIBRATED: midpoint of 187-254kg range (garage screenshots)
        front_spring_range_nmm=(90.0, 400.0),    # EXPANDED: garage shows 90-380 N/mm range
        front_heave_hard_range_nmm=(90.0, 600.0),  # CORRECTED: garage allows up to 600 N/mm; 400 blocked legal range for bumpy tracks
        rear_spring_range_nmm=(60.0, 300.0),     # Garage shows 60-190 N/mm; cap at 300
        perch_offset_front_baseline_mm=68.0,     # CALIBRATED: typical operating point from setups
        perch_offset_rear_baseline_mm=85.0,      # CALIBRATED: typical operating point from setups
        # Front heave damper defl model: defl = -11.58 + 0.544 * perch (R²=0.78)
        # Bottoming threshold: perch < 21.3mm (only 3 of 16 setups bottom)
        # Old IBT baseline (34.5mm) was near bottoming — real setups use 49-100mm
        slider_perch_coeff=0.0,
        slider_intercept=0.0,
        slider_heave_coeff=0.0,
        heave_spring_defl_max_intercept_mm=0.0,
        heave_spring_defl_max_slope=0.0,
        defl_static_intercept=0.0,
        defl_static_heave_coeff=0.0,
    ),
    corner_spring=CornerSpringModel(
        # ORECA chassis: torsion bars at ALL 4 corners (front AND rear)
        # Same torsion bar hardware as BMW — identical discrete OD options
        front_torsion_c=0.0008036,        # ESTIMATE — same as BMW until calibrated
        # NOTE: garage torsion bar deflection is NOT purely weight/(C*OD^4);
        # it includes preload from torsion bar turns. C constant calibration
        # deferred until turns-corrected model is built.
        front_torsion_od_ref_mm=13.9,     # IBT: TorsionBarOD = 13.90mm (front)
        # EXPANDED: front heave damper is ALWAYS bottomed (-1.7 to -2.5mm) across
        # ALL tested ODs (13.90, 14.76, 15.86) and spring rates (90, 190).
        # This is a normal Acura characteristic, not an error to prevent.
        front_torsion_od_range_mm=(13.9, 15.86),
        front_torsion_od_options=[         # CONFIRMED from garage dropdown
            13.90, 14.34, 14.76, 15.14, 15.51, 15.86,  # EXPANDED: full usable range
        ],
        # Rear also uses torsion bars (ORECA, not Dallara) — same discrete options
        rear_torsion_c=0.0008036,         # ESTIMATE — same C constant, needs calibration
        rear_torsion_od_range_mm=(13.9, 18.20),
        rear_torsion_od_options=[          # Same hardware as front
            13.90, 14.34, 14.76, 15.14, 15.51, 15.86,
            16.19, 16.51, 16.81, 17.11, 17.39, 17.67, 17.94, 18.20,
        ],
        rear_spring_range_nmm=(100.0, 300.0),    # Unused — rear is torsion bar
        rear_spring_step_nmm=10.0,               # Unused
        front_motion_ratio=1.0,           # Baked into C constant
        rear_motion_ratio=1.0,            # Baked into C constant for rear torsion
        track_width_mm=1600.0,            # ORECA LMDh chassis
        cg_height_mm=350.0,              # ORECA LMDh chassis
    ),
    arb=ARBModel(
        # ORECA ARB: Size (Disconnected/Soft/Medium/Stiff) + Blades (1-5)
        # Front: 25% softer than BMW — Acura runs more mechanical compliance for active heave system.
        # BMW calibrated: [0, 5500, 11000, 16500] → Acura: [0, 4500, 9000, 13500]
        # IMPROVED ESTIMATE — needs LLTD sweep session to validate (insufficient ARB variation data)
        front_size_labels=["Disconnected", "Soft", "Medium", "Stiff"],
        front_stiffness_nmm_deg=[0.0, 4500.0, 9000.0, 13500.0],  # IMPROVED ESTIMATE: 25% softer than BMW (Acura mechanical compliance)
        front_baseline_size="Medium",     # IBT: ArbSize = Medium
        front_baseline_blade=1,           # IBT: ArbBlades = 1
        rear_size_labels=["Soft", "Medium", "Stiff"],
        rear_stiffness_nmm_deg=[1500.0, 3000.0, 4500.0],
        rear_baseline_size="Medium",      # IBT: ArbSize = Medium
        rear_baseline_blade=2,            # IBT: ArbBlades = 2
        front_blade_count=5,              # PDF: blades 1-5
        rear_blade_count=5,               # PDF: blades 1-5
        track_width_front_mm=1730.0,      # ESTIMATE — ORECA LMDh
        track_width_rear_mm=1650.0,       # ESTIMATE — ORECA LMDh
    ),
    geometry=WheelGeometryModel(
        front_camber_baseline_deg=-2.8,   # IBT: Camber = -2.8 deg
        rear_camber_baseline_deg=-1.8,    # IBT: Camber = -1.8 deg
        front_roll_gain=0.60,             # ESTIMATE — ORECA platform
        rear_roll_gain=0.50,              # ESTIMATE — ORECA platform
        # CALIBRATED: front camber strongly affects front static RH
        # ~2.9mm RH per degree camber (camber=-1.6→RH=33.7, camber=-2.8→RH=30.2)
    ),
    damper=DamperModel(
        # ORECA heave+roll architecture: heave dampers have full LS/HS comp+rbd+slope;
        # roll dampers have LS+HS only (no separate comp/rbd, no slope).
        # iRacing Acura max is 10 clicks (not 11 like BMW).
        ls_comp_range=(1, 10),
        ls_rbd_range=(1, 10),
        hs_comp_range=(1, 10),
        hs_rbd_range=(1, 10),
        hs_slope_range=(1, 10),
        ls_force_per_click_n=18.0,        # ESTIMATE — same as Dallara
        hs_force_per_click_n=80.0,        # ESTIMATE — same as Dallara
        # Baselines from IBT (Hockenheim)
        front_ls_comp_baseline=2,         # IBT: FrontHeave LsCompDamping = 2
        front_ls_rbd_baseline=2,          # IBT: FrontHeave LsRbdDamping = 2
        front_hs_comp_baseline=2,         # IBT: FrontHeave HsCompDamping = 2
        front_hs_rbd_baseline=3,          # IBT: FrontHeave HsRbdDamping = 3
        front_hs_slope_baseline=10,       # IBT: FrontHeave HsCompDampSlope = 10
        rear_ls_comp_baseline=9,          # IBT: RearHeave LsCompDamping = 9
        rear_ls_rbd_baseline=5,           # IBT: RearHeave LsRbdDamping = 5
        rear_hs_comp_baseline=8,          # IBT: RearHeave HsCompDamping = 8
        rear_hs_rbd_baseline=3,           # IBT: RearHeave HsRbdDamping = 3
        rear_hs_slope_baseline=10,        # IBT: RearHeave HsCompDampSlope = 10
        # Roll dampers — Acura ARX-06 (ORECA) has BOTH front and rear roll dampers
        has_roll_dampers=True,
        has_front_roll_damper=True,
        has_rear_roll_damper=True,
        roll_ls_range=(1, 10),
        roll_hs_range=(1, 10),
        front_roll_ls_baseline=2,         # IBT: FrontRoll LsDamping = 2
        front_roll_hs_baseline=3,         # IBT: FrontRoll HsDamping = 3
        rear_roll_ls_baseline=9,          # IBT: RearRoll LsDamping = 9
        rear_roll_hs_baseline=6,          # IBT: RearRoll HsDamping = 6
    ),
    ride_height_model=RideHeightModel.uncalibrated(),
    deflection=DeflectionModel.uncalibrated(),
    # Acura wing range from aero maps
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


def get_car(name: str, apply_calibration: bool = True) -> CarModel:
    """Get car model by canonical name.

    Returns a fresh copy of the car model. If auto-calibration data exists
    for this car (from ``python -m car_model.auto_calibrate``), the calibrated
    models replace the BMW ESTIMATE defaults automatically.

    Args:
        name: Car canonical name ("bmw", "ferrari", "acura", "cadillac", "porsche").
        apply_calibration: If True (default), load and apply any saved calibration
            models from data/calibration/{car}/models.json. Set False to get the
            raw unmodified model (useful for calibration tooling itself).
    """
    import copy

    key = name.lower().strip()
    if key not in _CARS:
        available = ", ".join(_CARS.keys())
        raise KeyError(f"Unknown car '{name}'. Available: {available}")

    # Always return a fresh deep copy so in-place modifications in produce.py
    # don't contaminate the module-level singleton between sessions.
    car = copy.deepcopy(_CARS[key])

    if apply_calibration:
        try:
            from car_model.auto_calibrate import load_calibrated_models, apply_to_car
            cal_models = load_calibrated_models(key)
            if cal_models is not None and cal_models.calibration_complete:
                applied = apply_to_car(car, cal_models)
                if applied:
                    car._auto_calibration_applied = applied
        except Exception as e:
            # Auto-calibration is optional — never fail solver startup, but warn
            import warnings
            warnings.warn(f"Auto-calibration failed for {key}: {e}")

    return car
