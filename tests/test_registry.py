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
        ("bmw_m4_gt3", "bmw_m4_gt3"),
        ("aston_martin_vantage_gt3", "aston_martin_vantage_gt3"),
        ("porsche_992_gt3r", "porsche_992_gt3r"),
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
        ("BMW M4 GT3 EVO", "bmw_m4_gt3"),
        ("Aston Martin Vantage GT3 EVO", "aston_martin_vantage_gt3"),
        ("Porsche 911 GT3 R (992)", "porsche_992_gt3r"),
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
        ("bmwm4gt3", "bmw_m4_gt3"),
        ("amvantageevogt3", "aston_martin_vantage_gt3"),
        ("porsche992rgt3", "porsche_992_gt3r"),
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
        # 5 GTP cars + 3 GT3 cars (W1.3 added the GT3 entries to _CAR_REGISTRY).
        assert len(names) == 8
        assert "BMW M Hybrid V8" in names
        assert "Porsche 963" in names
        assert "BMW M4 GT3 EVO" in names
        assert "Aston Martin Vantage GT3 EVO" in names
        assert "Porsche 911 GT3 R (992)" in names

    def test_returns_strings(self):
        for name in supported_car_names():
            assert isinstance(name, str)


# ─── GT3 routing regression guards ──────────────────────────────────────────

class TestGT3RoutingRegression:
    """Pin the W1.3 fix: GT3 names MUST NOT silently resolve to the GTP BMW.

    Before W1.3, the substring fallback in resolve_car returned the GTP
    ``bmw`` entry for every GT3 input that contained the substring "bmw"
    (e.g. ``"bmwm4gt3"``, ``"BMW M4 GT3 EVO"``).  Every GT3 IBT therefore
    inherited the GTP BMW spec set, silently corrupting learner observations
    and emitting wrong setups.  These tests pin that the longest-match-wins
    rule now picks the GT3-specific entry.
    """

    def test_bmw_m4_gt3_screen_name_does_not_route_to_gtp(self):
        result = resolve_car("BMW M4 GT3 EVO")
        assert result is not None
        assert result.canonical == "bmw_m4_gt3"
        assert result.canonical != "bmw", "regression: GT3 name resolved to GTP BMW"

    def test_bmw_m4_gt3_sto_id_does_not_route_to_gtp(self):
        result = resolve_car("bmwm4gt3")
        assert result is not None
        assert result.canonical == "bmw_m4_gt3"
        assert result.sto_id == "bmwm4gt3"

    def test_aston_screen_name(self):
        result = resolve_car("Aston Martin Vantage GT3 EVO")
        assert result is not None
        assert result.canonical == "aston_martin_vantage_gt3"

    def test_aston_sto_id(self):
        result = resolve_car("amvantageevogt3")
        assert result is not None
        assert result.canonical == "aston_martin_vantage_gt3"

    def test_porsche_992_gt3r_screen_name_does_not_route_to_gtp(self):
        # Critical: must NOT return the GTP Porsche 963.
        result = resolve_car("Porsche 911 GT3 R (992)")
        assert result is not None
        assert result.canonical == "porsche_992_gt3r"
        assert result.canonical != "porsche", "regression: GT3 R name resolved to GTP 963"

    def test_porsche_992_gt3r_sto_id_does_not_route_to_gtp(self):
        result = resolve_car("porsche992rgt3")
        assert result is not None
        assert result.canonical == "porsche_992_gt3r"
        assert result.sto_id == "porsche992rgt3"

    def test_gtp_bmw_still_resolves(self):
        """Regression check: adding GT3 entries did not break GTP BMW lookup."""
        result = resolve_car("BMW M Hybrid V8")
        assert result is not None
        assert result.canonical == "bmw"
        assert result.sto_id == "bmwlmdh"

    def test_gtp_porsche_still_resolves(self):
        """Regression check: adding ``porsche_992_gt3r`` did not steal GTP Porsche."""
        result = resolve_car("Porsche 963")
        assert result is not None
        assert result.canonical == "porsche"
        assert result.sto_id == "porsche963"
