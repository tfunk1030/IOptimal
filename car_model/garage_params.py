"""Per-car parameter schema — iRacing garage field declarations.

Declares, for each of the 5 GTP cars:
  - Which parameters actually exist in the iRacing garage for that car
  - What the field VALUE means (index, physical N/mm, OD mm, label, etc.)
  - The iRacing garage display label
  - Legal range in garage-native units
  - How to convert to/from solver-internal units

WHY THIS EXISTS:
  The sto/observation adapter stores ALL cars under the same canonical field
  names (front_heave_nmm, torsion_bar_od_mm, etc.) BUT the values mean
  different things per car:

  BMW   front_heave_nmm = 50.0   → 50 N/mm (physical spring rate)
  Ferrari front_heave_nmm = 5.0  → index 5 → 130 N/mm (NOT 5 N/mm)
  Acura  front_heave_nmm = 180.0 → 180 N/mm (physical)

  Ferrari torsion_bar_od_mm = 3.0 → torsion bar index 3 (NOT an OD in mm)
  BMW     torsion_bar_od_mm = 14.34 → actual OD in mm

  Porsche: no torsion bars, no heave index field at all.

All output (setup cards, recommendations) must use garage-native units.
The solver converts to physics units internally.

Usage:
    from car_model.garage_params import get_param_schema, CarParamSchema

    schema = get_param_schema('ferrari')
    print(schema.front_heave.display_label)   # "Front Heave Spring (Index)"
    print(schema.front_heave.value_type)       # "indexed"
    print(schema.rear_torsion.exists)          # True  (Ferrari has rear torsion)
    print(schema.has_param('rear_torsion_bar_index'))  # True
"""

from __future__ import annotations
from dataclasses import dataclass, field
from functools import lru_cache


# ─── Parameter descriptor ────────────────────────────────────────────────────

@dataclass
class ParamDef:
    """Describes one tunable parameter for a specific car.

    Value types:
      "physical"  — value IS the physical quantity (N/mm, deg, mm, etc.)
      "indexed"   — value is a garage index; solver converts via lookup table
      "od_mm"     — value is torsion bar OD in mm (BMW/Cadillac/Porsche)
      "label"     — value is a string label ("Soft", "A", "Medium", etc.)
      "clicks"    — integer click count for dampers
    """
    # Internal canonical field name (what's stored in observation JSON)
    canonical: str = ""
    # Human-readable label as shown in iRacing garage UI
    display_label: str = ""
    # Unit shown to user in output (e.g. "N/mm", "idx", "mm", "deg", "Nm")
    unit: str = ""
    # Type of value — determines how solver converts to physics units
    value_type: str = "physical"   # "physical" | "indexed" | "od_mm" | "label" | "clicks"
    # Whether this parameter exists for this car at all
    exists: bool = True
    # Legal range in garage-native units (None = unrestricted / car-defined)
    min_val: float | None = None
    max_val: float | None = None
    # Discrete allowed values (None = continuous within min/max)
    discrete_values: list | None = None
    # Solver internal name (when different from canonical)
    solver_key: str = ""

    def __post_init__(self):
        if not self.solver_key:
            self.solver_key = self.canonical


@dataclass
class DamperDef:
    """Damper system descriptor for one car."""
    # Max clicks per channel
    ls_clicks: int = 11
    hs_clicks: int = 11
    # Whether HS slope (blow-off) is separately adjustable
    has_hs_slope: bool = True
    # Label used in iRacing garage
    system_label: str = "Double-Wishbone with shim stack"
    # Channel names that exist for this car
    front_channels: list[str] = field(
        default_factory=lambda: ["ls_comp", "ls_rbd", "hs_comp", "hs_rbd"]
    )
    rear_channels: list[str] = field(
        default_factory=lambda: ["ls_comp", "ls_rbd", "hs_comp", "hs_rbd"]
    )


# ─── Full per-car schema ─────────────────────────────────────────────────────

