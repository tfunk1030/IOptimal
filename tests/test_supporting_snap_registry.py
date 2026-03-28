from car_model.setup_registry import snap_supporting_field_value
from car_model.cars import get_car


def test_snap_supporting_field_value_numeric_resolution():
    car = get_car("bmw")
    assert snap_supporting_field_value(car, "diff_preload_nm", 43.2) == 45.0
    assert snap_supporting_field_value(car, "brake_bias_migration", 5.9) == 5.0
    assert snap_supporting_field_value(car, "brake_bias_target", -5.9) == -5.0


def test_snap_supporting_field_value_discrete_options():
    car = get_car("bmw")
    assert snap_supporting_field_value(car, "tc_gain", 10.9) == 10
    assert snap_supporting_field_value(car, "tc_slip", 0.1) == 1
    assert snap_supporting_field_value(car, "diff_clutch_plates", 5.4) == 6
    assert snap_supporting_field_value(car, "diff_ramp_option_idx", 10) == 2
    assert snap_supporting_field_value(car, "front_master_cyl_mm", 20.8) == 20.6


def test_snap_supporting_field_value_non_numeric_passthrough_and_pad_compound():
    car = get_car("bmw")
    assert snap_supporting_field_value(car, "gear_stack", "long") == "long"
    assert snap_supporting_field_value(car, "pad_compound", "unknown") == "Medium"
