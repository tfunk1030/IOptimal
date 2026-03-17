from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _snap(value: Any, step: float, digits: int = 3) -> float:
    try:
        snapped = round(round(float(value) / step) * step, digits)
    except (TypeError, ValueError):
        return 0.0
    if abs(snapped) < (step / 10.0):
        return 0.0
    return snapped


def _snap_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _corner_tuple(corner: Any) -> tuple[int, int, int, int, int]:
    return (
        _snap_int(getattr(corner, "ls_comp", 0)),
        _snap_int(getattr(corner, "ls_rbd", 0)),
        _snap_int(getattr(corner, "hs_comp", 0)),
        _snap_int(getattr(corner, "hs_rbd", 0)),
        _snap_int(getattr(corner, "hs_slope", 0)),
    )


@dataclass(frozen=True)
class SetupFingerprint:
    wing_deg: float
    fuel_l: int
    front_pushrod_mm: float
    rear_pushrod_mm: float
    front_heave_nmm: float
    front_heave_perch_mm: float
    rear_third_nmm: float
    rear_third_perch_mm: float
    front_torsion_od_mm: float
    rear_spring_nmm: float
    rear_spring_perch_mm: float
    front_arb_size: str
    front_arb_blade: int
    rear_arb_size: str
    rear_arb_blade: int
    front_camber_deg: float
    rear_camber_deg: float
    front_toe_mm: float
    rear_toe_mm: float
    damper_lf: tuple[int, int, int, int, int]
    damper_rf: tuple[int, int, int, int, int]
    damper_lr: tuple[int, int, int, int, int]
    damper_rr: tuple[int, int, int, int, int]

    def non_damper_key(self) -> tuple[Any, ...]:
        return (
            self.wing_deg,
            self.fuel_l,
            self.front_pushrod_mm,
            self.rear_pushrod_mm,
            self.front_heave_nmm,
            self.front_heave_perch_mm,
            self.rear_third_nmm,
            self.rear_third_perch_mm,
            self.front_torsion_od_mm,
            self.rear_spring_nmm,
            self.rear_spring_perch_mm,
            self.front_arb_size,
            self.front_arb_blade,
            self.rear_arb_size,
            self.rear_arb_blade,
            self.front_camber_deg,
            self.rear_camber_deg,
            self.front_toe_mm,
            self.rear_toe_mm,
        )

    def exact_key(self) -> tuple[Any, ...]:
        return self.non_damper_key() + self.damper_lf + self.damper_rf + self.damper_lr + self.damper_rr

    def max_damper_delta(self, other: "SetupFingerprint") -> int:
        return max(
            abs(a - b)
            for lhs, rhs in (
                (self.damper_lf, other.damper_lf),
                (self.damper_rf, other.damper_rf),
                (self.damper_lr, other.damper_lr),
                (self.damper_rr, other.damper_rr),
            )
            for a, b in zip(lhs, rhs)
        )

    def matches_candidate(self, other: "SetupFingerprint", damper_tolerance: int = 1) -> bool:
        return self.non_damper_key() == other.non_damper_key() and self.max_damper_delta(other) <= damper_tolerance

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationCluster:
    fingerprint: SetupFingerprint
    session_indices: list[int] = field(default_factory=list)
    session_labels: list[str] = field(default_factory=list)
    latest_session_idx: int = 0
    latest_session_label: str = ""
    best_cluster_session_idx: int = 0
    best_cluster_session_label: str = ""
    comparison_session_idx: int | None = None
    comparison_session_label: str | None = None
    validated_failed: bool = False
    penalty_mode: str = "none"
    lap_delta_s: float = 0.0
    metric_regressions: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fingerprint"] = self.fingerprint.to_dict()
        return data


@dataclass
class CandidateVeto:
    fingerprint: SetupFingerprint
    matched_session_label: str
    matched_session_idx: int
    reason: str
    penalty: float
    penalty_mode: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fingerprint"] = self.fingerprint.to_dict()
        return data


def match_failed_cluster(
    fingerprint: SetupFingerprint,
    clusters: list[ValidationCluster] | None,
    damper_tolerance: int = 1,
) -> ValidationCluster | None:
    for cluster in clusters or []:
        if not cluster.validated_failed:
            continue
        if cluster.fingerprint.matches_candidate(fingerprint, damper_tolerance=damper_tolerance):
            return cluster
    return None


