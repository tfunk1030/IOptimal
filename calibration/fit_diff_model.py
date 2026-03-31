"""Fit simple diff/TC calibration artifacts from telemetry samples."""

from __future__ import annotations

from statistics import mean
from typing import Iterable

from calibration.models import FittedModelArtifact, NormalizedTelemetrySample


def fit_diff_model(*, car: str, track: str, samples: Iterable[NormalizedTelemetrySample]) -> FittedModelArtifact:
    rows = list(samples)
    if not rows:
        raise ValueError("No telemetry samples supplied.")

    preload_vals = []
    tc_gain_vals = []
    tc_slip_vals = []
    rear_slip = []
    understeer_low = []
    for row in rows:
        inputs = row.canonical_inputs
        measured = row.measured
        if "diff_preload_nm" in inputs:
            preload_vals.append(float(inputs["diff_preload_nm"]))
        if "tc_gain" in inputs:
            tc_gain_vals.append(float(inputs["tc_gain"]))
        if "tc_slip" in inputs:
            tc_slip_vals.append(float(inputs["tc_slip"]))
        if measured.get("rear_power_slip_ratio_p95") is not None:
            rear_slip.append(float(measured["rear_power_slip_ratio_p95"]))
        if measured.get("understeer_low_speed_deg") is not None:
            understeer_low.append(float(measured["understeer_low_speed_deg"]))

    return FittedModelArtifact(
        car=car,
        track=track,
        model_type="diff_model",
        parameters={
            "mean_diff_preload_nm": round(mean(preload_vals), 4) if preload_vals else None,
            "mean_tc_gain": round(mean(tc_gain_vals), 4) if tc_gain_vals else None,
            "mean_tc_slip": round(mean(tc_slip_vals), 4) if tc_slip_vals else None,
        },
        metrics={
            "samples": len(rows),
            "mean_rear_power_slip_ratio_p95": round(mean(rear_slip), 6) if rear_slip else None,
            "mean_understeer_low_speed_deg": round(mean(understeer_low), 6) if understeer_low else None,
        },
    )
