"""Tests for track_model.track_store — cumulative track profile accumulation."""

from __future__ import annotations

import json
import copy
from pathlib import Path

import numpy as np
import pytest

from track_model.profile import TrackProfile, BrakingZone, Corner, KerbEvent
from track_model.track_store import (
    TrackProfileStore,
    SessionSnapshot,
    _filter_mad,
    _merge_spatial_events,
    _passes_quality_gate,
    _aggregate_scalar,
)


# ── Fixtures ──

def _make_profile(**overrides) -> TrackProfile:
    """Create a minimal valid TrackProfile for testing."""
    defaults = dict(
        track_name="Sebring International Raceway",
        track_config="International",
        track_length_m=5954.0,
        car="porsche_963",
        best_lap_time_s=120.0,
        median_speed_kph=170.0,
        max_speed_kph=290.0,
        min_speed_kph=60.0,
        pct_above_200kph=0.25,
        pct_below_120kph=0.15,
        peak_lat_g=2.5,
        peak_braking_g=3.0,
        peak_accel_g=1.2,
        peak_vertical_g=4.0,
        shock_vel_p50_front_mps=0.05,
        shock_vel_p95_front_mps=0.15,
        shock_vel_p99_front_mps=0.30,
        shock_vel_p50_rear_mps=0.04,
        shock_vel_p95_rear_mps=0.12,
        shock_vel_p99_rear_mps=0.25,
        shock_vel_p50_front_clean_mps=0.04,
        shock_vel_p95_front_clean_mps=0.13,
        shock_vel_p99_front_clean_mps=0.28,
        shock_vel_p50_rear_clean_mps=0.035,
        shock_vel_p95_rear_clean_mps=0.11,
        shock_vel_p99_rear_clean_mps=0.23,
        shock_vel_p99_front_hs_mps=0.20,
        shock_vel_p99_rear_hs_mps=0.18,
        shock_vel_p95_front_kerb_mps=0.25,
        shock_vel_p99_front_kerb_mps=0.40,
        shock_vel_p95_rear_kerb_mps=0.22,
        shock_vel_p99_rear_kerb_mps=0.35,
        kerb_sample_pct=5.0,
        elevation_change_m=12.0,
        roll_gradient_deg_per_g=0.8,
        lltd_measured=0.52,
        splitter_rh_mean_mm=25.0,
        splitter_rh_min_mm=15.0,
        air_temp_c=25.0,
        track_temp_c=35.0,
        air_density_kg_m3=1.18,
        telemetry_source="test_session.ibt",
        speed_bands_kph={"60-80": 5.0, "80-100": 10.0, "100-120": 15.0,
                         "120-140": 15.0, "140-160": 10.0, "160-180": 10.0,
                         "180-200": 10.0, "200-220": 10.0, "220-240": 10.0,
                         "240-260": 5.0},
        lateral_g={"mean_abs": 0.94, "p90": 1.83, "p95": 2.02, "max": 2.5},
        body_roll_deg={"mean_abs": 0.72, "p95": 1.67, "max": 2.0},
        braking_zones=[
            BrakingZone(lap_dist_m=500.0, entry_speed_kph=250.0,
                        min_speed_kph=80.0, peak_decel_g=2.8, braking_dist_m=120.0),
            BrakingZone(lap_dist_m=2000.0, entry_speed_kph=220.0,
                        min_speed_kph=100.0, peak_decel_g=2.5, braking_dist_m=100.0),
        ],
        corners=[
            Corner(lap_dist_m=620.0, speed_kph=80.0, peak_lat_g=2.0,
                   radius_m=50.0, direction="right"),
        ],
        kerb_events=[
            KerbEvent(lap_dist_m=700.0, severity=3.5, side="left"),
        ],
    )
    defaults.update(overrides)
    return TrackProfile(**defaults)


# ── Quality Gate Tests ──