@dataclass
class CarParamSchema:
    """Complete parameter declaration for one GTP car.

    Every field that appears in a setup recommendation or setup card
    is declared here with its correct unit and meaning.
    """
    car: str

    # ── Springs ───────────────────────────────────────────────────────
    front_heave:      ParamDef = field(default_factory=ParamDef)
    rear_heave:       ParamDef = field(default_factory=ParamDef)
    rear_spring:      ParamDef = field(default_factory=ParamDef)
    front_torsion:    ParamDef = field(default_factory=ParamDef)
    rear_torsion:     ParamDef = field(default_factory=ParamDef)

    # ── Ride height ───────────────────────────────────────────────────
    front_rh_static:  ParamDef = field(default_factory=ParamDef)
    rear_rh_static:   ParamDef = field(default_factory=ParamDef)
    front_pushrod:    ParamDef = field(default_factory=ParamDef)
    rear_pushrod:     ParamDef = field(default_factory=ParamDef)

    # ── ARB ───────────────────────────────────────────────────────────
    front_arb_size:   ParamDef = field(default_factory=ParamDef)
    front_arb_blade:  ParamDef = field(default_factory=ParamDef)
    rear_arb_size:    ParamDef = field(default_factory=ParamDef)
    rear_arb_blade:   ParamDef = field(default_factory=ParamDef)

    # ── Wing ─────────────────────────────────────────────────────────
    wing:             ParamDef = field(default_factory=ParamDef)

    # ── Geometry ─────────────────────────────────────────────────────
    front_camber:     ParamDef = field(default_factory=ParamDef)
    rear_camber:      ParamDef = field(default_factory=ParamDef)
    front_toe:        ParamDef = field(default_factory=ParamDef)
    rear_toe:         ParamDef = field(default_factory=ParamDef)

    # ── Brakes ───────────────────────────────────────────────────────
    brake_bias:       ParamDef = field(default_factory=ParamDef)

    # ── Diff ─────────────────────────────────────────────────────────
    diff_preload:     ParamDef = field(default_factory=ParamDef)
    diff_ramp:        ParamDef = field(default_factory=ParamDef)

    # ── TC ────────────────────────────────────────────────────────────
    tc_gain:          ParamDef = field(default_factory=ParamDef)
    tc_slip:          ParamDef = field(default_factory=ParamDef)

    # ── Dampers ───────────────────────────────────────────────────────
    dampers:          DamperDef = field(default_factory=DamperDef)

    def has_param(self, canonical: str) -> bool:
        """True if this car has the named parameter and it exists."""
        for attr in vars(self).values():
            if isinstance(attr, ParamDef) and attr.canonical == canonical:
                return attr.exists
        return False

    def all_params(self) -> list[ParamDef]:
        """All ParamDef fields, existing and non-existing."""
        return [v for v in vars(self).values() if isinstance(v, ParamDef)]

    def existing_params(self) -> list[ParamDef]:
        """Only params that exist for this car."""
        return [p for p in self.all_params() if p.exists]

    def format_value(self, canonical: str, value) -> str:
        """Format a value for human-readable output in garage-native units."""
        for p in self.all_params():
            if p.canonical == canonical and p.exists:
                if p.value_type == "label":
                    return str(value)
                elif p.value_type in ("indexed", "clicks"):
                    return f"{int(value)}" if value is not None else "—"
                elif p.unit:
                    if isinstance(value, float):
                        prec = 1 if p.unit in ("mm", "deg", "Nm") else 3
                        return f"{value:.{prec}f} {p.unit}"
                    return f"{value} {p.unit}"
        return str(value) if value is not None else "—"


# ─── Schema definitions per car ───────────────────────────────────────────────

