"""GT3 Phase 2 — Wave 8 Unit 1 (W8.1) tests.

Verifies the audit BLOCKER findings F1, F2, F3, F6 + DEGRADED F8, F10, F11
from ``docs/audits/gt3_phase2/infra-teamdb-watcher-desktop.md`` are fixed:

* `CarDefinition` carries `iracing_car_path`, `bop_version`, `suspension_arch`.
* `Observation` carries `suspension_arch` (NOT NULL, default GTP-torsion),
  `bop_version`, `iracing_car_path`.
* `aggregate_observations()` partitions by `suspension_arch` so a mixed
  GTP+GT3 list does not co-fit; the wrong-arch rows are dropped.
* The track key in the aggregator goes through
  ``car_model.registry.track_key`` (handles multi-word tracks like
  "Red Bull Ring", not just "red").
* `compute_support_tier(arch="gt3_coil_4wheel", n=10)` returns "partial"
  per the per-arch threshold table.
* The migration script exists at ``migrations/0001_gt3_phase2.sql`` with
  the expected ALTER TABLE statements.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from teamdb.aggregator import (
    _TIER_THRESHOLDS_BY_ARCH,
    aggregate_observations,
    compute_support_tier,
)
from teamdb.models import CarDefinition, Observation


# ─── helpers ─────────────────────────────────────────────────────────────


def _gtp_obs(session_id: str, *, car: str = "bmw") -> dict:
    """Minimal observation dict shaped for the aggregator's path."""
    return {
        "session_id": session_id,
        "car": car,
        "track": "Sebring International Raceway",
        "suspension_arch": "gtp_heave_third_torsion_front",
        "setup": {},
        "performance": {},
        "telemetry_summary": {},
        "diagnosis": {},
        "driver_style": {},
        "track_demand": {},
    }


def _gt3_obs(session_id: str, *, car: str = "bmw_m4_gt3") -> dict:
    return {
        "session_id": session_id,
        "car": car,
        "track": "Red Bull Ring",
        "suspension_arch": "gt3_coil_4wheel",
        "setup": {},
        "performance": {},
        "telemetry_summary": {},
        "diagnosis": {},
        "driver_style": {},
        "track_demand": {},
    }


# ─── F1 + F2: schema columns exist ───────────────────────────────────────


class TestSchemaColumns(unittest.TestCase):
    """F1 + F2: the new columns are wired on the SQLAlchemy models."""

    def test_car_definition_has_iracing_car_path(self):
        col = CarDefinition.__table__.columns.get("iracing_car_path")
        self.assertIsNotNone(col, "CarDefinition.iracing_car_path missing (F1)")
        self.assertTrue(col.nullable)

    def test_car_definition_has_bop_version(self):
        col = CarDefinition.__table__.columns.get("bop_version")
        self.assertIsNotNone(col, "CarDefinition.bop_version missing (F1)")
        self.assertTrue(col.nullable)

    def test_car_definition_has_suspension_arch(self):
        col = CarDefinition.__table__.columns.get("suspension_arch")
        self.assertIsNotNone(col, "CarDefinition.suspension_arch missing (F1)")

    def test_car_definition_indexes_present(self):
        index_names = {idx.name for idx in CarDefinition.__table__.indexes}
        self.assertIn("ix_car_definitions_iracing_path", index_names)
        self.assertIn("ix_car_definitions_arch", index_names)

    def test_observation_has_suspension_arch_not_null(self):
        col = Observation.__table__.columns.get("suspension_arch")
        self.assertIsNotNone(col, "Observation.suspension_arch missing (F2)")
        self.assertFalse(col.nullable, "Observation.suspension_arch must be NOT NULL")
        # The column carries a default for backward-compat with legacy GTP rows.
        # SQLAlchemy stores it under ``col.default.arg`` for scalar defaults.
        default = col.default
        self.assertIsNotNone(default, "Observation.suspension_arch must have a default")
        self.assertEqual(default.arg, "gtp_heave_third_torsion_front")

    def test_observation_has_bop_version(self):
        col = Observation.__table__.columns.get("bop_version")
        self.assertIsNotNone(col, "Observation.bop_version missing (F8)")
        self.assertTrue(col.nullable)

    def test_observation_has_iracing_car_path(self):
        col = Observation.__table__.columns.get("iracing_car_path")
        self.assertIsNotNone(col, "Observation.iracing_car_path missing (F2)")
        self.assertTrue(col.nullable)

    def test_observation_arch_track_index(self):
        index_names = {idx.name for idx in Observation.__table__.indexes}
        self.assertIn("ix_observations_team_arch_track", index_names)


