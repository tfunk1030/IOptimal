"""Per-axle roll damper architecture tests.

The Porsche 963 (Multimatic) has a FRONT roll damper but NO rear roll damper —
rear roll motion is implicit in the per-corner LR/RR shocks. The Acura ARX-06
(ORECA) has BOTH front and rear roll dampers. Writing
``CarSetup_Dampers_RearRoll_*`` for Porsche emits XML IDs that don't exist in
iRacing's Porsche garage schema (phantom output), so the writer must gate on
``DamperModel.has_front_roll_damper`` / ``has_rear_roll_damper``.

Tests:
1. Porsche .sto omits ``CarSetup_Dampers_RearRoll_*`` IDs.
2. Porsche .sto still contains ``CarSetup_Dampers_FrontRoll_*`` IDs.
3. Acura .sto contains BOTH front and rear roll damper IDs.
4. Forward-looking: cars declaring ``has_roll_dampers=True`` without per-axle
   flags should raise (Unit 6 contract). Marked ``xfail`` until that change
   lands so we don't block on Unit 6's PR.
"""

from __future__ import annotations

import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from output.setup_writer import write_sto


# ── Step bundle helpers ────────────────────────────────────────────────────

def _corner(ls_comp: int = 6, ls_rbd: int = 6, hs_comp: int = 4,
            hs_rbd: int = 8, hs_slope: int = 11) -> SimpleNamespace:
    return SimpleNamespace(
        ls_comp=ls_comp, ls_rbd=ls_rbd,
        hs_comp=hs_comp, hs_rbd=hs_rbd,
        hs_slope=hs_slope,
    )


def _step_bundle_with_roll_dampers() -> tuple[SimpleNamespace, ...]:
    step1 = SimpleNamespace(
        dynamic_front_rh_mm=20.0, dynamic_rear_rh_mm=42.0,
        df_balance_pct=45.0, ld_ratio=4.0,
        static_front_rh_mm=31.0, static_rear_rh_mm=48.5,
        front_pushrod_offset_mm=-26.0, rear_pushrod_offset_mm=-24.0,
    )
    step2 = SimpleNamespace(
        front_heave_nmm=200.0, rear_third_nmm=300.0,
        perch_offset_front_mm=0.0, perch_offset_rear_mm=0.0,
        front_excursion_at_rate_mm=12.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=14.0,
        front_wheel_rate_nmm=180.0,  # Porsche uses front roll spring → wheel rate
        rear_spring_rate_nmm=160.0,
        rear_spring_perch_mm=0.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="Connected", front_arb_blade_start=1,
        rear_arb_size="Stiff", rear_arb_blade_start=4,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-3.0, rear_camber_deg=-1.8,
        front_toe_mm=-0.4, rear_toe_mm=0.2,
    )
    step6 = SimpleNamespace(
        lf=_corner(), rf=_corner(),
        lr=_corner(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=11, hs_slope=12),
        rr=_corner(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=11, hs_slope=12),
        # Roll-damper values; writer gates on per-axle flags
        front_roll_ls=5,
        front_roll_hs=4,
        front_roll_hs_slope=11,
        rear_roll_ls=6,
        rear_roll_hs=5,
        # Porsche-only 3rd damper
        rear_3rd_ls_comp=2,
        rear_3rd_hs_comp=2,
        rear_3rd_ls_rbd=2,
        rear_3rd_hs_rbd=2,
    )
    return step1, step2, step3, step4, step5, step6


def _write(car_canonical: str, track_name: str, fuel_l: float = 58.0) -> str:
    step1, step2, step3, step4, step5, step6 = _step_bundle_with_roll_dampers()
    with TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"{car_canonical}.sto"
        write_sto(
            car_name=car_canonical.title(),
            track_name=track_name,
            wing=17.0,
            fuel_l=fuel_l,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=out,
            car_canonical=car_canonical,
            tyre_pressure_kpa=152.0,
            brake_bias_pct=50.0,
            diff_coast_drive_ramp="40/65",
            diff_clutch_plates=6,
            diff_preload_nm=10.0,
            tc_gain=4,
            tc_slip=3,
        )
        return out.read_text(encoding="utf-8")


# ── Porsche: no rear roll damper ───────────────────────────────────────────

class TestPorscheRollDampers:
    def test_omits_phantom_rear_roll_damper_ids(self) -> None:
        text = _write("porsche", "Algarve")
        assert "CarSetup_Dampers_RearRoll_LsDamping" not in text
        assert "CarSetup_Dampers_RearRoll_HsDamping" not in text

    def test_still_writes_front_roll_damper_ids(self) -> None:
        text = _write("porsche", "Algarve")
        assert "CarSetup_Dampers_FrontRoll_LsDamping" in text
        assert "CarSetup_Dampers_FrontRoll_HsDamping" in text


# ── Acura: both axles ──────────────────────────────────────────────────────

class TestAcuraRollDampers:
    def test_writes_front_and_rear_roll_damper_ids(self) -> None:
        text = _write("acura", "Hockenheim")
        assert "CarSetup_Dampers_FrontRoll_LsDamping" in text
        assert "CarSetup_Dampers_FrontRoll_HsDamping" in text
        assert "CarSetup_Dampers_RearRoll_LsDamping" in text
        assert "CarSetup_Dampers_RearRoll_HsDamping" in text


# ── Unit 6 forward-looking contract ───────────────────────────────────────

@pytest.mark.xfail(
    reason="Unit 6: legacy backward-compat still allowed in setup_writer.py "
           "(has_roll_dampers=True with neither per-axle flag falls back to "
           "BOTH). Strict ValueError contract not yet wired.",
    strict=False,
)
def test_legacy_roll_dampers_without_per_axle_flags_raises(
    porsche_car,
) -> None:
    """A car with ``has_roll_dampers=True`` but neither ``has_front_roll_damper``
    nor ``has_rear_roll_damper`` set should be rejected — silent fallback is
    a Key Principle 8 violation.
    """
    legacy = copy.deepcopy(porsche_car)
    legacy.damper.has_roll_dampers = True
    legacy.damper.has_front_roll_damper = False
    legacy.damper.has_rear_roll_damper = False

    # Patch the get_car cache so write_sto picks up the legacy variant.
    import car_model.cars as cars_mod
    original = cars_mod._CARS["porsche"]
    cars_mod._CARS["porsche"] = legacy
    try:
        with pytest.raises(ValueError):
            _write("porsche", "Algarve")
    finally:
        cars_mod._CARS["porsche"] = original
