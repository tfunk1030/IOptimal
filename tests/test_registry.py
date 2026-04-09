"""Tests for the unified car and track name registry."""

import pytest

from car_model.registry import (
    CarIdentity,
    TrackIdentity,
    resolve_car,
    supported_car_names,
    track_slug,
)


# ─── resolve_car ────────────────────────────────────────────────────────────

class TestResolveCar:
    """Test car name resolution from all known string forms."""

    @pytest.mark.parametrize("name,expected_canonical", [
        # Canonical names
        ("bmw", "bmw"),
        ("porsche", "porsche"),
        ("ferrari", "ferrari"),
        ("cadillac", "cadillac"),
        ("acura", "acura"),
    ])
    def test_canonical(self, name, expected_canonical):
        result = resolve_car(name)
        assert result is not None
        assert result.canonical == expected_canonical

    @pytest.mark.parametrize("name,expected_canonical", [
        # iRacing screen names
        ("BMW M Hybrid V8", "bmw"),
        ("Porsche 963", "porsche"),
        ("Ferrari 499P", "ferrari"),
        ("Cadillac V-Series.R", "cadillac"),
        ("Acura ARX-06", "acura"),
    ])
    def test_screen_name(self, name, expected_canonical):
        result = resolve_car(name)
        assert result is not None
        assert result.canonical == expected_canonical

    @pytest.mark.parametrize("name,expected_canonical", [
        # STO binary car IDs
        ("bmwlmdh", "bmw"),
        ("porsche963", "porsche"),
        ("ferrari499p", "ferrari"),
        ("cadillacvseriesr", "cadillac"),
        ("acuraarx06gtp", "acura"),
    ])
    def test_sto_id(self, name, expected_canonical):
        result = resolve_car(name)
        assert result is not None
        assert result.canonical == expected_canonical

    @pytest.mark.parametrize("name,expected_canonical", [
        # Case-insensitive fallback
        ("BMW", "bmw"),
        ("PORSCHE", "porsche"),
        ("bmw m hybrid v8", "bmw"),
        ("FERRARI 499P", "ferrari"),
        ("BMWLMDH", "bmw"),
    ])
    def test_case_insensitive(self, name, expected_canonical):
        result = resolve_car(name)
        assert result is not None
        assert result.canonical == expected_canonical

    def test_unknown_car(self):
        assert resolve_car("McLaren 720S GT3") is None
        assert resolve_car("") is None
        assert resolve_car("unknown") is None

    def test_identity_fields(self):
        result = resolve_car("bmw")
        assert result is not None
        assert result.display_name == "BMW M Hybrid V8"
        assert result.screen_name == "BMW M Hybrid V8"
        assert result.sto_id == "bmwlmdh"
        assert result.aero_folder == "bmw"

    def test_identity_is_frozen(self):
        result = resolve_car("bmw")
        with pytest.raises(AttributeError):
            result.canonical = "changed"


# ─── track_slug ─────────────────────────────────────────────────────────────

class TestTrackSlug:
    def test_with_config(self):
        assert track_slug("Sebring International Raceway", "International") == \
            "sebring_international_raceway_international"

    def test_without_config(self):
        assert track_slug("Sebring International Raceway") == \
            "sebring_international_raceway"

    def test_empty_config(self):
        assert track_slug("Sebring International Raceway", "") == \
            "sebring_international_raceway"

    def test_spa(self):
        assert track_slug("Circuit de Spa-Francorchamps", "Endurance") == \
            "circuit_de_spa-francorchamps_endurance"

    def test_algarve(self):
        assert track_slug("Algarve International Circuit", "Grand Prix") == \
            "algarve_international_circuit_grand_prix"


# ─── supported_car_names ────────────────────────────────────────────────────

class TestSupportedCarNames:
    def test_returns_all_display_names(self):
        names = supported_car_names()
        assert len(names) == 5
        assert "BMW M Hybrid V8" in names
        assert "Porsche 963" in names

    def test_returns_strings(self):
        for name in supported_car_names():
            assert isinstance(name, str)
