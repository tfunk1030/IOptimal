"""Index↔physical round-trip tests for Ferrari and Acura indexed controls.

Verifies that the index↔physical conversion boundary is consistent: encoding
a physical value to the garage index space and then decoding back produces
the same physical value (within snap resolution), and that out-of-range
physical values clamp cleanly into the legal index range.

Also covers the null-check behavior in the Ferrari indexed-setup decoder
(`_decode_ferrari_indexed_setup`) so partially-initialized current_setup
fields don't get silently coerced to 0.0 via the out-of-range guard.
"""
from __future__ import annotations

import logging

import pytest

from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car
from car_model.garage import GarageSetupState
from solver.solve_chain import _decode_ferrari_indexed_setup


# ─── Ferrari ─────────────────────────────────────────────────────────────────


def test_ferrari_front_heave_index_roundtrip():
    car = get_car("ferrari")
    hsm = car.heave_spring
    gr = car.garage_ranges
    idx_lo, idx_hi = hsm.front_setting_index_range
    for idx in range(int(idx_lo), int(idx_hi) + 1):
        rate = hsm.front_rate_from_setting(float(idx))
        roundtrip = hsm.front_setting_from_rate(rate, resolution=gr.heave_spring_resolution_nmm)
        assert abs(roundtrip - idx) < 1e-6, (
            f"Ferrari front heave idx={idx} → rate={rate} → idx={roundtrip}"
        )


def test_ferrari_rear_third_index_roundtrip():
    car = get_car("ferrari")
    hsm = car.heave_spring
    gr = car.garage_ranges
    idx_lo, idx_hi = hsm.rear_setting_index_range
    for idx in range(int(idx_lo), int(idx_hi) + 1):
        rate = hsm.rear_rate_from_setting(float(idx))
        roundtrip = hsm.rear_setting_from_rate(rate, resolution=gr.heave_spring_resolution_nmm)
        assert abs(roundtrip - idx) < 1e-6, (
            f"Ferrari rear third idx={idx} → rate={rate} → idx={roundtrip}"
        )


def test_ferrari_front_torsion_od_index_roundtrip():
    car = get_car("ferrari")
    csm = car.corner_spring
    gr = car.garage_ranges
    idx_lo, idx_hi = csm.front_setting_index_range
    for idx in range(int(idx_lo), int(idx_hi) + 1):
        od = csm.front_torsion_od_from_setting(float(idx))
        roundtrip = csm.front_setting_from_torsion_od(od, resolution=gr.rear_spring_resolution_nmm)
        assert abs(roundtrip - idx) < 1e-6, (
            f"Ferrari front torsion OD idx={idx} → od={od} → idx={roundtrip}"
        )


def test_ferrari_rear_torsion_bar_index_roundtrip():
    car = get_car("ferrari")
    csm = car.corner_spring
    gr = car.garage_ranges
    idx_lo, idx_hi = csm.rear_setting_index_range
    for idx in range(int(idx_lo), int(idx_hi) + 1):
        rate = csm.rear_bar_rate_from_setting(float(idx))
        roundtrip = csm.rear_setting_from_bar_rate(rate, resolution=gr.rear_spring_resolution_nmm)
        assert abs(roundtrip - idx) < 1e-6, (
            f"Ferrari rear torsion idx={idx} → rate={rate} → idx={roundtrip}"
        )


def test_ferrari_out_of_range_physical_clamps_into_index_space():
    car = get_car("ferrari")
    hsm = car.heave_spring
    csm = car.corner_spring
    gr = car.garage_ranges

    # Physical rate WAY above the calibrated anchor; encoder must clamp to idx_hi.
    huge_rate = 100000.0
    front_idx_hi = hsm.front_setting_index_range[1]
    encoded = hsm.front_setting_from_rate(huge_rate, resolution=gr.heave_spring_resolution_nmm)
    assert encoded == pytest.approx(front_idx_hi, abs=1e-6)

    # Negative physical rate must clamp to idx_lo, not produce a negative index.
    encoded_neg = hsm.front_setting_from_rate(-500.0, resolution=gr.heave_spring_resolution_nmm)
    front_idx_lo = hsm.front_setting_index_range[0]
    assert encoded_neg == pytest.approx(front_idx_lo, abs=1e-6)

    # Same for rear torsion bar rate.
    rear_idx_hi = csm.rear_setting_index_range[1]
    rear_idx_lo = csm.rear_setting_index_range[0]
    assert csm.rear_setting_from_bar_rate(99999.0, resolution=gr.rear_spring_resolution_nmm) == pytest.approx(rear_idx_hi, abs=1e-6)
    assert csm.rear_setting_from_bar_rate(-1.0, resolution=gr.rear_spring_resolution_nmm) == pytest.approx(rear_idx_lo, abs=1e-6)

    # Front torsion OD physical out-of-range clamps to index extremes too.
    assert csm.front_setting_from_torsion_od(99.0, resolution=gr.rear_spring_resolution_nmm) == pytest.approx(csm.front_setting_index_range[1], abs=1e-6)
    assert csm.front_setting_from_torsion_od(0.0, resolution=gr.rear_spring_resolution_nmm) == pytest.approx(csm.front_setting_index_range[0], abs=1e-6)


