"""Mission compliance — enforces MISSION.md principles.

Failing tests mean someone reintroduced a forbidden pattern, or a needed unit
hasn't landed yet. Many tests will SKIP until upstream units (M1, F1, F2, F3,
D1, C1, etc.) ship — the skip messages explain which unit must land first.

These tests are intentionally strict. They're meant to make the build fail
when the codebase drifts from the 6 mission principles laid out in MISSION.md
(authored by Unit M1):

    1. Every lap is data
    2. Physics-first (no preserve-driver default)
    3. No hardcoded fallbacks
    4. Continuous learning (confidence tiers)
    5. Coupled evaluation
    6. Corner-by-corner causal

Running: ``pytest tests/test_mission_compliance.py -v``
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from car_model.cars import _CARS

REPO = Path(__file__).resolve().parents[1]
SOLVER_DIR = REPO / "solver"
CALIBRATION_DIR = REPO / "data" / "calibration"
ALL_CARS = ("bmw", "porsche", "ferrari", "cadillac", "acura")
LEGAL_TIERS = frozenset({"high", "medium", "low", "insufficient"})


# ─── Helpers ────────────────────────────────────────────────────────────────


def _solver_python_files() -> list[Path]:
    """Return solver/*.py files (excluding __init__ and __pycache__)."""
    if not SOLVER_DIR.is_dir():
        return []
    return [
        p
        for p in SOLVER_DIR.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    ]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_models_json(car: str) -> dict | None:
    path = CALIBRATION_DIR / car / "models.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_calibration_points(car: str) -> list[dict] | None:
    path = CALIBRATION_DIR / car / "calibration_points.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


# ─── Principle 3: No hardcoded fallbacks ────────────────────────────────────


class TestPrincipleNoHardcodedFallbacks:
    """Principle 3: solver code must not silently fall back to BMW defaults
    or other hardcoded magic numbers when a car field is missing.

    The user-stated rule: ``no fallbacks to baselines or hardcoded values``.
    See CLAUDE.md Key Principles 7 + 8.
    """

    def test_no_getattr_with_numeric_default_in_solver(self):
        """Solver files must not use ``getattr(car, 'field', <numeric>)``.

        These are the dangerous silent fallbacks the user banned: when ``car``
        doesn't carry the field, the solver pretends the BMW value is OK.
        Use direct attribute access (``car.field``) so missing fields raise
        AttributeError loudly instead.

        Failing this test means F2 (no-hardcoded-fallbacks unit) has not
        cleaned the latest violation. Either remove the getattr or hoist
        the field onto the car model.
        """
        # Match getattr(<expr containing 'car'>, "name", <number>) — both int and float.
        forbidden = re.compile(
            r"getattr\s*\(\s*[^,]*\bcar\b[^,]*,\s*[\"']\w+[\"']\s*,\s*-?\d+(?:\.\d+)?\s*\)"
        )
        violations: list[str] = []
        for solver_file in _solver_python_files():
            text = _read_text(solver_file)
            for match in forbidden.finditer(text):
                line_num = text[: match.start()].count("\n") + 1
                violations.append(f"{solver_file.name}:{line_num}: {match.group(0)}")
        assert not violations, (
            "Forbidden getattr-with-numeric-default patterns detected — these are\n"
            "silent BMW-default fallbacks the user banned. Replace with direct\n"
            "attribute access, or hoist the field onto the car model.\n\n"
            + "\n".join(violations)
        )

    def test_no_bmw_magic_numbers_as_solver_fallbacks(self):
        """Specific BMW magic numbers (track widths, mass) must not appear
        as ``or <number>`` or ``... <name> = <number>`` solver fallbacks.

        These constants are BMW-specific (track_width_front_mm=1730,
        track_width_rear_mm=1650, master cylinder 19.1/20.6 mm). When they
        appear next to ``or`` or as right-hand-side defaults in a solver
        file, that's a smoking gun for a silent BMW-leak.

        This test scans for the literal numbers next to ``or`` so it catches
        ``foo = car.track_width or 1730.0`` patterns. It deliberately does
        NOT scan car_model/ — those files legitimately define BMW values.
        """
        # Each tuple: (literal, suffix-pattern). We look for ``or <literal>``
        # immediately preceding the literal (one of the most common silent-
        # fallback shapes) or the literal as a default after an ``=``.
        bmw_magics = ["1730.0", "1650.0", "1730", "1650"]
        violations: list[str] = []
        for solver_file in _solver_python_files():
            text = _read_text(solver_file)
            for magic in bmw_magics:
                # Match ``or 1730.0`` or ``or  1730.0`` (whitespace-tolerant).
                pattern = re.compile(rf"\bor\s+{re.escape(magic)}\b")
                for match in pattern.finditer(text):
                    line_num = text[: match.start()].count("\n") + 1
                    line_text = text.split("\n")[line_num - 1].strip()
                    violations.append(
                        f"{solver_file.name}:{line_num}: {line_text}"
                    )
        assert not violations, (
            "BMW magic-number fallbacks detected in solver/. Use car.* fields\n"
            "directly; if the field is missing on some cars, hoist it.\n\n"
            + "\n".join(violations)
        )


# ─── Principle 2: Physics-first / no preserve-driver default ────────────────


class TestPrincipleNoPreserveDriverDefault:
    """Principle 2: when physics has a calibrated answer, the solver must
    output it. Falling back to the driver-loaded value is allowed only as
    a last-resort and must be tagged with an explicit ``[FALLBACK]`` /
    ``preserve_driver`` provenance string so the user can audit it.
    """

    def test_no_silent_preserve_driver_in_solvers(self):
        """No solver file may use ``preserve_driver`` provenance without
        also writing a ``FALLBACK`` warning marker on the same span.

        After F1 lands, every preserve-driver path should be tagged with a
        loud warning so the user can see it. This test passes today (no
        ``preserve_driver`` strings exist yet) and will start enforcing the
        contract once F1 introduces them.
        """
        if not any(
            "preserve_driver" in _read_text(p) for p in _solver_python_files()
        ):
            pytest.skip(
                "No preserve_driver provenance strings in solver/ yet — F1 "
                "(physics-first preservation tagging) has not landed."
            )
        violations: list[str] = []
        for solver_file in _solver_python_files():
            text = _read_text(solver_file)
            for m in re.finditer(r"preserve_driver", text):
                # Look at a 200-char window around the match for a FALLBACK marker.
                start = max(0, m.start() - 200)
                end = min(len(text), m.end() + 200)
                window = text[start:end]
                if "FALLBACK" not in window and "fallback" not in window:
                    line_num = text[: m.start()].count("\n") + 1
                    violations.append(f"{solver_file.name}:{line_num}")
        assert not violations, (
            "preserve_driver used without FALLBACK marker — Principle 2\n"
            "requires every preserve-driver path to be tagged loudly.\n\n"
            + "\n".join(violations)
        )


# ─── Principle 4: Continuous learning / confidence tiers ────────────────────


class TestPrincipleConfidenceTiers:
    """Principle 4: every fitted regression must carry a confidence_tier
    so downstream consumers can degrade gracefully when calibration is
    weak instead of trusting an over-fit model blindly.
    """

    @pytest.mark.parametrize("car", ALL_CARS)
    def test_models_json_has_tier_field(self, car: str):
        """Every model dict in ``data/calibration/<car>/models.json`` that
        has an ``r_squared`` field must also carry ``confidence_tier``.

        This will FAIL until F3 (confidence-tier propagation unit) lands
        and re-fits each car. Treat the failure as a TODO marker.
        """
        models = _load_models_json(car)
        if models is None:
            pytest.skip(f"{car}: models.json missing or unparseable")

        missing: list[str] = []
        for key, val in models.items():
            if not isinstance(val, dict):
                continue
            if "r_squared" not in val:
                continue
            if "confidence_tier" not in val:
                missing.append(f"{car}/{key}")
        assert not missing, (
            "Calibrated models without confidence_tier — F3 (confidence-tier\n"
            "propagation) needs to land and re-fit these car/track pairs.\n\n"
            + "\n".join(missing)
        )

    @pytest.mark.parametrize("car", ALL_CARS)
    def test_tier_values_are_one_of_known_set(self, car: str):
        """If confidence_tier is set, it must be one of the four legal
        values. Anything else is a typo or a forgotten enum-mapping.
        """
        models = _load_models_json(car)
        if models is None:
            pytest.skip(f"{car}: models.json missing")

        bad: list[str] = []
        for key, val in models.items():
            if not isinstance(val, dict):
                continue
            tier = val.get("confidence_tier")
            if tier is None:
                continue
            if tier not in LEGAL_TIERS:
                bad.append(f"{car}/{key}: tier={tier!r}")
        assert not bad, (
            f"confidence_tier values must be in {sorted(LEGAL_TIERS)}; got:\n"
            + "\n".join(bad)
        )


# ─── Principle 1: Per-lap data ──────────────────────────────────────────────


class TestPrinciplePerLapData:
    """Principle 1: every lap is data. After D1 lands, ingestion produces
    one CalibrationPoint per lap (not per session) so within-stint dynamics
    enter the regression instead of being averaged away.
    """

    @pytest.mark.parametrize("car", ALL_CARS)
    def test_per_lap_calibration_points_present(self, car: str):
        """At least one CalibrationPoint per car must carry a non-zero
        ``lap_number``. Today every existing point omits the field, so this
        test will SKIP for every car (signalling D1 hasn't shipped) instead
        of failing the build.
        """
        pts = _load_calibration_points(car)
        if pts is None:
            pytest.skip(f"{car}: no calibration_points.json")
        if not pts:
            pytest.skip(f"{car}: calibration_points.json is empty")

        # Skip-with-message until D1 lands. Once D1 ships, the assertion below
        # turns this into a hard failure until ingestion is rerun.
        if not any("lap_number" in p for p in pts):
            pytest.skip(
                f"{car}: per-lap CalibrationPoints not yet emitted — D1 "
                "(every-lap ingestion) needs to land and re-ingest IBTs."
            )

        lap_numbers = {p.get("lap_number", 0) for p in pts}
        assert any(ln and ln > 0 for ln in lap_numbers), (
            f"{car}: lap_number field exists but every value is 0/None — "
            "D1 may have regressed."
        )


# ─── Principle 3 (companion): cars carry explicit fields, not defaults ──────


class TestPrincipleCarModelsHaveExplicitFields:
    """Principle 3: every car must declare its own values for fields the
    solver consumes. Sharing a class-level default between cars is the
    same anti-pattern as a getattr-with-numeric-default — silent BMW leak.
    """

    @pytest.mark.parametrize("car_name", ALL_CARS)
    def test_track_width_fields_explicit(self, car_name: str):
        """Every car's ARBModel must carry positive front+rear track widths.
        These feed every roll-stiffness computation; a missing value would
        silently fall back to BMW (1730/1650 mm).
        """
        car = _CARS[car_name]
        assert car.arb is not None, f"{car_name}: arb missing entirely"
        front = getattr(car.arb, "track_width_front_mm", 0.0)
        rear = getattr(car.arb, "track_width_rear_mm", 0.0)
        assert front and front > 0, (
            f"{car_name}: track_width_front_mm missing or zero "
            f"(got {front!r})"
        )
        assert rear and rear > 0, (
            f"{car_name}: track_width_rear_mm missing or zero "
            f"(got {rear!r})"
        )

    @pytest.mark.parametrize("car_name", ALL_CARS)
    def test_master_cylinder_fields_explicit(self, car_name: str):
        """Every car's brake master-cylinder option list (on
        ``car.garage_ranges``) must be non-empty and contain only positive
        diameters. An empty list silently lets the BMW default leak in.
        """
        car = _CARS[car_name]
        gr = getattr(car, "garage_ranges", None)
        assert gr is not None, f"{car_name}: garage_ranges missing entirely"
        opts = getattr(gr, "brake_master_cyl_options_mm", None)
        assert opts is not None, (
            f"{car_name}: brake_master_cyl_options_mm not declared on "
            f"garage_ranges"
        )
        assert isinstance(opts, list) and len(opts) > 0, (
            f"{car_name}: brake_master_cyl_options_mm empty (would leak BMW "
            f"defaults). Got {opts!r}"
        )
        assert all(isinstance(v, (int, float)) and v > 0 for v in opts), (
            f"{car_name}: brake_master_cyl_options_mm has non-positive "
            f"entries: {opts!r}"
        )

    @pytest.mark.parametrize("car_name", ALL_CARS)
    def test_total_mass_explicit(self, car_name: str):
        """Mass is a Tier-A physics input (used everywhere in the solver).
        Every car must declare its own positive ``mass_car_kg`` — this
        field has no default in CarModel, so a missing value would crash
        the constructor, but a regression that introduces a default would
        silently leak BMW values to other cars. This test guards against
        that.
        """
        car = _CARS[car_name]
        mass = getattr(car, "mass_car_kg", None)
        assert mass is not None and mass > 0, (
            f"{car_name}: mass_car_kg missing or non-positive (got {mass!r})"
        )


# ─── Principle 6: Corner-by-corner causal report sections ───────────────────


class TestPrincipleEngineeringReportSections:
    """Principle 6: every engineering report must include a per-corner
    impact section and a "why this candidate was chosen" section. These
    sections are introduced by C1 (coupled evaluation) and C2 (corner
    causal report) — until those land, both tests SKIP.
    """

    def _find_report_text(self) -> str | None:
        """Search the repo for the most recent engineering-report module
        text so the section-header tests can grep without invoking pipeline.
        """
        candidates = [
            REPO / "pipeline" / "report.py",
            REPO / "output" / "report.py",
        ]
        for path in candidates:
            if path.exists():
                return _read_text(path)
        return None

    def test_per_corner_impact_section_present(self):
        """The engineering report module must reference a PER-CORNER IMPACT
        section header so it appears whenever the report is rendered with
        corner-segmentation data.
        """
        text = self._find_report_text()
        if text is None:
            pytest.skip("No engineering report module found at expected paths")
        if "PER-CORNER IMPACT" not in text and "PER CORNER IMPACT" not in text:
            pytest.skip(
                "PER-CORNER IMPACT section not present in report.py — C2 "
                "(corner-by-corner causal report) needs to land."
            )

    def test_parameter_coupling_section_present(self):
        """After C1 lands, every report must show a coupling-cascade section
        so the reader can see which other parameters changed in response to
        each step's output.
        """
        text = self._find_report_text()
        if text is None:
            pytest.skip("No engineering report module found at expected paths")
        markers = (
            "WHY THIS CANDIDATE",
            "COUPLING CASCADE",
            "PARAMETER COUPLING",
        )
        if not any(m in text for m in markers):
            pytest.skip(
                "Coupling/why-this-candidate section not present in report.py — "
                "C1 (coupled evaluation) needs to land."
            )