# ─── F3: aggregator partitions by suspension_arch ────────────────────────


class _StubModelSet:
    """Stand-in for ``learner.empirical_models.EmpiricalModelSet``.

    Records which observations the fitter received so tests can assert
    only the matching-arch rows were forwarded.
    """

    last_observations: list[dict] | None = None
    last_car: str | None = None
    last_track: str | None = None

    def __init__(self, observations: list[dict], car: str, track: str):
        type(self).last_observations = observations
        type(self).last_car = car
        type(self).last_track = track
        self.corrections = {}

    def to_dict(self) -> dict:
        return {"observations_seen": len(self.last_observations or [])}


def _stub_fit_models(observations, deltas, car, track):
    return _StubModelSet(observations, car, track)


def _stub_detect_delta(*_args, **_kwargs):
    class _D:
        def to_dict(self):
            return {}
    return _D()


def _stub_observation_from_dict(d):
    return d  # The detect_delta stub doesn't read it.


class TestArchPartitioning(unittest.TestCase):
    """F3: GT3 + GTP rows must not be co-fitted."""

    def setUp(self):
        # Reset the stub class-level state between tests.
        _StubModelSet.last_observations = None
        _StubModelSet.last_car = None
        _StubModelSet.last_track = None

    def _patch_learner(self):
        return (
            patch("learner.empirical_models.fit_models", _stub_fit_models),
            patch("learner.delta_detector.detect_delta", _stub_detect_delta),
            patch(
                "learner.observation.Observation.from_dict",
                _stub_observation_from_dict,
            ),
        )

    def test_gtp_call_drops_gt3_rows(self):
        mixed = [
            _gtp_obs("gtp1"),
            _gtp_obs("gtp2"),
            _gt3_obs("gt3a"),
            _gt3_obs("gt3b"),
            _gt3_obs("gt3c"),
        ]
        patches = self._patch_learner()
        for p in patches:
            p.start()
        try:
            result = aggregate_observations(
                mixed, car="bmw", track="Sebring International Raceway"
            )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(result["observation_count"], 2)
        self.assertEqual(result["suspension_arch"], "gtp_heave_third_torsion_front")
        # Only GTP rows reached the fitter.
        forwarded = _StubModelSet.last_observations or []
        forwarded_archs = {o["suspension_arch"] for o in forwarded}
        self.assertEqual(forwarded_archs, {"gtp_heave_third_torsion_front"})

    def test_gt3_call_drops_gtp_rows(self):
        mixed = [
            _gtp_obs("gtp1"),
            _gtp_obs("gtp2"),
            _gt3_obs("gt3a"),
            _gt3_obs("gt3b"),
            _gt3_obs("gt3c"),
        ]
        patches = self._patch_learner()
        for p in patches:
            p.start()
        try:
            result = aggregate_observations(
                mixed, car="bmw_m4_gt3", track="Red Bull Ring"
            )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(result["observation_count"], 3)
        self.assertEqual(result["suspension_arch"], "gt3_coil_4wheel")
        forwarded = _StubModelSet.last_observations or []
        forwarded_archs = {o["suspension_arch"] for o in forwarded}
        self.assertEqual(forwarded_archs, {"gt3_coil_4wheel"})

    def test_explicit_arch_override_partitions(self):
        """If the caller passes an explicit arch override, honor it even
        when the registry would resolve a different arch for `car`."""
        rows = [_gtp_obs("a"), _gt3_obs("b"), _gt3_obs("c")]
        patches = self._patch_learner()
        for p in patches:
            p.start()
        try:
            result = aggregate_observations(
                rows,
                car="bmw",  # registry says GTP
                track="Sebring International Raceway",
                suspension_arch="gt3_coil_4wheel",  # caller insists GT3
            )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(result["suspension_arch"], "gt3_coil_4wheel")
        self.assertEqual(result["observation_count"], 2)


