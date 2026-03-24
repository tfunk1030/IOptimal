import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from analyzer.stint_analysis import (
    LapQuality,
    StintDataset,
    StintLapState,
    build_stint_dataset,
    merge_stint_datasets,
)
from solver.solve_chain import SolveChainInputs, SolveChainResult
from solver.stint_reasoner import aggregate_stint_recommendations, solve_stint_compromise


def _measured(
    lap_number: int,
    lap_time_s: float,
    fuel_level_l: float,
    **overrides,
):
    values = {
        "lap_number": lap_number,
        "lap_time_s": lap_time_s,
        "fuel_level_at_measurement_l": fuel_level_l,
        "front_rh_std_mm": 4.0,
        "rear_rh_std_mm": 5.0,
        "front_pressure_mean_kpa": 165.0,
        "rear_pressure_mean_kpa": 166.0,
        "body_slip_p95_deg": 2.8,
        "understeer_mean_deg": 0.8,
        "pitch_range_braking_deg": 1.0,
        "front_heave_travel_used_pct": 74.0,
        "rear_heave_travel_used_pct": 72.0,
        "bottoming_event_count_front_clean": 0,
        "bottoming_event_count_rear_clean": 0,
        "front_braking_lock_ratio_p95": 0.05,
        "rear_power_slip_ratio_p95": 0.05,
        "rear_slip_ratio_p95": 0.05,
        "understeer_low_speed_deg": 0.8,
        "understeer_high_speed_deg": 1.0,
        "abs_active_pct": 5.0,
        "front_carcass_mean_c": 92.0,
        "rear_carcass_mean_c": 93.0,
        "mean_front_rh_at_speed_mm": 22.0,
        "mean_rear_rh_at_speed_mm": 44.0,
        "peak_lat_g_measured": 2.0,
        "lf_pressure_kpa": 165.0,
        "rf_pressure_kpa": 165.0,
        "lr_pressure_kpa": 166.0,
        "rr_pressure_kpa": 166.0,
        "lf_wear_pct": 1.0,
        "rf_wear_pct": 1.0,
        "lr_wear_pct": 1.0,
        "rr_wear_pct": 1.0,
        "track_temp_c": 35.0,
        "air_temp_c": 25.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeIBT:
    def __init__(self, laps, *, pit_laps=None):
        self._laps = list(laps)
        self._pit_laps = set(pit_laps or [])
        total_samples = max(end for _, _, _, end in self._laps) + 1 if self._laps else 0
        self._channels = {
            "OnPitRoad": np.zeros(total_samples, dtype=float),
            "FuelLevel": np.zeros(total_samples, dtype=float),
        }
        fuel_level = 90.0
        for lap_num, _lap_time, start_idx, end_idx in self._laps:
            samples = end_idx - start_idx + 1
            self._channels["OnPitRoad"][start_idx:end_idx + 1] = 1.0 if lap_num in self._pit_laps else 0.0
            if lap_num in self._pit_laps:
                values = np.linspace(fuel_level, fuel_level + 6.0, samples)
                fuel_level += 6.0
            else:
                values = np.linspace(fuel_level, fuel_level - 2.0, samples)
                fuel_level -= 2.0
            self._channels["FuelLevel"][start_idx:end_idx + 1] = values

    def lap_times(self, min_time=0.0):
        return [lap for lap in self._laps if lap[1] >= min_time]

    def has_channel(self, name: str) -> bool:
        return name in self._channels

    def channel(self, name: str):
        return self._channels[name]


def _corner(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10):
    return SimpleNamespace(
        ls_comp=ls_comp,
        ls_rbd=ls_rbd,
        hs_comp=hs_comp,
        hs_rbd=hs_rbd,
        hs_slope=hs_slope,
    )


def _result(front_heave_nmm: float, *, perch_front: float = -11.0) -> SolveChainResult:
    step1 = SimpleNamespace(
        front_pushrod_offset_mm=-26.0,
        rear_pushrod_offset_mm=-24.0,
        static_front_rh_mm=18.0,
        static_rear_rh_mm=42.0,
    )
    step2 = SimpleNamespace(
        front_heave_nmm=front_heave_nmm,
        rear_third_nmm=520.0,
        perch_offset_front_mm=perch_front,
        perch_offset_rear_mm=42.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=13.9,
        rear_spring_rate_nmm=160.0,
        rear_spring_perch_mm=30.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="Soft",
        front_arb_blade_start=1,
        rear_arb_size="Medium",
        rear_arb_blade_start=3,
        rarb_blade_slow_corner=3,
        rarb_blade_fast_corner=3,
        farb_blade_locked=1,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-2.9,
        rear_camber_deg=-1.9,
        front_toe_mm=-0.4,
        rear_toe_mm=0.0,
    )
    step6 = SimpleNamespace(
        lf=_corner(),
        rf=_corner(),
        lr=_corner(),
        rr=_corner(),
    )
    supporting = SimpleNamespace(
        brake_bias_pct=46.0,
        brake_bias_target=0.0,
        brake_bias_migration=0.0,
        front_master_cyl_mm=19.1,
        rear_master_cyl_mm=20.6,
        pad_compound="Medium",
        diff_preload_nm=20.0,
        tc_gain=4,
        tc_slip=3,
        diff_clutch_plates=4,
        diff_ramp_option_idx=1,
        diff_ramp_angles="45/70",
        fuel_l=80.0,
        fuel_low_warning_l=5.0,
        fuel_target_l=0.0,
        gear_stack="short",
        roof_light_color="white",
    )
    legal = SimpleNamespace(valid=True, messages=[], to_dict=lambda: {"valid": True, "messages": []})
    confidence = SimpleNamespace(overall=0.8, to_dict=lambda: {"overall": 0.8, "per_metric": {}})
    return SolveChainResult(
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        legal_validation=legal,
        decision_trace=[],
        prediction=None,
        prediction_confidence=confidence,
        notes=[],
        candidate_vetoes=[],
        optimizer_used=False,
    )


def _apply_overrides(result: SolveChainResult, overrides) -> SolveChainResult:
    updated = copy.deepcopy(result)
    for step_name in ("step1", "step2", "step3", "step4", "step5", "supporting"):
        target = getattr(updated, step_name)
        for field_name, value in getattr(overrides, step_name).items():
            setattr(target, field_name, value)
    for corner_name, mapping in overrides.step6.items():
        corner = getattr(updated.step6, corner_name)
        for field_name, value in mapping.items():
            setattr(corner, field_name, value)
    return updated


class StintDatasetTests(unittest.TestCase):
    def test_build_stint_dataset_splits_stints_and_selects_last_green_run(self) -> None:
        laps = [(lap, 100.0 + 0.1 * lap, (lap - 1) * 4, (lap - 1) * 4 + 3) for lap in range(1, 10)]
        ibt = FakeIBT(laps, pit_laps={4})
        measured_by_lap = {
            lap: _measured(lap, lap_time, fuel_level_l=90.0 - lap * 2.0)
            for lap, lap_time, _start, _end in laps
        }

        with patch("analyzer.extract.extract_measurements", side_effect=lambda *_args, lap, **_kwargs: measured_by_lap[lap]):
            dataset = build_stint_dataset(
                ibt_path="fake.ibt",
                car=SimpleNamespace(),
                stint_select="last",
                stint_max_laps=40,
                threshold_pct=1.5,
                min_lap_time=90.0,
                ibt=ibt,
                source_label="S1",
            )

        self.assertEqual([(segment.start_lap, segment.end_lap) for segment in dataset.segments], [(1, 3), (5, 9)])
        self.assertEqual([(segment.start_lap, segment.end_lap) for segment in dataset.selected_segments], [(5, 9)])
        self.assertEqual([lap.lap_number for lap in dataset.usable_laps], [5, 6, 7, 8, 9])
        self.assertIsNone(dataset.fallback_mode)
        self.assertIn("Selected stint segment(s): 5-9 via 'last' selection.", dataset.selection_notes)

    def test_build_stint_dataset_preserves_phase_coverage_when_capping_evaluation_laps(self) -> None:
        laps = [(lap, 101.0 + 0.05 * lap, (lap - 1) * 3, (lap - 1) * 3 + 2) for lap in range(1, 51)]
        ibt = FakeIBT(laps)
        measured_by_lap = {
            lap: _measured(lap, lap_time, fuel_level_l=100.0 - lap)
            for lap, lap_time, _start, _end in laps
        }

        with patch("analyzer.extract.extract_measurements", side_effect=lambda *_args, lap, **_kwargs: measured_by_lap[lap]):
            dataset = build_stint_dataset(
                ibt_path="fake.ibt",
                car=SimpleNamespace(),
                stint_select="longest",
                stint_max_laps=10,
                threshold_pct=1.5,
                min_lap_time=90.0,
                ibt=ibt,
            )

        eval_laps = [lap.lap_number for lap in dataset.evaluation_laps]
        self.assertEqual(len(eval_laps), 10)
        self.assertEqual(eval_laps[:3], [1, 2, 3])
        self.assertEqual(eval_laps[-3:], [48, 49, 50])
        self.assertTrue(any(4 <= lap <= 47 for lap in eval_laps))
        self.assertEqual(sum(1 for lap in dataset.usable_laps if lap.selected_for_evaluation), 10)
        self.assertIn("representative laps", " ".join(dataset.selection_notes))

    def test_build_stint_dataset_falls_back_when_too_few_usable_laps_remain(self) -> None:
        laps = [(lap, 100.0 + lap, (lap - 1) * 2, (lap - 1) * 2 + 1) for lap in range(1, 7)]
        ibt = FakeIBT(laps)
        measured_by_lap = {
            1: _measured(1, 101.0, 88.0),
            2: _measured(2, 102.0, 86.0, front_rh_std_mm=0.0, rear_rh_std_mm=0.0, front_pressure_mean_kpa=0.0, rear_pressure_mean_kpa=0.0, body_slip_p95_deg=0.0, understeer_mean_deg=0.0, pitch_range_braking_deg=0.0),
            3: _measured(3, 103.0, 84.0),
            4: _measured(4, 104.0, 82.0, front_rh_std_mm=0.0, rear_rh_std_mm=0.0, front_pressure_mean_kpa=0.0, rear_pressure_mean_kpa=0.0, body_slip_p95_deg=0.0, understeer_mean_deg=0.0, pitch_range_braking_deg=0.0),
            5: _measured(5, 105.0, 80.0),
            6: _measured(6, 106.0, 78.0, front_rh_std_mm=0.0, rear_rh_std_mm=0.0, front_pressure_mean_kpa=0.0, rear_pressure_mean_kpa=0.0, body_slip_p95_deg=0.0, understeer_mean_deg=0.0, pitch_range_braking_deg=0.0),
        }

        with patch("analyzer.extract.extract_measurements", side_effect=lambda *_args, lap, **_kwargs: measured_by_lap[lap]):
            dataset = build_stint_dataset(
                ibt_path="fake.ibt",
                car=SimpleNamespace(),
                stint_select="all",
                stint_max_laps=40,
                threshold_pct=1.5,
                min_lap_time=90.0,
                ibt=ibt,
            )

        self.assertEqual([lap.lap_number for lap in dataset.usable_laps], [1, 3, 5])
        self.assertEqual(dataset.fallback_mode, "single_lap_insufficient_stint_data")
        self.assertIn("Only 3 usable stint laps remained after gating", " ".join(dataset.selection_notes))


class StintReasonerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_result = _result(50.0)
        self.base_inputs = SolveChainInputs(
            car=SimpleNamespace(),
            surface=SimpleNamespace(),
            track=SimpleNamespace(),
            measured=_measured(0, 100.0, 90.0),
            driver=SimpleNamespace(style="smooth"),
            diagnosis=SimpleNamespace(),
            current_setup=SimpleNamespace(),
            target_balance=50.14,
            fuel_load_l=90.0,
            wing_angle=17.0,
        )

    def test_solve_stint_compromise_uses_all_retained_laps_and_prefers_late_safe_support(self) -> None:
        retained_laps = []
        for lap_number in range(1, 6):
            late = lap_number >= 4
            measured = _measured(
                lap_number,
                100.0 + lap_number * 0.1,
                90.0 - lap_number * 2.0,
                front_heave_travel_used_pct=96.0 if late else 70.0,
                pitch_range_braking_deg=2.0 if late else 0.9,
                bottoming_event_count_front_clean=4 if late else 0,
            )
            retained_laps.append(
                StintLapState(
                    lap_number=lap_number,
                    lap_time_s=100.0 + lap_number * 0.1,
                    start_idx=lap_number * 10,
                    end_idx=lap_number * 10 + 5,
                    measured=measured,
                    snapshot=SimpleNamespace(),
                    quality=LapQuality(),
                    progress=(lap_number - 1) / 4,
                    phase="late" if late else "early",
                    fuel_level_l=90.0 - lap_number * 2.0,
                    source_label="S1",
                    source_path="fake.ibt",
                    selected_for_evaluation=True,
                )
            )
        dataset = StintDataset(
            ibt_path="fake.ibt",
            usable_laps=list(retained_laps),
            evaluation_laps=list(retained_laps),
            confidence=0.9,
            phase_summaries={"late": {"issues": ["increase_front_support"]}},
        )

        solve_calls = []

        def fake_run_base_solve(inputs):
            solve_calls.append(getattr(inputs.measured, "lap_number", 0))
            front_heave = 40.0 if getattr(inputs.measured, "lap_number", 0) <= 3 else 100.0
            return _result(front_heave)

        with (
            patch("solver.stint_reasoner.run_base_solve", side_effect=fake_run_base_solve),
            patch("solver.stint_reasoner.materialize_overrides", side_effect=lambda base_result, overrides, _inputs: _apply_overrides(base_result, overrides)),
        ):
            solve = solve_stint_compromise(
                dataset=dataset,
                base_inputs=self.base_inputs,
                base_result=self.base_result,
            )

        self.assertEqual(solve_calls, [1, 2, 3, 4, 5])
        self.assertGreaterEqual(solve.result.step2.front_heave_nmm, 90.0)
        self.assertLess(solve.objective["total"], 1.0)
        self.assertIsNone(solve.fallback_mode)
        self.assertIn("across 5 retained laps", " ".join(solve.notes))

    def test_solve_stint_compromise_returns_single_lap_fallback_for_short_dataset(self) -> None:
        short_dataset = StintDataset(
            ibt_path="fake.ibt",
            usable_laps=[
                StintLapState(
                    lap_number=lap_number,
                    lap_time_s=100.0,
                    start_idx=lap_number,
                    end_idx=lap_number,
                    measured=_measured(lap_number, 100.0, 90.0),
                    snapshot=SimpleNamespace(),
                    quality=LapQuality(),
                    progress=0.0,
                    phase="early",
                    fuel_level_l=90.0,
                    selected_for_evaluation=True,
                )
                for lap_number in range(1, 5)
            ],
            evaluation_laps=[],
            confidence=0.8,
        )

        solve = solve_stint_compromise(
            dataset=short_dataset,
            base_inputs=self.base_inputs,
            base_result=self.base_result,
        )

        self.assertIs(solve.result, self.base_result)
        self.assertEqual(solve.fallback_mode, "single_lap_insufficient_stint_data")
        self.assertIn("insufficient usable stint laps", " ".join(solve.notes))

    def test_aggregate_stint_recommendations_requires_sixty_percent_consensus(self) -> None:
        datasets = [
            StintDataset(ibt_path="s1", usable_laps=[object()], phase_summaries={"late": {"issues": ["improve_traction"]}}),
            StintDataset(ibt_path="s2", usable_laps=[object()], phase_summaries={"late": {"issues": ["improve_traction"]}}),
            StintDataset(ibt_path="s3", usable_laps=[object()], phase_summaries={"late": {"issues": ["reduce_entry_understeer"]}}),
        ]

        recommendations = aggregate_stint_recommendations(datasets)

        self.assertEqual(recommendations, [
            {
                "phase": "late",
                "issue": "improve_traction",
                "count": 2,
                "stint_count": 3,
                "ratio": 0.667,
            }
        ])

    def test_merge_stint_datasets_keeps_cross_session_selection_metadata(self) -> None:
        first = StintDataset(
            ibt_path="s1",
            source_label="S1",
            usable_laps=[
                StintLapState(
                    lap_number=1,
                    lap_time_s=100.0,
                    start_idx=0,
                    end_idx=1,
                    measured=_measured(1, 100.0, 90.0),
                    snapshot=SimpleNamespace(),
                    quality=LapQuality(),
                    progress=0.0,
                    phase="early",
                    fuel_level_l=90.0,
                    source_label="S1",
                    selected_for_evaluation=True,
                )
            ],
            confidence=0.8,
            phase_summaries={"early": {"issues": ["increase_front_support"]}},
            selection_notes=["S1 kept"],
        )
        second = StintDataset(
            ibt_path="s2",
            source_label="S2",
            usable_laps=[
                StintLapState(
                    lap_number=2,
                    lap_time_s=101.0,
                    start_idx=2,
                    end_idx=3,
                    measured=_measured(2, 101.0, 88.0),
                    snapshot=SimpleNamespace(),
                    quality=LapQuality(),
                    progress=1.0,
                    phase="late",
                    fuel_level_l=88.0,
                    source_label="S2",
                    selected_for_evaluation=True,
                )
            ],
            confidence=0.6,
            phase_summaries={"late": {"issues": ["improve_traction"]}},
            selection_notes=["S2 kept"],
        )

        merged = merge_stint_datasets([first, second], stint_max_laps=40)

        self.assertEqual([lap.source_label for lap in merged.usable_laps], ["S1", "S2"])
        self.assertEqual(merged.selected_lap_count, 2)
        self.assertEqual(merged.selection_notes, ["S1 kept", "S2 kept"])
        self.assertAlmostEqual(merged.confidence, 0.7, places=3)


if __name__ == "__main__":
    unittest.main()
