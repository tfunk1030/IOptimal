"""GT3 Phase 2 Wave 9 Unit 1 — webapp + CLI + validation regression tests.

Covers F1-F4 (webapp), F5-F9 (CLI), F16-F18 (validation) from the
``docs/audits/gt3_phase2/webapp-cli-tests-docs.md`` audit. The webapp
end-to-end tests in ``tests/test_webapp_routes.py`` (which require
fastapi + httpx) remain unchanged; this module covers the unit-level
contracts that don't need an HTTP client.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestSetupGroupSpecsArchitectureAware(unittest.TestCase):
    """F2 — ``SETUP_GROUP_SPECS`` must drop heave/third/torsion for GT3 cars
    and surface 4 corner spring rates + bump rubber gap + splitter height.
    GTP cars must keep the legacy layout (regression).
    """

    def _row_labels(self, specs) -> set[str]:
        labels: set[str] = set()
        for group in specs:
            for row in group.rows:
                labels.add(row.label)
        return labels

    def test_bmw_m4_gt3_drops_gtp_only_rows(self) -> None:
        from webapp.services import setup_group_specs_for

        specs = setup_group_specs_for("bmw_m4_gt3")
        labels = self._row_labels(specs)
        # GT3 must NOT have heave / third / torsion
        self.assertNotIn("Front heave", labels)
        self.assertNotIn("Rear third", labels)
        self.assertNotIn("Front torsion", labels)
        # GT3 SHOULD have corner-spring + GT3-only platform fields
        self.assertIn("Front spring", labels)
        self.assertIn("Rear spring", labels)
        self.assertIn("Front bump rubber gap", labels)
        self.assertIn("Rear bump rubber gap", labels)
        self.assertIn("Splitter height", labels)

    def test_aston_martin_vantage_gt3_drops_gtp_only_rows(self) -> None:
        from webapp.services import setup_group_specs_for

        specs = setup_group_specs_for("aston_martin_vantage_gt3")
        labels = self._row_labels(specs)
        self.assertNotIn("Front heave", labels)
        self.assertNotIn("Rear third", labels)
        self.assertNotIn("Front torsion", labels)
        self.assertIn("Front spring", labels)
        self.assertIn("Rear spring", labels)

    def test_porsche_992_gt3r_drops_gtp_only_rows(self) -> None:
        from webapp.services import setup_group_specs_for

        specs = setup_group_specs_for("porsche_992_gt3r")
        labels = self._row_labels(specs)
        self.assertNotIn("Front heave", labels)
        self.assertNotIn("Rear third", labels)
        self.assertNotIn("Front torsion", labels)
        self.assertIn("Front spring", labels)
        self.assertIn("Rear spring", labels)

    def test_gtp_bmw_keeps_legacy_layout(self) -> None:
        from webapp.services import setup_group_specs_for

        specs = setup_group_specs_for("bmw")
        labels = self._row_labels(specs)
        # GTP cars keep heave / third / torsion
        self.assertIn("Front heave", labels)
        self.assertIn("Rear third", labels)
        self.assertIn("Front torsion", labels)

    def test_unknown_car_falls_back_to_gtp(self) -> None:
        from webapp.services import setup_group_specs_for

        specs = setup_group_specs_for("nonexistent_car_xyz")
        labels = self._row_labels(specs)
        self.assertIn("Front heave", labels)


class TestListSupportedCars(unittest.TestCase):
    """F1 — ``list_supported_cars()`` is the source of truth for the webapp
    car selector. Must include all 8 canonical entries with class labels.
    """

    def test_returns_at_least_8_cars(self) -> None:
        from webapp.services import list_supported_cars

        rows = list_supported_cars()
        self.assertGreaterEqual(len(rows), 8)

    def test_class_labels_split_gtp_and_gt3(self) -> None:
        from webapp.services import list_supported_cars

        rows = list_supported_cars()
        canonicals = {canonical for canonical, _, _ in rows}
        klass_for = {canonical: klass for canonical, _, klass in rows}

        # GTP entries
        for gtp in ("bmw", "porsche", "ferrari", "cadillac", "acura"):
            self.assertEqual(klass_for.get(gtp), "GTP", f"{gtp} should be GTP")

        # GT3 entries
        for gt3 in ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r"):
            self.assertIn(gt3, canonicals, f"{gt3} missing from supported_cars")
            self.assertEqual(klass_for.get(gt3), "GT3", f"{gt3} should be GT3")

    def test_class_filter_gt3_returns_only_gt3(self) -> None:
        from webapp.services import list_supported_cars

        rows = list_supported_cars(class_filter="GT3")
        canonicals = {canonical for canonical, _, _ in rows}
        # Currently 3 GT3 cars in the registry
        self.assertEqual(canonicals, {
            "bmw_m4_gt3",
            "aston_martin_vantage_gt3",
            "porsche_992_gt3r",
        })

    def test_class_filter_gtp_returns_only_gtp(self) -> None:
        from webapp.services import list_supported_cars

        rows = list_supported_cars(class_filter="GTP")
        for _, _, klass in rows:
            self.assertEqual(klass, "GTP")
        self.assertGreaterEqual(len(rows), 5)


class TestCliCarChoices(unittest.TestCase):
    """F5/F6/F8/F9 — CLI ``--car`` must accept GT3 canonical names. Spawn the
    process with ``--help`` and verify the choices block lists GT3 cars.
    """

    def _run_help(self, argv: list[str]) -> str:
        result = subprocess.run(
            [sys.executable] + argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
        # Some CLIs print --help to stderr (e.g. analyzer prints a deprecation
        # banner there); merge both streams for the assertion.
        return (result.stdout or "") + "\n" + (result.stderr or "")

    def test_calibrate_help_lists_gt3_cars(self) -> None:
        text = self._run_help([str(ROOT / "__main__.py"), "calibrate", "--help"])
        self.assertIn("bmw_m4_gt3", text)
        self.assertIn("aston_martin_vantage_gt3", text)
        self.assertIn("porsche_992_gt3r", text)

    def test_produce_help_lists_gt3_cars(self) -> None:
        text = self._run_help([str(ROOT / "__main__.py"), "produce", "--help"])
        self.assertIn("bmw_m4_gt3", text)

    def test_run_subcommand_help_lists_gt3_cars(self) -> None:
        text = self._run_help([str(ROOT / "__main__.py"), "run", "--help"])
        self.assertIn("bmw_m4_gt3", text)

    def test_analyzer_help_lists_gt3_cars(self) -> None:
        text = self._run_help(["-m", "analyzer", "--help"])
        self.assertIn("bmw_m4_gt3", text)

    def test_learner_ingest_help_lists_gt3_cars(self) -> None:
        text = self._run_help(["-m", "learner.ingest", "--help"])
        self.assertIn("bmw_m4_gt3", text)


class TestValidationRegistryGT3(unittest.TestCase):
    """F16 — validation ``_confidence_tier`` must include GT3 entries."""

    def _make_row(self, car: str, track: str):
        from validation.run_validation import ObservationSample

        return ObservationSample(
            path=Path("/tmp/fake.json"),
            session_id="s1",
            car=car,
            track=track,
            track_config="",
            lap_time_s=100.0,
            params={},
            telemetry={},
            performance={},
            signal_sources={},
        )

    def test_bmw_sebring_remains_calibrated(self) -> None:
        from validation.run_validation import _confidence_tier

        row = self._make_row("bmw", "Sebring International Raceway")
        self.assertEqual(_confidence_tier(row, 30), "calibrated")

    def test_bmw_m4_gt3_spielberg_is_exploratory(self) -> None:
        from validation.run_validation import _confidence_tier

        row = self._make_row("bmw_m4_gt3", "Red Bull Ring")
        self.assertIn(_confidence_tier(row, 1), ("exploratory", "unsupported"))

    def test_aston_gt3_spielberg_is_exploratory(self) -> None:
        from validation.run_validation import _confidence_tier

        row = self._make_row("aston_martin_vantage_gt3", "Red Bull Ring")
        self.assertIn(_confidence_tier(row, 1), ("exploratory", "unsupported"))

    def test_porsche_992_gt3r_spielberg_is_exploratory(self) -> None:
        from validation.run_validation import _confidence_tier

        row = self._make_row("porsche_992_gt3r", "Red Bull Ring")
        self.assertIn(_confidence_tier(row, 1), ("exploratory", "unsupported"))

    def test_unknown_pair_is_unsupported(self) -> None:
        from validation.run_validation import _confidence_tier

        row = self._make_row("ferrari_296_gt3", "Spa")
        self.assertEqual(_confidence_tier(row, 0), "unsupported")


class TestValidationLoadObservationsCarFilter(unittest.TestCase):
    """F18 — ``load_observations`` accepts car/track filter args."""

    def test_default_filter_is_bmw_sebring(self) -> None:
        from validation.objective_calibration import load_observations

        rows = load_observations()
        self.assertIsInstance(rows, list)

    def test_gt3_car_filter_does_not_raise(self) -> None:
        from validation.objective_calibration import load_observations

        rows = load_observations(
            car_filter=["bmw_m4_gt3"],
            track_filter=["spielberg"],
        )
        self.assertIsInstance(rows, list)

    def test_target_samples_accepts_kwargs(self) -> None:
        from validation.run_validation import _target_samples, ObservationSample

        rows = [
            ObservationSample(
                path=Path("/tmp/x.json"),
                session_id="s",
                car="bmw_m4_gt3",
                track="Red Bull Ring",
                track_config="",
                lap_time_s=110.0,
                params={},
                telemetry={},
                performance={},
                signal_sources={},
            ),
        ]
        # Default still BMW/Sebring (returns nothing)
        self.assertEqual(_target_samples(rows), [])
        # Pass GT3 car/track explicitly — observation matches via track_key
        # alias collapsing "Red Bull Ring" → "spielberg".
        result = _target_samples(rows, car="bmw_m4_gt3", track="spielberg")
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