def test_ferrari_garage_state_decodes_indices():
    """from_current_setup is THE one boundary that decodes indices to physical."""
    car = get_car("ferrari")
    hsm = car.heave_spring
    csm = car.corner_spring

    setup = CurrentSetup(source="ibt")
    setup.front_heave_nmm = 1.0  # index
    setup.rear_third_nmm = 2.0   # index
    setup.front_torsion_od_mm = 5.0  # index
    setup.rear_spring_nmm = 8.0  # rear bar index
    setup.front_camber_deg = -2.5
    setup.rear_camber_deg = -1.5
    setup.wing_angle_deg = 8.0

    state = GarageSetupState.from_current_setup(setup, car=car)

    assert state.front_heave_nmm == pytest.approx(hsm.front_rate_from_setting(1.0))
    assert state.rear_third_nmm == pytest.approx(hsm.rear_rate_from_setting(2.0))
    assert state.front_torsion_od_mm == pytest.approx(csm.front_torsion_od_from_setting(5.0))
    assert state.rear_spring_nmm == pytest.approx(csm.rear_bar_rate_from_setting(8.0))


def test_ferrari_garage_state_passthrough_when_already_physical():
    """If the input value is already physical (out of index range), pass through."""
    car = get_car("ferrari")
    setup = CurrentSetup(source="ibt")
    # Already-physical rates above the index ranges
    setup.front_heave_nmm = 80.0  # physical N/mm, above idx range (0-8.5)
    setup.rear_third_nmm = 600.0  # physical N/mm
    setup.front_torsion_od_mm = 22.0  # physical mm, above idx range (0-18.5)
    setup.rear_spring_nmm = 450.0  # physical bar rate
    setup.front_camber_deg = -2.5
    setup.rear_camber_deg = -1.5
    setup.wing_angle_deg = 8.0

    state = GarageSetupState.from_current_setup(setup, car=car)
    assert state.front_heave_nmm == pytest.approx(80.0)
    assert state.rear_third_nmm == pytest.approx(600.0)
    assert state.front_torsion_od_mm == pytest.approx(22.0)
    assert state.rear_spring_nmm == pytest.approx(450.0)


def test_decode_ferrari_indexed_setup_skips_none_and_zero(caplog):
    """Partially-initialized current_setup must not have indexed fields silently
    coerced to 0.0 via the out-of-range guard."""
    car = get_car("ferrari")
    setup = CurrentSetup(source="ibt")
    # Leave indexed fields at default 0.0 — this represents "not yet populated".
    setup.front_heave_nmm = 0.0
    setup.rear_third_nmm = 0.0
    setup.front_torsion_od_mm = 0.0
    setup.rear_spring_nmm = 0.0

    with caplog.at_level(logging.WARNING, logger="solver.solve_chain"):
        _decode_ferrari_indexed_setup(car, setup)

    # Values must remain 0.0 (NOT decoded to physical anchor like 30 N/mm).
    assert setup.front_heave_nmm == 0.0
    assert setup.rear_third_nmm == 0.0
    assert setup.front_torsion_od_mm == 0.0
    assert setup.rear_spring_nmm == 0.0
    # And we must have warned about each skipped field.
    assert any("front_heave_nmm" in m for m in caplog.messages)
    assert any("rear_third_nmm" in m for m in caplog.messages)