def _bmw_schema() -> CarParamSchema:
    """BMW M Hybrid V8 — LMDh Dallara chassis.
    
    Torsion bars: OD in mm (3 discrete options: 13.90, 14.34, 15.14).
    Rear suspension: coil spring (N/mm) — no separate rear torsion bar.
    Front heave: N/mm (continuous 0-900).
    Rear third spring: N/mm (continuous 100-900).
    Dampers: 11-click shim stack (front + rear separate).
    ARB: Soft only (front), Soft/Medium/Stiff (rear).
    """
    s = CarParamSchema(car="bmw")
    s.front_heave = ParamDef(
        canonical="front_heave_nmm", display_label="Front Heave Spring",
        unit="N/mm", value_type="physical", min_val=0.0, max_val=900.0,
        solver_key="front_heave_spring_nmm",
    )
    s.rear_heave = ParamDef(
        canonical="rear_third_nmm", display_label="Rear Third Spring",
        unit="N/mm", value_type="physical", min_val=100.0, max_val=900.0,
        solver_key="rear_third_spring_nmm",
    )
    s.rear_spring = ParamDef(
        canonical="rear_spring_nmm", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", min_val=100.0, max_val=400.0,
        solver_key="rear_spring_rate_nmm",
    )
    s.front_torsion = ParamDef(
        canonical="torsion_bar_od_mm", display_label="Front Torsion Bar OD",
        unit="mm", value_type="od_mm",
        discrete_values=[13.90, 14.34, 15.14],
        solver_key="front_torsion_od_mm",
    )
    s.rear_torsion = ParamDef(
        canonical="rear_torsion_bar_index", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", exists=False,
        # BMW rear torsion = same as rear_spring_nmm, not a separate field
    )
    s.front_rh_static = ParamDef(
        canonical="front_rh_static", display_label="Front Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.rear_rh_static = ParamDef(
        canonical="rear_rh_static", display_label="Rear Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.front_pushrod = ParamDef(
        canonical="front_pushrod", display_label="Front Pushrod",
        unit="mm", value_type="physical",
    )
    s.rear_pushrod = ParamDef(
        canonical="rear_pushrod", display_label="Rear Pushrod",
        unit="mm", value_type="physical",
    )
    s.front_arb_size = ParamDef(
        canonical="front_arb_size", display_label="Front ARB Size",
        unit="", value_type="label",
        discrete_values=["Soft"],  # BMW front only has Soft
    )
    s.front_arb_blade = ParamDef(
        canonical="front_arb_blade", display_label="Front ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.rear_arb_size = ParamDef(
        canonical="rear_arb_size", display_label="Rear ARB Size",
        unit="", value_type="label",
        discrete_values=["Soft", "Medium", "Stiff"],
    )
    s.rear_arb_blade = ParamDef(
        canonical="rear_arb_blade", display_label="Rear ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.wing = ParamDef(
        canonical="wing", display_label="Wing Angle",
        unit="deg", value_type="physical",
        discrete_values=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    )
    s.front_camber = ParamDef(
        canonical="front_camber_deg", display_label="Front Camber",
        unit="deg", value_type="physical",
    )
    s.rear_camber = ParamDef(
        canonical="rear_camber_deg", display_label="Rear Camber",
        unit="deg", value_type="physical",
    )
    s.front_toe = ParamDef(
        canonical="front_toe_mm", display_label="Front Toe",
        unit="mm", value_type="physical",
    )
    s.rear_toe = ParamDef(
        canonical="rear_toe_mm", display_label="Rear Toe",
        unit="mm", value_type="physical",
    )
    s.brake_bias = ParamDef(
        canonical="brake_bias_pct", display_label="Brake Bias",
        unit="%", value_type="physical", min_val=44.0, max_val=50.0,
    )
    s.diff_preload = ParamDef(
        canonical="diff_preload_nm", display_label="Diff Preload",
        unit="Nm", value_type="physical", min_val=0.0, max_val=100.0,
    )
    s.diff_ramp = ParamDef(
        canonical="diff_ramp_label", display_label="Diff Ramp",
        unit="", value_type="label",
        discrete_values=["40/65", "45/70", "50/75"],
    )
    s.tc_gain = ParamDef(
        canonical="tc_gain", display_label="TC Gain",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.tc_slip = ParamDef(
        canonical="tc_slip", display_label="TC Slip",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.dampers = DamperDef(
        ls_clicks=11, hs_clicks=11, has_hs_slope=True,
        system_label="Öhlins TTX shim stack",
    )
    return s


def _ferrari_schema() -> CarParamSchema:
    """Ferrari 499P — LMH (not LMDh).

    KEY DIFFERENCES from BMW:
    - Front heave spring: INDEXED (0-8) → 30-190 N/mm. Value in garage/obs = index.
    - Rear third spring: INDEXED (0-9) → 410-950 N/mm. Value = index.
    - Front torsion bar: INDEXED (0-18). Value = index (NOT OD in mm).
    - Rear torsion bar: INDEXED (0-18) — Ferrari HAS rear torsion adjustment.
      (Most LMDh cars do not have a separate rear torsion bar parameter.)
    - ARB sizes: A/B/C/D/E/Disconnected (not Soft/Medium/Stiff).
    - Dampers: 0-40 clicks (Öhlins high-range). Front LS/HS separate.
      Rear LS/HS separate with much wider range than LMDh cars.
    - Brake bias: 49-56% range (higher than BMW's 44-50%).
    - Hybrid: rear-drive enabled/disabled + corner % setting.
    """
    s = CarParamSchema(car="ferrari")
    s.front_heave = ParamDef(
        canonical="front_heave_index", display_label="Front Heave Spring (Index)",
        unit="idx", value_type="indexed", min_val=0, max_val=8,
        solver_key="front_heave_spring_nmm",  # solver converts idx → N/mm
    )
    s.rear_heave = ParamDef(
        canonical="rear_heave_index", display_label="Rear Third Spring (Index)",
        unit="idx", value_type="indexed", min_val=0, max_val=9,
        solver_key="rear_third_spring_nmm",
    )
    s.rear_spring = ParamDef(
        # Ferrari rear_spring_nmm = same as rear_heave_index in obs (alias)
        canonical="rear_spring_nmm", display_label="Rear Third Spring (Index)",
        unit="idx", value_type="indexed", exists=False,
        # Not a separate field — rear_heave_index covers this
    )
    s.front_torsion = ParamDef(
        canonical="front_torsion_bar_index", display_label="Front Torsion Bar (Index)",
        unit="idx", value_type="indexed", min_val=0, max_val=18,
        solver_key="front_torsion_od_mm",  # solver converts idx → wheel rate
    )
    s.rear_torsion = ParamDef(
        canonical="rear_torsion_bar_index", display_label="Rear Torsion Bar (Index)",
        unit="idx", value_type="indexed", min_val=0, max_val=18,
        exists=True,  # Ferrari DOES have rear torsion bar adjustment
        solver_key="rear_torsion_bar_index",
    )
    s.front_rh_static = ParamDef(
        canonical="front_rh_static", display_label="Front Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.rear_rh_static = ParamDef(
        canonical="rear_rh_static", display_label="Rear Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.front_pushrod = ParamDef(
        canonical="front_pushrod", display_label="Front Pushrod",
        unit="mm", value_type="physical",
    )
    s.rear_pushrod = ParamDef(
        canonical="rear_pushrod", display_label="Rear Pushrod",
        unit="mm", value_type="physical",
    )
    s.front_arb_size = ParamDef(
        canonical="front_arb_size", display_label="Front ARB",
        unit="", value_type="label",
        discrete_values=["Disconnected", "A", "B", "C", "D", "E"],
    )
    s.front_arb_blade = ParamDef(
        canonical="front_arb_blade", display_label="Front ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.rear_arb_size = ParamDef(
        canonical="rear_arb_size", display_label="Rear ARB",
        unit="", value_type="label",
        discrete_values=["Disconnected", "A", "B", "C", "D", "E"],
    )
    s.rear_arb_blade = ParamDef(
        canonical="rear_arb_blade", display_label="Rear ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.wing = ParamDef(
        canonical="wing", display_label="Wing Angle",
        unit="deg", value_type="physical",
        discrete_values=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    )
    s.front_camber = ParamDef(
        canonical="front_camber_deg", display_label="Front Camber",
        unit="deg", value_type="physical",
    )
    s.rear_camber = ParamDef(
        canonical="rear_camber_deg", display_label="Rear Camber",
        unit="deg", value_type="physical",
    )
    s.front_toe = ParamDef(
        canonical="front_toe_mm", display_label="Front Toe",
        unit="mm", value_type="physical",
    )
    s.rear_toe = ParamDef(
        canonical="rear_toe_mm", display_label="Rear Toe",
        unit="mm", value_type="physical",
    )
    s.brake_bias = ParamDef(
        canonical="brake_bias_pct", display_label="Brake Bias",
        unit="%", value_type="physical", min_val=49.0, max_val=57.0,
    )
    s.diff_preload = ParamDef(
        canonical="diff_preload_nm", display_label="Diff Preload",
        unit="Nm", value_type="physical", min_val=0.0, max_val=50.0,
    )
    s.diff_ramp = ParamDef(
        canonical="diff_ramp_label", display_label="Diff Ramp",
        unit="", value_type="label",
        discrete_values=["Less Locking", "More Locking"],
    )
    s.tc_gain = ParamDef(
        canonical="tc_gain", display_label="TC Gain",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.tc_slip = ParamDef(
        canonical="tc_slip", display_label="TC Slip",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.dampers = DamperDef(
        ls_clicks=40, hs_clicks=40, has_hs_slope=True,
        system_label="Öhlins TTX high-range (0-40 clicks)",
        front_channels=["ls_comp", "ls_rbd", "hs_comp", "hs_rbd"],
        rear_channels=["ls_comp", "ls_rbd", "hs_comp", "hs_rbd"],
    )
    return s


def _cadillac_schema() -> CarParamSchema:
    """Cadillac V-Series.R — LMDh Dallara chassis.

    Similar to BMW but:
    - Diff with preload, ramp angles, and clutch plates (confirmed from .sto).
    - Torsion bar OD: 3 discrete options (13.90, 14.34, 14.76).
    - Rear spring: N/mm (coil, same as BMW pattern).
    - ARB: only Soft front, Soft/Medium rear.
    - TC gain and TC slip both available (confirmed from .sto).
    """
    s = CarParamSchema(car="cadillac")
    s.front_heave = ParamDef(
        canonical="front_heave_nmm", display_label="Front Heave Spring",
        unit="N/mm", value_type="physical", min_val=20.0, max_val=200.0,
        solver_key="front_heave_spring_nmm",
    )
    s.rear_heave = ParamDef(
        canonical="rear_third_nmm", display_label="Rear Third Spring",
        unit="N/mm", value_type="physical", min_val=100.0, max_val=1000.0,
        solver_key="rear_third_spring_nmm",
    )
    s.rear_spring = ParamDef(
        canonical="rear_spring_nmm", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", min_val=100.0, max_val=400.0,
        solver_key="rear_spring_rate_nmm",
    )
    s.front_torsion = ParamDef(
        canonical="torsion_bar_od_mm", display_label="Front Torsion Bar OD",
        unit="mm", value_type="od_mm",
        discrete_values=[13.90, 14.34, 14.76],
        solver_key="front_torsion_od_mm",
    )
    s.rear_torsion = ParamDef(
        canonical="rear_torsion_bar_index", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", exists=False,
        # Cadillac rear = coil spring only, controlled via rear_spring_nmm
    )
    s.front_rh_static = ParamDef(
        canonical="front_rh_static", display_label="Front Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.rear_rh_static = ParamDef(
        canonical="rear_rh_static", display_label="Rear Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.front_pushrod = ParamDef(
        canonical="front_pushrod", display_label="Front Pushrod",
        unit="mm", value_type="physical",
    )
    s.rear_pushrod = ParamDef(
        canonical="rear_pushrod", display_label="Rear Pushrod",
        unit="mm", value_type="physical",
    )
    s.front_arb_size = ParamDef(
        canonical="front_arb_size", display_label="Front ARB Size",
        unit="", value_type="label", discrete_values=["Soft"],
    )
    s.front_arb_blade = ParamDef(
        canonical="front_arb_blade", display_label="Front ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.rear_arb_size = ParamDef(
        canonical="rear_arb_size", display_label="Rear ARB Size",
        unit="", value_type="label", discrete_values=["Soft", "Medium"],
    )
    s.rear_arb_blade = ParamDef(
        canonical="rear_arb_blade", display_label="Rear ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.wing = ParamDef(
        canonical="wing", display_label="Wing Angle",
        unit="deg", value_type="physical",
        discrete_values=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    )
    s.front_camber = ParamDef(
        canonical="front_camber_deg", display_label="Front Camber",
        unit="deg", value_type="physical",
    )
    s.rear_camber = ParamDef(
        canonical="rear_camber_deg", display_label="Rear Camber",
        unit="deg", value_type="physical",
    )
    s.front_toe = ParamDef(
        canonical="front_toe_mm", display_label="Front Toe",
        unit="mm", value_type="physical",
    )
    s.rear_toe = ParamDef(
        canonical="rear_toe_mm", display_label="Rear Toe",
        unit="mm", value_type="physical",
    )
    s.brake_bias = ParamDef(
        canonical="brake_bias_pct", display_label="Brake Bias",
        unit="%", value_type="physical", min_val=45.0, max_val=52.0,
    )
    s.diff_preload = ParamDef(
        canonical="diff_preload_nm", display_label="Diff Preload",
        unit="Nm", value_type="physical", exists=True,
        min_val=0.0, max_val=150.0,
        # CORRECTED 2026-04-28: .sto confirms Preload=35 Nm; NOT open diff
    )
    s.diff_ramp = ParamDef(
        canonical="diff_ramp_label", display_label="Diff Ramp Angles",
        unit="", value_type="label", exists=True,
        # CORRECTED 2026-04-28: .sto confirms CoastDriveRampAngles="45/70"
    )
    s.diff_clutch_plates = ParamDef(
        canonical="diff_clutch_plates", display_label="Clutch Friction Plates",
        unit="", value_type="clicks", exists=True,
        min_val=1, max_val=10,
        # ADDED 2026-04-28: .sto confirms ClutchFrictionPlates=6
    )
    s.tc_gain = ParamDef(
        canonical="tc_gain", display_label="TC Gain",
        unit="", value_type="clicks", min_val=0, max_val=11,
        exists=True,
        # CORRECTED 2026-04-28: .sto confirms TractionControlGain=6
    )
    s.tc_slip = ParamDef(
        canonical="tc_slip", display_label="TC Slip",
        unit="", value_type="clicks", min_val=0, max_val=11,
        exists=True,
        # CORRECTED 2026-04-28: .sto confirms TractionControlSlip=5
    )
    s.dampers = DamperDef(
        ls_clicks=11, hs_clicks=11, has_hs_slope=True,
        system_label="Öhlins TTX shim stack",
    )
    return s


def _porsche_schema() -> CarParamSchema:
    """Porsche 963 — LMDh Multimatic chassis (NOT Dallara).

    KEY DIFFERENCES (source: official iRacing Porsche 963 User Manual PDF):
    - DSSV (spool valve) dampers — very different from BMW shim stack.
      Click behavior is non-linear; BMW zeta calibration is INVALID for Porsche.
    - NO front torsion bar OD adjustment (confirmed in manual — torsion bars exist
      for corner weight via Torsion Bar Turns but no OD selection).
    - UNIQUE: Roll Spring (front) — resists roll but not heave. Has Roll Perch Offset.
      Roll Spring Deflection must be ≈0 for tech compliance.
    - UNIQUE: Roll Damper (front only) — LS + HS + HS Slope, comp+rbd linked.
    - Front ARB: Connected/Disconnected TOGGLE (NOT size labels like Soft/Medium).
      ARB Adjustment (blades 1-5) is in-car adjustable via F8 FARB.
      Disconnecting disables blade adjustment.
    - HS Comp Damping Slope: REAR CORNERS ONLY. Front heave has no slope.
    - Rear uses "Pushrod Length DELTA" (not "Offset" like BMW).
    - NEEDS: dedicated IBT sessions for Roll Spring range, DSSV damper model.
    """
    s = CarParamSchema(car="porsche")
    s.front_heave = ParamDef(
        canonical="front_heave_nmm", display_label="Front Heave Spring",
        unit="N/mm", value_type="physical", min_val=20.0, max_val=200.0,
        solver_key="front_heave_spring_nmm",
    )
    s.rear_heave = ParamDef(
        canonical="rear_third_nmm", display_label="Rear Third Spring",
        unit="N/mm", value_type="physical", min_val=0.0, max_val=300.0,
        solver_key="rear_third_spring_nmm",
    )
    s.rear_spring = ParamDef(
        canonical="rear_spring_nmm", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", min_val=100.0, max_val=400.0,
        solver_key="rear_spring_rate_nmm",
    )
    s.front_torsion = ParamDef(
        canonical="torsion_bar_od_mm", display_label="Front Torsion Bar OD",
        unit="mm", value_type="od_mm", exists=False,
        # Porsche: No front Torsion Bar OD selection confirmed by manual.
        # Manual tech specs: "DOUBLE-WISHBONE FRONT, MULTILINK REAR" — not torsion bar front.
        # Front spring stiffness in roll controlled by Roll Spring (unique parameter).
        # Torsion Bar Turns exist per corner for crossweight/RH, but no OD choice.
    )
    s.rear_torsion = ParamDef(
        canonical="rear_torsion_bar_index", display_label="Rear Torsion Bar",
        unit="", value_type="physical", exists=False,
        # Porsche has no rear torsion bar adjustment
    )
    s.front_rh_static = ParamDef(
        canonical="front_rh_static", display_label="Front Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.rear_rh_static = ParamDef(
        canonical="rear_rh_static", display_label="Rear Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.front_pushrod = ParamDef(
        canonical="front_pushrod", display_label="Front Pushrod",
        unit="mm", value_type="physical",
    )
    s.rear_pushrod = ParamDef(
        canonical="rear_pushrod", display_label="Rear Pushrod",
        unit="mm", value_type="physical",
    )
    s.front_arb_size = ParamDef(
        canonical="front_arb_size", display_label="Front ARB Setting",
        unit="", value_type="label",
        # CONFIRMED from manual: Connected/Disconnected toggle (NOT size labels)
        # Connected enables ARB Adjustment (blade) tuning.
        discrete_values=["Disconnected", "Connected"],
        exists=True,
    )
    s.front_arb_blade = ParamDef(
        canonical="front_arb_blade", display_label="Front ARB Adjustment",
        unit="", value_type="clicks", min_val=1, max_val=5,
        # In-car via F8 FARB. Only adjustable when ARB Setting = Connected.
    )
    s.rear_arb_size = ParamDef(
        canonical="rear_arb_size", display_label="Rear ARB Size",
        unit="", value_type="label",
        # CORRECTED 2026-04-03: garage screenshots show Stiff available.
        discrete_values=["Disconnected", "Soft", "Medium", "Stiff"],
    )
    s.rear_arb_blade = ParamDef(
        canonical="rear_arb_blade", display_label="Rear ARB Adjustment",
        unit="", value_type="clicks", min_val=1, max_val=5,
        # In-car via F8 RARB
    )
    s.wing = ParamDef(
        canonical="wing", display_label="Wing Angle",
        unit="deg", value_type="physical",
        discrete_values=[12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    )
    s.front_camber = ParamDef(
        canonical="front_camber_deg", display_label="Front Camber",
        unit="deg", value_type="physical",
    )
    s.rear_camber = ParamDef(
        canonical="rear_camber_deg", display_label="Rear Camber",
        unit="deg", value_type="physical",
    )
    s.front_toe = ParamDef(
        canonical="front_toe_mm", display_label="Front Toe",
        unit="mm", value_type="physical",
    )
    s.rear_toe = ParamDef(
        canonical="rear_toe_mm", display_label="Rear Toe",
        unit="mm", value_type="physical",
    )
    s.brake_bias = ParamDef(
        canonical="brake_bias_pct", display_label="Brake Bias",
        unit="%", value_type="physical", min_val=48.0, max_val=55.0,
    )
    s.diff_preload = ParamDef(
        canonical="diff_preload_nm", display_label="Diff Preload",
        unit="Nm", value_type="physical", exists=True,
        min_val=0.0, max_val=40.0,
        # CORRECTED 2026-04-03: garage screenshots confirm Porsche HAS diff preload (0 Nm shown)
    )
    s.diff_ramp = ParamDef(
        canonical="diff_ramp_label", display_label="Diff Ramp Angles",
        unit="", value_type="label", exists=True,
        # CORRECTED 2026-04-03: garage screenshots show "50/75" coast/drive ramp angles
    )
    s.diff_clutch_plates = ParamDef(
        canonical="diff_clutch_plates", display_label="Clutch Friction Plates",
        unit="", value_type="clicks", exists=True,
        min_val=1, max_val=10,
        # CORRECTED 2026-04-03: garage screenshots show 6 clutch plates
    )
    s.tc_gain = ParamDef(
        canonical="tc_gain", display_label="TC Gain",
        unit="", value_type="clicks",
    )
    s.tc_slip = ParamDef(
        canonical="tc_slip", display_label="TC Slip",
        unit="", value_type="clicks",
    )
    s.dampers = DamperDef(
        ls_clicks=20, hs_clicks=20, has_hs_slope=False,
        system_label="Multimatic DSSV spool valve (click count TBD — needs calibration IBT)",
    )
    return s


def _acura_schema() -> CarParamSchema:
    """Acura ARX-06 — LMDh Oreca chassis.

    KEY DIFFERENCES (source: official iRacing Acura ARX-06 User Manual via ManualsLib):
    - Wing: 6.0-10.0 deg, 0.5-deg steps (ALL other GTP cars: 12-17 deg).
    - Front heave: N/mm (90-400 range) — same as BMW format, different range.
    - Front torsion bar: OD in mm (confirmed by manual — Torsion Bar O.D. exists
      with Torsion Bar Turns for corner weight). Options: 13.90/15.51/15.86.
    - UNIQUE: HEAVE DAMPER (front) — Acura front heave element is ACTIVELY DAMPED,
      not a passive slider like BMW. Manual shows "HEAVE DAMPER DEFL" (not "HEAVE SLIDER DEFL").
    - Front ARB: Disconnected + Soft (possibly Medium) — includes Disconnected option.
    - REAR: Uses "Rear Heave Spring" NOT "Third Spring". Same function, different name.
    - NO rear torsion bar adjustment (rear_torsion_bar_index = 0.0 in all IBT sessions).
    - Hybrid: rear-drive enabled/disabled + corner % setting.
    """
    s = CarParamSchema(car="acura")
    s.front_heave = ParamDef(
        canonical="front_heave_nmm", display_label="Front Heave Spring",
        unit="N/mm", value_type="physical", min_val=90.0, max_val=400.0,
        solver_key="front_heave_spring_nmm",
    )
    s.rear_heave = ParamDef(
        # CONFIRMED: Acura calls this "Rear Heave Spring" NOT "Third Spring"
        # Same function as BMW Third Spring, different iRacing label.
        canonical="rear_third_nmm", display_label="Rear Heave Spring",
        unit="N/mm", value_type="physical", min_val=60.0, max_val=300.0,
        solver_key="rear_third_spring_nmm",
    )
    s.rear_spring = ParamDef(
        canonical="rear_spring_nmm", display_label="Rear Spring Rate",
        unit="N/mm", value_type="physical", exists=False,  # rear_spring_nmm = 0.0 always
        solver_key="rear_spring_rate_nmm",
    )
    s.front_torsion = ParamDef(
        canonical="torsion_bar_od_mm", display_label="Front Torsion Bar OD",
        unit="mm", value_type="od_mm",
        discrete_values=[13.90, 15.51, 15.86],
        solver_key="front_torsion_od_mm",
    )
    s.rear_torsion = ParamDef(
        canonical="rear_torsion_bar_index", display_label="Rear Torsion Bar",
        unit="", value_type="physical", exists=False,
        # Acura: rear_torsion_bar_index = 0.0 in all sessions — not adjustable
    )
    s.front_rh_static = ParamDef(
        canonical="front_rh_static", display_label="Front Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.rear_rh_static = ParamDef(
        canonical="rear_rh_static", display_label="Rear Ride Height",
        unit="mm", value_type="physical", min_val=30.0, max_val=80.0,
    )
    s.front_pushrod = ParamDef(
        canonical="front_pushrod", display_label="Front Pushrod",
        unit="mm", value_type="physical",
    )
    s.rear_pushrod = ParamDef(
        canonical="rear_pushrod", display_label="Rear Pushrod",
        unit="mm", value_type="physical",
    )
    s.front_arb_size = ParamDef(
        canonical="front_arb_size", display_label="Front ARB Size",
        unit="", value_type="label",
        discrete_values=["Soft", "Medium"],
    )
    s.front_arb_blade = ParamDef(
        canonical="front_arb_blade", display_label="Front ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.rear_arb_size = ParamDef(
        canonical="rear_arb_size", display_label="Rear ARB Size",
        unit="", value_type="label",
        discrete_values=["Soft", "Medium"],
    )
    s.rear_arb_blade = ParamDef(
        canonical="rear_arb_blade", display_label="Rear ARB Blade",
        unit="", value_type="clicks", min_val=1, max_val=5,
    )
    s.wing = ParamDef(
        canonical="wing", display_label="Wing Angle",
        unit="deg", value_type="physical",
        discrete_values=[6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0],
    )
    s.front_camber = ParamDef(
        canonical="front_camber_deg", display_label="Front Camber",
        unit="deg", value_type="physical",
    )
    s.rear_camber = ParamDef(
        canonical="rear_camber_deg", display_label="Rear Camber",
        unit="deg", value_type="physical",
    )
    s.front_toe = ParamDef(
        canonical="front_toe_mm", display_label="Front Toe",
        unit="mm", value_type="physical",
    )
    s.rear_toe = ParamDef(
        canonical="rear_toe_mm", display_label="Rear Toe",
        unit="mm", value_type="physical",
    )
    s.brake_bias = ParamDef(
        canonical="brake_bias_pct", display_label="Brake Bias",
        unit="%", value_type="physical", min_val=44.0, max_val=52.0,
    )
    s.diff_preload = ParamDef(
        canonical="diff_preload_nm", display_label="Diff Preload",
        unit="Nm", value_type="physical", min_val=0.0, max_val=100.0,
    )
    s.diff_ramp = ParamDef(
        canonical="diff_ramp_label", display_label="Diff Ramp",
        unit="", value_type="label",
        discrete_values=["40/65"],
    )
    s.tc_gain = ParamDef(
        canonical="tc_gain", display_label="TC Gain",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.tc_slip = ParamDef(
        canonical="tc_slip", display_label="TC Slip",
        unit="", value_type="clicks", min_val=0, max_val=11,
    )
    s.dampers = DamperDef(
        ls_clicks=11, hs_clicks=11, has_hs_slope=True,
        system_label="Öhlins TTX shim stack",
    )
    return s


# ─── Registry ────────────────────────────────────────────────────────────────

_SCHEMAS: dict[str, CarParamSchema] = {
    "bmw":      _bmw_schema(),
    "ferrari":  _ferrari_schema(),
    "cadillac": _cadillac_schema(),
    "porsche":  _porsche_schema(),
    "acura":    _acura_schema(),
}


@lru_cache(maxsize=10)
def get_param_schema(car: str) -> CarParamSchema:
    """Get the parameter schema for a car.

    Args:
        car: Canonical car name ("bmw", "ferrari", "cadillac", "porsche", "acura")

    Returns:
        CarParamSchema with full parameter declaration for this car.

    Raises:
        KeyError if car is not known.
    """
    key = car.lower().strip()
    # Handle common aliases
    _ALIASES = {
        "ferrari499p": "ferrari",
        "ferrari_499p": "ferrari",
        "bmw_m_hybrid": "bmw",
        "cadillac_v": "cadillac",
        "porsche963": "porsche",
        "acura_arx06": "acura",
    }
    key = _ALIASES.get(key, key)
    if key not in _SCHEMAS:
        raise KeyError(
            f"Unknown car '{car}'. Known: {list(_SCHEMAS.keys())}"
        )
    return _SCHEMAS[key]


def format_setup_card(car: str, setup: dict) -> str:
    """Render a setup dict as a human-readable card using car-native units.

    Args:
        car:   Canonical car name
        setup: Dict of {canonical_param: value}

    Returns:
        Multi-line formatted setup card string.
    """
    schema = get_param_schema(car)
    lines = [f"═══ {car.upper()} Setup ═══"]

    groups = [
        ("Springs",  ["front_heave", "rear_heave", "rear_spring", "front_torsion", "rear_torsion"]),
        ("Ride Height", ["front_rh_static", "rear_rh_static", "front_pushrod", "rear_pushrod"]),
        ("ARB",      ["front_arb_size", "front_arb_blade", "rear_arb_size", "rear_arb_blade"]),
        ("Wing",     ["wing"]),
        ("Geometry", ["front_camber", "rear_camber", "front_toe", "rear_toe"]),
        ("Brakes",   ["brake_bias"]),
        ("Diff",     ["diff_preload", "diff_ramp"]),
        ("TC",       ["tc_gain", "tc_slip"]),
    ]

    for group_name, field_names in groups:
        group_lines = []
        for fname in field_names:
            param = getattr(schema, fname, None)
            if not param or not param.exists:
                continue
            val = setup.get(param.canonical)
            if val is None:
                val = setup.get(param.solver_key)
            if val is None:
                continue
            formatted = schema.format_value(param.canonical, val)
            group_lines.append(f"  {param.display_label}: {formatted}")
        if group_lines:
            lines.append(f"\n{group_name}")
            lines.extend(group_lines)

    return "\n".join(lines)