def fingerprint_from_current_setup(setup: Any) -> SetupFingerprint:
    front_tuple = (
        _snap_int(getattr(setup, "front_ls_comp", 0)),
        _snap_int(getattr(setup, "front_ls_rbd", 0)),
        _snap_int(getattr(setup, "front_hs_comp", 0)),
        _snap_int(getattr(setup, "front_hs_rbd", 0)),
        _snap_int(getattr(setup, "front_hs_slope", 0)),
    )
    rear_tuple = (
        _snap_int(getattr(setup, "rear_ls_comp", 0)),
        _snap_int(getattr(setup, "rear_ls_rbd", 0)),
        _snap_int(getattr(setup, "rear_hs_comp", 0)),
        _snap_int(getattr(setup, "rear_hs_rbd", 0)),
        _snap_int(getattr(setup, "rear_hs_slope", 0)),
    )
    return SetupFingerprint(
        wing_deg=_snap(getattr(setup, "wing_angle_deg", 0.0), 1.0, 1),
        fuel_l=_snap_int(getattr(setup, "fuel_l", 0.0)),
        front_pushrod_mm=_snap(getattr(setup, "front_pushrod_mm", 0.0), 0.5, 1),
        rear_pushrod_mm=_snap(getattr(setup, "rear_pushrod_mm", 0.0), 0.5, 1),
        front_heave_nmm=_snap(getattr(setup, "front_heave_nmm", 0.0), 1.0, 1),
        front_heave_perch_mm=_snap(getattr(setup, "front_heave_perch_mm", 0.0), 0.5, 1),
        rear_third_nmm=_snap(getattr(setup, "rear_third_nmm", 0.0), 1.0, 1),
        rear_third_perch_mm=_snap(getattr(setup, "rear_third_perch_mm", 0.0), 0.5, 1),
        front_torsion_od_mm=_snap(getattr(setup, "front_torsion_od_mm", 0.0), 0.01, 2),
        rear_spring_nmm=_snap(getattr(setup, "rear_spring_nmm", 0.0), 1.0, 1),
        rear_spring_perch_mm=_snap(getattr(setup, "rear_spring_perch_mm", 0.0), 0.5, 1),
        front_arb_size=str(getattr(setup, "front_arb_size", "") or ""),
        front_arb_blade=_snap_int(getattr(setup, "front_arb_blade", 0)),
        rear_arb_size=str(getattr(setup, "rear_arb_size", "") or ""),
        rear_arb_blade=_snap_int(getattr(setup, "rear_arb_blade", 0)),
        front_camber_deg=_snap(getattr(setup, "front_camber_deg", 0.0), 0.1, 1),
        rear_camber_deg=_snap(getattr(setup, "rear_camber_deg", 0.0), 0.1, 1),
        front_toe_mm=_snap(getattr(setup, "front_toe_mm", 0.0), 0.1, 1),
        rear_toe_mm=_snap(getattr(setup, "rear_toe_mm", 0.0), 0.1, 1),
        damper_lf=front_tuple,
        damper_rf=front_tuple,
        damper_lr=rear_tuple,
        damper_rr=rear_tuple,
    )


def fingerprint_from_solver_steps(
    wing: float,
    fuel_l: float,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
) -> SetupFingerprint:
    return SetupFingerprint(
        wing_deg=_snap(wing, 1.0, 1),
        fuel_l=_snap_int(fuel_l),
        front_pushrod_mm=_snap(getattr(step1, "front_pushrod_offset_mm", 0.0), 0.5, 1),
        rear_pushrod_mm=_snap(getattr(step1, "rear_pushrod_offset_mm", 0.0), 0.5, 1),
        front_heave_nmm=_snap(getattr(step2, "front_heave_nmm", 0.0), 1.0, 1),
        front_heave_perch_mm=_snap(getattr(step2, "perch_offset_front_mm", 0.0), 0.5, 1),
        rear_third_nmm=_snap(getattr(step2, "rear_third_nmm", 0.0), 1.0, 1),
        rear_third_perch_mm=_snap(getattr(step2, "perch_offset_rear_mm", 0.0), 0.5, 1),
        front_torsion_od_mm=_snap(getattr(step3, "front_torsion_od_mm", 0.0), 0.01, 2),
        rear_spring_nmm=_snap(getattr(step3, "rear_spring_rate_nmm", 0.0), 1.0, 1),
        rear_spring_perch_mm=_snap(getattr(step3, "rear_spring_perch_mm", 0.0), 0.5, 1),
        front_arb_size=str(getattr(step4, "front_arb_size", "") or ""),
        front_arb_blade=_snap_int(getattr(step4, "front_arb_blade_start", 0)),
        rear_arb_size=str(getattr(step4, "rear_arb_size", "") or ""),
        rear_arb_blade=_snap_int(getattr(step4, "rear_arb_blade_start", 0)),
        front_camber_deg=_snap(getattr(step5, "front_camber_deg", 0.0), 0.1, 1),
        rear_camber_deg=_snap(getattr(step5, "rear_camber_deg", 0.0), 0.1, 1),
        front_toe_mm=_snap(getattr(step5, "front_toe_mm", 0.0), 0.1, 1),
        rear_toe_mm=_snap(getattr(step5, "rear_toe_mm", 0.0), 0.1, 1),
        damper_lf=_corner_tuple(getattr(step6, "lf", None)),
        damper_rf=_corner_tuple(getattr(step6, "rf", None)),
        damper_lr=_corner_tuple(getattr(step6, "lr", None)),
        damper_rr=_corner_tuple(getattr(step6, "rr", None)),
    )