class TestQualityGate:
    def test_accepts_normal_session(self):
        p = _make_profile()
        ok, reason = _passes_quality_gate(p, 120.0, 5)
        assert ok
        assert reason == "accepted"

    def test_rejects_crash(self):
        p = _make_profile(peak_vertical_g=16.0)
        ok, reason = _passes_quality_gate(p, 120.0, 0)
        assert not ok
        assert "crash" in reason

    def test_rejects_pit_lane(self):
        p = _make_profile(median_speed_kph=80.0)
        ok, reason = _passes_quality_gate(p, 120.0, 0)
        assert not ok
        assert "pit-lane" in reason

    def test_rejects_extreme_shock(self):
        p = _make_profile(shock_vel_p99_front_mps=2.5)
        ok, reason = _passes_quality_gate(p, 120.0, 0)
        assert not ok
        assert "shock" in reason

    def test_rejects_wet_at_3_sessions(self):
        p = _make_profile(air_density_kg_m3=1.30)
        ok, reason = _passes_quality_gate(p, 120.0, 3)
        assert not ok
        assert "wet" in reason

    def test_allows_wet_under_3_sessions(self):
        p = _make_profile(air_density_kg_m3=1.30)
        ok, _ = _passes_quality_gate(p, 120.0, 2)
        assert ok

    def test_rejects_slow_lap(self):
        p = _make_profile(best_lap_time_s=140.0)  # 120 * 1.15 = 138
        ok, reason = _passes_quality_gate(p, 120.0, 5)
        assert not ok
        assert "slow" in reason

    def test_allows_fast_lap(self):
        p = _make_profile(best_lap_time_s=130.0)  # within 15%
        ok, _ = _passes_quality_gate(p, 120.0, 5)
        assert ok


# ── MAD Outlier Filtering Tests ──

class TestMADFilter:
    def test_small_sample_passthrough(self):
        vals = [1.0, 2.0]
        assert _filter_mad(vals, 2) == vals

    def test_rejects_extreme_outlier(self):
        vals = [0.10, 0.12, 0.11, 0.13, 0.50]  # 0.50 is extreme
        filtered = _filter_mad(vals, 5)
        assert 0.50 not in filtered
        assert len(filtered) == 4

    def test_conservative_threshold_small_n(self):
        # With N<10, z threshold is 3.0 (more conservative)
        vals = [10.0, 10.1, 10.2, 15.0]  # 15.0 is ~3.3σ from median
        filtered_small = _filter_mad(vals, 4)
        # With larger N, z threshold is 2.5 (less conservative)
        filtered_large = _filter_mad(vals, 15)
        assert len(filtered_small) >= len(filtered_large)

    def test_identical_values_passthrough(self):
        vals = [5.0, 5.0, 5.0, 5.0]
        assert _filter_mad(vals, 4) == vals

    def test_never_returns_empty(self):
        vals = [1.0, 100.0, 200.0]
        result = _filter_mad(vals, 3)
        assert len(result) > 0


# ── Spatial Event Matching Tests ──

class TestSpatialMerging:
    def test_no_false_merge_close_zones(self):
        """Two braking zones 15m apart must NOT merge at 25m tolerance."""
        # These are distinct features that happen to be close
        session1 = [
            {"lap_dist_m": 4067.6, "entry_speed_kph": 200.0, "peak_decel_g": 2.5},
            {"lap_dist_m": 4077.6, "entry_speed_kph": 150.0, "peak_decel_g": 2.0},
        ]
        # Second session: same two zones
        session2 = [
            {"lap_dist_m": 4068.0, "entry_speed_kph": 198.0, "peak_decel_g": 2.4},
            {"lap_dist_m": 4078.0, "entry_speed_kph": 148.0, "peak_decel_g": 1.9},
        ]
        merged = _merge_spatial_events(
            [session1, session2],
            match_tolerance_m=25.0,
            numeric_fields=["entry_speed_kph", "peak_decel_g"],
        )
        # Should keep 2 distinct zones, not merge into 1
        assert len(merged) == 2

    def test_matching_drift(self):
        """Same zone at +5m offset across sessions should merge."""
        s1 = [{"lap_dist_m": 500.0, "entry_speed_kph": 250.0, "peak_decel_g": 2.8}]
        s2 = [{"lap_dist_m": 505.0, "entry_speed_kph": 248.0, "peak_decel_g": 2.7}]
        s3 = [{"lap_dist_m": 502.0, "entry_speed_kph": 252.0, "peak_decel_g": 2.9}]
        merged = _merge_spatial_events(
            [s1, s2, s3],
            match_tolerance_m=25.0,
            numeric_fields=["entry_speed_kph", "peak_decel_g"],
        )
        assert len(merged) == 1
        assert 248.0 <= merged[0]["entry_speed_kph"] <= 252.0

    def test_drops_rare_events(self):
        """Events appearing in <30% of sessions get dropped."""
        common = {"lap_dist_m": 500.0, "entry_speed_kph": 250.0}
        rare = {"lap_dist_m": 3000.0, "entry_speed_kph": 200.0}
        # 4 sessions, rare event only in 1 (25% < 30%)
        sessions = [
            [common, rare],
            [common],
            [common],
            [common],
        ]
        merged = _merge_spatial_events(
            sessions,
            match_tolerance_m=25.0,
            numeric_fields=["entry_speed_kph"],
        )
        dists = [e["lap_dist_m"] for e in merged]
        assert 3000.0 not in dists
        assert any(abs(d - 500.0) < 10 for d in dists)

    def test_single_session_passthrough(self):
        events = [{"lap_dist_m": 100.0, "speed_kph": 80.0}]
        merged = _merge_spatial_events([events], match_tolerance_m=15.0, numeric_fields=["speed_kph"])
        assert len(merged) == 1