def test_decode_ferrari_indexed_setup_decodes_valid_indices():
    """Sanity: when indices are present, they're decoded to physical values."""
    car = get_car("ferrari")
    hsm = car.heave_spring
    setup = CurrentSetup(source="ibt")
    setup.front_heave_nmm = 1.0  # valid index
    setup.rear_third_nmm = 2.0   # valid index
    setup.front_torsion_od_mm = 5.0
    setup.rear_spring_nmm = 8.0

    _decode_ferrari_indexed_setup(car, setup)

    assert setup.front_heave_nmm == pytest.approx(hsm.front_rate_from_setting(1.0))
    assert setup.rear_third_nmm == pytest.approx(hsm.rear_rate_from_setting(2.0))


def test_decode_ferrari_indexed_setup_idempotent():
    """Calling the decoder twice is safe (range guard prevents double-conversion)."""
    car = get_car("ferrari")
    setup = CurrentSetup(source="ibt")
    setup.front_heave_nmm = 1.0
    setup.rear_third_nmm = 2.0
    setup.front_torsion_od_mm = 5.0
    setup.rear_spring_nmm = 8.0

    _decode_ferrari_indexed_setup(car, setup)
    after_first = (
        setup.front_heave_nmm,
        setup.rear_third_nmm,
        setup.front_torsion_od_mm,
        setup.rear_spring_nmm,
    )
    _decode_ferrari_indexed_setup(car, setup)
    after_second = (
        setup.front_heave_nmm,
        setup.rear_third_nmm,
        setup.front_torsion_od_mm,
        setup.rear_spring_nmm,
    )
    assert after_first == after_second


def test_decode_ferrari_no_op_for_non_ferrari():
    """Non-Ferrari cars must not have their setup mutated by the Ferrari helper."""
    car = get_car("bmw")
    setup = CurrentSetup(source="ibt")
    setup.front_heave_nmm = 1.0  # would be a real physical rate for BMW (low but legal)
    setup.rear_third_nmm = 530.0
    _decode_ferrari_indexed_setup(car, setup)
    assert setup.front_heave_nmm == 1.0
    assert setup.rear_third_nmm == 530.0


# ─── Acura ───────────────────────────────────────────────────────────────────
#
# Acura's heave_spring and corner_spring do NOT set ``setting_index_range``;
# they expose physical N/mm rates and discrete OD options directly. The
# ``from_current_setup`` indexed-decode block must therefore be a no-op for
# Acura, and round-tripping a physical value through the OD-snap helper must
# return a value within the discrete option set.


def test_acura_heave_passthrough():
    car = get_car("acura")
    assert car.heave_spring.front_setting_index_range is None
    assert car.heave_spring.rear_setting_index_range is None

    setup = CurrentSetup(source="ibt")
    setup.front_heave_nmm = 180.0
    setup.rear_third_nmm = 120.0
    setup.front_torsion_od_mm = 13.9
    setup.rear_spring_nmm = 200.0  # unused by Acura, but must pass through
    setup.front_camber_deg = -2.8
    setup.rear_camber_deg = -1.8
    setup.wing_angle_deg = 8.0

    state = GarageSetupState.from_current_setup(setup, car=car)
    assert state.front_heave_nmm == pytest.approx(180.0)
    assert state.rear_third_nmm == pytest.approx(120.0)
    assert state.front_torsion_od_mm == pytest.approx(13.9)
    assert state.rear_spring_nmm == pytest.approx(200.0)


def test_acura_torsion_od_snap_clamps_out_of_range():
    """Acura uses discrete OD options — snapping clamps to the nearest legal value."""
    car = get_car("acura")
    csm = car.corner_spring

    # Above the highest option clamps to that option.
    snapped_high = csm.snap_torsion_od(99.0)
    assert snapped_high == max(csm.front_torsion_od_options)
    # Below the lowest option clamps to that option.
    snapped_low = csm.snap_torsion_od(0.0)
    assert snapped_low == min(csm.front_torsion_od_options)

    snapped_rear_high = csm.snap_rear_torsion_od(99.0)
    assert snapped_rear_high == max(csm.rear_torsion_od_options)


def test_acura_torsion_od_snap_roundtrip_for_legal_values():
    """Snapping a legal Acura discrete OD returns the same OD."""
    car = get_car("acura")
    csm = car.corner_spring
    for od in csm.front_torsion_od_options:
        assert csm.snap_torsion_od(od) == pytest.approx(od)
    for od in csm.rear_torsion_od_options:
        assert csm.snap_rear_torsion_od(od) == pytest.approx(od)
