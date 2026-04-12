"""Validate aero map parsed data has physically plausible values.

Guards against corruption like the Porsche wing 13 bug where the L/D
first column contained front_rh axis values (25-75) instead of L/D
ratios (~3.4-3.7).
"""

import json
from pathlib import Path

import numpy as np
import pytest

PARSED_DIR = Path(__file__).parent.parent / "data" / "aeromaps_parsed"


def _aero_npz_files():
    """Yield (car_name, npz_path, meta) for each parsed aero file."""
    for json_path in sorted(PARSED_DIR.glob("*_aero.json")):
        car = json_path.stem.replace("_aero", "")
        npz_path = json_path.with_suffix(".npz")
        if not npz_path.exists():
            continue
        meta = json.loads(json_path.read_text())
        yield car, npz_path, meta


@pytest.mark.parametrize(
    "car,npz_path,meta",
    list(_aero_npz_files()),
    ids=[c for c, _, _ in _aero_npz_files()],
)
def test_ld_values_in_range(car, npz_path, meta):
    """All L/D values must be in [1.0, 6.0] for every car and wing angle."""
    data = np.load(str(npz_path))
    for wing in meta["wing_angles"]:
        ld = data[f"ld_{wing}"]
        ld_min = float(np.nanmin(ld))
        ld_max = float(np.nanmax(ld))
        assert ld_min >= 1.0, (
            f"{car} wing {wing}: L/D min={ld_min:.3f} < 1.0"
        )
        assert ld_max <= 6.0, (
            f"{car} wing {wing}: L/D max={ld_max:.3f} > 6.0"
        )


@pytest.mark.parametrize(
    "car,npz_path,meta",
    list(_aero_npz_files()),
    ids=[c for c, _, _ in _aero_npz_files()],
)
def test_balance_values_in_range(car, npz_path, meta):
    """All DF balance values must be in [10.0, 90.0]."""
    data = np.load(str(npz_path))
    for wing in meta["wing_angles"]:
        bal = data[f"balance_{wing}"]
        bal_min = float(np.nanmin(bal))
        bal_max = float(np.nanmax(bal))
        assert bal_min >= 10.0, (
            f"{car} wing {wing}: balance min={bal_min:.2f} < 10.0"
        )
        assert bal_max <= 90.0, (
            f"{car} wing {wing}: balance max={bal_max:.2f} > 90.0"
        )


@pytest.mark.parametrize(
    "car,npz_path,meta",
    list(_aero_npz_files()),
    ids=[c for c, _, _ in _aero_npz_files()],
)
def test_grid_shapes_consistent(car, npz_path, meta):
    """Balance and L/D grids must have matching shapes for each wing."""
    data = np.load(str(npz_path))
    expected_shape = tuple(meta["grid_shape"])
    for wing in meta["wing_angles"]:
        bal_shape = data[f"balance_{wing}"].shape
        ld_shape = data[f"ld_{wing}"].shape
        assert bal_shape == expected_shape, (
            f"{car} wing {wing}: balance shape {bal_shape} != expected {expected_shape}"
        )
        assert ld_shape == expected_shape, (
            f"{car} wing {wing}: L/D shape {ld_shape} != expected {expected_shape}"
        )