# ── TrackProfileStore Integration Tests ──

class TestTrackProfileStore:
    def test_single_session_consensus_equals_input(self, tmp_path):
        p = _make_profile()
        store = TrackProfileStore("test_track", "porsche_963", base_dir=tmp_path)
        ok, _ = store.add_session(p, session_id="session1")
        assert ok
        assert store.n_sessions == 1

        c = store.consensus()
        assert c.consensus_n_sessions == 1
        assert c.track_name == p.track_name
        assert abs(c.best_lap_time_s - p.best_lap_time_s) < 0.001
        assert abs(c.shock_vel_p99_front_mps - p.shock_vel_p99_front_mps) < 0.001
        assert abs(c.peak_lat_g - p.peak_lat_g) < 0.001

    def test_multiple_sessions_p75_for_p99(self, tmp_path):
        """Verify shock_vel_p99 fields use P75 aggregation."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        values = [0.25, 0.30, 0.35]
        for i, v in enumerate(values):
            p = _make_profile(shock_vel_p99_front_mps=v)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        expected_p75 = float(np.percentile(values, 75))
        assert abs(c.shock_vel_p99_front_mps - expected_p75) < 0.001

    def test_multiple_sessions_median_for_p95(self, tmp_path):
        """Verify shock_vel_p95 fields use median aggregation."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        values = [0.10, 0.15, 0.12]
        for i, v in enumerate(values):
            p = _make_profile(shock_vel_p95_front_mps=v)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        expected_median = float(np.median(values))
        assert abs(c.shock_vel_p95_front_mps - expected_median) < 0.001

    def test_envelope_fields_use_max(self, tmp_path):
        """Verify peak_lat_g uses max across sessions."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        for i, g in enumerate([2.0, 2.5, 2.3]):
            p = _make_profile(peak_lat_g=g)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        assert abs(c.peak_lat_g - 2.5) < 0.001

    def test_best_lap_uses_min(self, tmp_path):
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        for i, t in enumerate([122.0, 118.0, 120.0]):
            p = _make_profile(best_lap_time_s=t)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        assert abs(c.best_lap_time_s - 118.0) < 0.001

    def test_deduplication(self, tmp_path):
        p = _make_profile()
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        ok1, _ = store.add_session(p, session_id="same_id")
        ok2, reason = store.add_session(p, session_id="same_id")
        assert ok1
        assert not ok2
        assert "duplicate" in reason
        assert store.n_sessions == 1

    def test_persistence_roundtrip(self, tmp_path):
        store1 = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        p1 = _make_profile(best_lap_time_s=119.0)
        p2 = _make_profile(best_lap_time_s=121.0, shock_vel_p99_front_mps=0.35)
        store1.add_session(p1, session_id="s1")
        store1.add_session(p2, session_id="s2")

        # Reload from disk
        store2 = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        assert store2.n_sessions == 2

        c1 = store1.consensus()
        c2 = store2.consensus()
        assert abs(c1.best_lap_time_s - c2.best_lap_time_s) < 0.001
        assert abs(c1.shock_vel_p99_front_mps - c2.shock_vel_p99_front_mps) < 0.001

    def test_quality_gate_rejects_crash(self, tmp_path):
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        p = _make_profile(peak_vertical_g=20.0)
        ok, reason = store.add_session(p, session_id="crash")
        assert not ok
        assert "crash" in reason
        assert store.n_sessions == 0

    def test_quality_gate_rejects_slow(self, tmp_path):
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        # Add 3 sessions first to activate relative gates
        for i in range(3):
            p = _make_profile(best_lap_time_s=120.0 + i)
            store.add_session(p, session_id=f"s{i}")

        slow = _make_profile(best_lap_time_s=150.0)  # > 120 * 1.15 = 138
        ok, reason = store.add_session(slow, session_id="slow")
        assert not ok
        assert "slow" in reason

    def test_consensus_is_valid_track_profile(self, tmp_path):
        """Ensure consensus output has all solver-required fields."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        for i in range(3):
            p = _make_profile(best_lap_time_s=118.0 + i)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        # Type checks
        assert isinstance(c, TrackProfile)
        assert isinstance(c.track_name, str)
        assert isinstance(c.best_lap_time_s, float)
        assert isinstance(c.shock_vel_p99_front_mps, float)
        assert isinstance(c.braking_zones, list)
        assert isinstance(c.corners, list)
        assert isinstance(c.kerb_events, list)
        assert c.consensus_n_sessions == 3
        assert "consensus" in c.telemetry_source.lower()
        # Solver-required properties work
        assert c.aero_reference_speed_kph > 0

    def test_legacy_migration(self, tmp_path):
        """Existing JSON bootstraps into store."""
        p = _make_profile(car="porsche_963")
        legacy_path = tmp_path / "test_track.json"
        p.save(legacy_path)

        store = TrackProfileStore("test_track", "porsche_963", base_dir=tmp_path)
        assert store.n_sessions == 1
        c = store.consensus()
        assert abs(c.best_lap_time_s - p.best_lap_time_s) < 0.001

    def test_legacy_migration_skips_wrong_car(self, tmp_path):
        """Legacy migration only imports if car matches."""
        p = _make_profile(car="bmw_m_hybrid_v8")
        legacy_path = tmp_path / "test_track.json"
        p.save(legacy_path)

        store = TrackProfileStore("test_track", "porsche_963", base_dir=tmp_path)
        assert store.n_sessions == 0

    def test_mad_outlier_filtering_in_consensus(self, tmp_path):
        """Verify MAD filtering rejects extreme values in consensus."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        # 4 normal + 1 extreme
        for i in range(4):
            p = _make_profile(median_speed_kph=170.0 + i)
            store.add_session(p, session_id=f"s{i}")
        extreme = _make_profile(median_speed_kph=500.0)  # way out
        store.add_session(extreme, session_id="extreme")

        c = store.consensus()
        # median_speed should be near 171, not pulled up by 500
        assert c.median_speed_kph < 175.0

    def test_braking_zone_aggregation(self, tmp_path):
        """Verify braking zones are merged and aggregated."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        for i in range(3):
            zones = [
                BrakingZone(
                    lap_dist_m=500.0 + i * 2,  # slight drift
                    entry_speed_kph=250.0 + i,
                    min_speed_kph=80.0,
                    peak_decel_g=2.8 + i * 0.1,
                    braking_dist_m=120.0,
                ),
            ]
            p = _make_profile(braking_zones=zones)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        # Should have merged the default 2 zones + the overridden one
        # The override replaces default zones, so we get just 1 zone per session
        assert len(c.braking_zones) >= 1
        # Entry speed should be aggregated (median of 250, 251, 252)
        bz = c.braking_zones[0]
        assert isinstance(bz, BrakingZone)

    def test_distribution_aggregation(self, tmp_path):
        """Verify distribution fields are merged."""
        store = TrackProfileStore("test", "porsche_963", base_dir=tmp_path)
        for i in range(3):
            lat_g = {"mean_abs": 0.9 + i * 0.05, "p95": 2.0 + i * 0.1, "max": 2.5 + i * 0.1}
            p = _make_profile(lateral_g=lat_g)
            store.add_session(p, session_id=f"s{i}")

        c = store.consensus()
        assert isinstance(c.lateral_g, dict)
        assert "mean_abs" in c.lateral_g
        # Should be median of [0.9, 0.95, 1.0]
        assert abs(c.lateral_g["mean_abs"] - 0.95) < 0.01

    def test_store_path_format(self, tmp_path):
        store = TrackProfileStore("sebring_international", "porsche_963", base_dir=tmp_path)
        expected = tmp_path / "sebring_international_porsche_963_store.json"
        assert store._store_path() == expected