# ─── F10: aggregator uses canonical track_key ────────────────────────────


class TestTrackKeyHelper(unittest.TestCase):
    """F10: multi-word tracks must not collapse to their first word."""

    def test_aggregator_uses_registry_track_key(self):
        """Track 'Red Bull Ring' must resolve via the registry, not
        ``track.lower().split()[0]`` (which would return ``"red"``)."""
        called: list[str] = []
        from teamdb import aggregator as agg

        original = agg._registry_track_key

        def _spy(name: str) -> str:
            called.append(name)
            return original(name)

        with patch.object(agg, "_registry_track_key", _spy), \
             patch("learner.empirical_models.fit_models", _stub_fit_models), \
             patch("learner.delta_detector.detect_delta", _stub_detect_delta), \
             patch(
                 "learner.observation.Observation.from_dict",
                 _stub_observation_from_dict,
             ):
            aggregate_observations(
                [_gt3_obs("a", car="bmw_m4_gt3"), _gt3_obs("b", car="bmw_m4_gt3")],
                car="bmw_m4_gt3",
                track="Red Bull Ring",
            )

        self.assertIn("Red Bull Ring", called)
        # Sanity: the registry helper does not return "red".
        self.assertNotEqual(original("Red Bull Ring"), "red")


# ─── F11: per-arch support tier thresholds ───────────────────────────────


class TestPerArchSupportTier(unittest.TestCase):
    def test_thresholds_table_has_gt3_partition(self):
        self.assertIn("gt3_coil_4wheel", _TIER_THRESHOLDS_BY_ARCH)
        self.assertIn("gtp_heave_third_torsion_front", _TIER_THRESHOLDS_BY_ARCH)

    def test_gt3_threshold_lower_than_gtp(self):
        gt3 = _TIER_THRESHOLDS_BY_ARCH["gt3_coil_4wheel"]
        gtp = _TIER_THRESHOLDS_BY_ARCH["gtp_heave_third_torsion_front"]
        self.assertLessEqual(gt3["calibrated"], gtp["calibrated"])

    def test_gt3_partial_at_10_observations(self):
        """GT3 reaches partial coverage at 10 observations
        (vs GTP's 15) — F11 in the audit."""
        self.assertEqual(
            compute_support_tier(10, suspension_arch="gt3_coil_4wheel"),
            "partial",
        )
        self.assertEqual(
            compute_support_tier(10, suspension_arch="gtp_heave_third_torsion_front"),
            "exploratory",
        )

    def test_legacy_call_without_arch_uses_gtp_thresholds(self):
        """Backward compat: callers that forget to pass `suspension_arch`
        get the GTP defaults (matches pre-W8.1 behavior)."""
        self.assertEqual(compute_support_tier(15), "partial")
        self.assertEqual(compute_support_tier(5), "exploratory")
        self.assertEqual(compute_support_tier(0), "unsupported")


# ─── F9: migration script exists ─────────────────────────────────────────


class TestMigrationScript(unittest.TestCase):
    """The Phase 2 raw-SQL migration must exist and contain the
    ALTER TABLE / backfill / index statements the audit calls out."""

    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        cls.path = repo_root / "migrations" / "0001_gt3_phase2.sql"

    def test_file_exists(self):
        self.assertTrue(
            self.path.exists(),
            f"Migration script not found at {self.path}",
        )

    def test_alters_car_definitions(self):
        body = self.path.read_text()
        self.assertIn("ALTER TABLE car_definitions", body)
        self.assertIn("iracing_car_path", body)
        self.assertIn("bop_version", body)
        self.assertIn("suspension_arch", body)

    def test_alters_observations(self):
        body = self.path.read_text()
        self.assertIn("ALTER TABLE observations", body)
        # NOT NULL with default backfills existing rows.
        self.assertIn("NOT NULL DEFAULT 'gtp_heave_third_torsion_front'", body)

    def test_creates_arch_index(self):
        body = self.path.read_text()
        self.assertIn("ix_observations_team_arch_track", body)
        self.assertIn("ix_car_definitions_iracing_path", body)
        self.assertIn("ix_car_definitions_arch", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
