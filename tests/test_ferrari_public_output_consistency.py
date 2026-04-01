"""Test: Ferrari indexed-controls round-trip consistency and CarModel wiring.

These tests verify that:
1. FerrariIndexedControlModel encodes/decodes correctly at calibrated anchor points.
2. FERRARI_499P CarModel has ferrari_indexed_controls properly wired.
3. Non-Ferrari cars do NOT have indexed controls (None).
4. HeaveSpringModel front_spring_range_nmm is (30.0, 190.0) for Ferrari.
"""
import pytest


# ─── IndexedControlModel encoding/decoding ────────────────────────────────────

def test_ferrari_indexed_controls_front_heave_anchor():
    """Front heave: idx 1 → 50 N/mm (validated anchor from IBT Mar19/Mar20)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.front_heave_rate_from_index(1.0)
    assert abs(result - 50.0) < 0.1, f"Expected 50.0 N/mm at idx 1, got {result}"


def test_ferrari_indexed_controls_front_heave_max():
    """Front heave: idx 8 → 190 N/mm (upper range boundary)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.front_heave_rate_from_index(8.0)
    assert abs(result - 190.0) < 0.1, f"Expected 190.0 N/mm at idx 8, got {result}"


def test_ferrari_indexed_controls_front_heave_min():
    """Front heave: idx 0 → 30 N/mm (lower range boundary)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.front_heave_rate_from_index(0.0)
    assert abs(result - 30.0) < 0.1, f"Expected 30.0 N/mm at idx 0, got {result}"


def test_ferrari_indexed_controls_rear_heave_anchor():
    """Rear heave: idx 2 → 530 N/mm (validated anchor)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.rear_heave_rate_from_index(2.0)
    assert abs(result - 530.0) < 0.5, f"Expected 530.0 N/mm at idx 2, got {result}"


def test_ferrari_indexed_controls_front_torsion_calibrated():
    """Front torsion: idx 2 → 220.6 N/mm (validated garage screenshot)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.front_torsion_rate_from_index(2.0)
    assert abs(result - 220.6) < 0.5, f"Expected 220.6 N/mm at idx 2, got {result}"


def test_ferrari_indexed_controls_rear_torsion_calibrated():
    """Rear torsion: idx 18 → 599.6 N/mm (validated garage screenshot)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    result = ctrl.rear_torsion_rate_from_index(18.0)
    assert abs(result - 599.6) < 0.5, f"Expected 599.6 N/mm at idx 18, got {result}"


def test_ferrari_indexed_controls_inverse_front_heave():
    """Round-trip: encode 50 N/mm → should recover idx 1.0."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    idx = ctrl.front_heave_index_from_rate(50.0)
    assert abs(idx - 1.0) < 0.01, f"Expected idx 1.0 for 50 N/mm, got {idx}"


def test_ferrari_indexed_controls_inverse_round_trip():
    """Full round-trip: encode then decode should recover original value within 1 N/mm."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    rate = 50.0  # validated anchor
    idx = ctrl.front_heave_index_from_rate(rate)
    recovered = ctrl.front_heave_rate_from_index(idx)
    assert abs(recovered - rate) < 1.0, (
        f"Round-trip failed: 50.0 → idx {idx} → {recovered} N/mm"
    )


def test_ferrari_indexed_controls_interpolation():
    """Interpolation between calibrated points should be monotonic."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS
    ctrl = FERRARI_499P_INDEXED_CONTROLS
    rates = [ctrl.front_heave_rate_from_index(float(i)) for i in range(9)]
    for i in range(len(rates) - 1):
        assert rates[i] < rates[i + 1], (
            f"Rates not monotonic: idx {i}={rates[i]}, idx {i+1}={rates[i+1]}"
        )


# ─── CarModel wiring ──────────────────────────────────────────────────────────

def test_ferrari_car_has_indexed_controls():
    """FERRARI_499P must have ferrari_indexed_controls wired (not None)."""
    from car_model.cars import get_car
    car = get_car("ferrari", apply_calibration=False)
    assert car.ferrari_indexed_controls is not None, (
        "FERRARI_499P.ferrari_indexed_controls should not be None"
    )


def test_ferrari_indexed_controls_table_lengths():
    """FERRARI_499P indexed controls must have correct number of anchor points."""
    from car_model.cars import get_car
    car = get_car("ferrari", apply_calibration=False)
    ctrl = car.ferrari_indexed_controls
    assert len(ctrl.front_heave) == 9, f"front_heave should have 9 pts, got {len(ctrl.front_heave)}"
    assert len(ctrl.rear_heave) == 10, f"rear_heave should have 10 pts, got {len(ctrl.rear_heave)}"
    assert len(ctrl.front_torsion) == 7, f"front_torsion should have 7 pts, got {len(ctrl.front_torsion)}"
    assert len(ctrl.rear_torsion) == 5, f"rear_torsion should have 5 pts, got {len(ctrl.rear_torsion)}"


def test_ferrari_heave_spring_range():
    """FERRARI_499P front_spring_range_nmm should be (30.0, 190.0)."""
    from car_model.cars import get_car
    car = get_car("ferrari", apply_calibration=False)
    lo, hi = car.heave_spring.front_spring_range_nmm
    assert abs(lo - 30.0) < 0.01, f"Expected lower bound 30.0, got {lo}"
    assert abs(hi - 190.0) < 0.01, f"Expected upper bound 190.0, got {hi}"


def test_non_ferrari_no_indexed_controls():
    """Non-Ferrari cars must NOT have indexed controls (should be None)."""
    from car_model.cars import get_car
    for car_name in ["bmw", "cadillac", "acura", "porsche"]:
        car = get_car(car_name, apply_calibration=False)
        assert car.ferrari_indexed_controls is None, (
            f"{car_name} should not have indexed controls, but ferrari_indexed_controls is set"
        )


def test_ferrari_indexed_controls_standalone_import():
    """FERRARI_499P_INDEXED_CONTROLS can be imported standalone (no CarModel needed)."""
    from car_model.cars import FERRARI_499P_INDEXED_CONTROLS, FerrariIndexedControlModel
    assert isinstance(FERRARI_499P_INDEXED_CONTROLS, FerrariIndexedControlModel)
