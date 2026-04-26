"""Empirical models fitted from accumulated observations.

These models don't REPLACE the physics solver — they INFORM it. Each model
captures a relationship that the physics engine approximates with theory,
and the empirical model provides a data-driven correction factor.

The key models:
1. Aero compression model: how much does the car actually compress vs V²?
2. Roll gradient model: actual deg/g vs what the spring model predicts
3. Heave effective mass: calibrated m_eff per track surface (varies!)
4. Roll-distribution proxy auditing: the IBT signal is not true LLTD
5. Damper click → settle time: how clicks map to damping response
6. Lap time sensitivity: which parameters have the biggest lap time effect

Each model stores its fit quality (R², sample count, confidence interval)
so the solver knows when to trust it and when to fall back to theory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np


# Time decay: weight *= 0.95 ^ days_since_observation
TIME_DECAY_BASE = 0.95

# Minimum sessions for non-prediction corrections
MIN_SESSIONS_FOR_CORRECTIONS = 5


@dataclass
class FittedRelationship:
    """A single empirical relationship derived from data."""
    name: str
    x_param: str        # input parameter name
    y_param: str        # output metric name
    fit_type: str       # "linear" | "quadratic" | "power_law" | "lookup"

    # Fit parameters (for y = a*x + b, or y = a*x^n + b)
    coefficients: list[float] = field(default_factory=list)

    # Fit quality
    r_squared: float = 0.0
    sample_count: int = 0
    residual_std: float = 0.0

    # Data points used in fit
    x_values: list[float] = field(default_factory=list)
    y_values: list[float] = field(default_factory=list)

    # Validity range
    x_min: float = 0.0
    x_max: float = 0.0

    # Comparison to physics model
    physics_prediction_at_mean: float = 0.0
    empirical_prediction_at_mean: float = 0.0
    correction_factor: float = 1.0  # empirical / physics

    def predict(self, x: float) -> float | None:
        """Predict y from x using the fitted model."""
        if self.sample_count < 3:
            return None  # not enough data
        if not self.coefficients:
            return None

        if self.fit_type == "linear":
            a, b = self.coefficients[0], self.coefficients[1] if len(self.coefficients) > 1 else 0
            return a * x + b
        elif self.fit_type == "quadratic":
            a, b, c = self.coefficients[:3]
            return a * x**2 + b * x + c
        elif self.fit_type == "power_law":
            a, n = self.coefficients[:2]
            return a * x**n
        return None

    def confidence_at(self, x: float) -> str:
        """Confidence level for a prediction at x."""
        if self.sample_count < 3:
            return "insufficient_data"
        if self.r_squared < 0.3:
            return "low"
        if x < self.x_min or x > self.x_max:
            return "extrapolation"
        if self.r_squared < 0.7:
            return "moderate"
        return "high"


@dataclass
class EmpiricalModelSet:
    """Collection of all empirical models for one car/track combination."""
    car: str
    track: str
    relationships: dict[str, FittedRelationship] = field(default_factory=dict)
    last_updated: str = ""
    observation_count: int = 0

    # Aggregated insights
    most_sensitive_parameters: list[tuple[str, float]] = field(default_factory=list)
    # (parameter, lap_time_sensitivity_s_per_unit)

    # Physics model corrections
    corrections: dict[str, float] = field(default_factory=dict)
    # e.g. {"roll_stiffness_factor": 0.48, "m_eff_front_correction": 1.37}

    def to_dict(self) -> dict:
        from dataclasses import asdict
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "EmpiricalModelSet":
        rels = {}
        for k, v in d.get("relationships", {}).items():
            rels[k] = FittedRelationship(**v)
        return EmpiricalModelSet(
            car=d["car"],
            track=d["track"],
            relationships=rels,
            last_updated=d.get("last_updated", ""),
            observation_count=d.get("observation_count", 0),
            most_sensitive_parameters=d.get("most_sensitive_parameters", []),
            corrections=d.get("corrections", {}),
        )


def _safe_linear_fit(x: list[float], y: list[float]) -> tuple[list[float], float]:
    """Fit y = a*x + b, return (coefficients, r²).

    Requires >= 4 points for a meaningful fit. With only 2 points a linear fit
    is always perfect (R²=1.0) and tells us nothing about the relationship.
    """
    if len(x) < 4:
        return [], 0.0
    x_arr = np.array(x, dtype=float)
    y_arr = np.array(y, dtype=float)
    # Guard against NaN/inf in input data
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    if np.sum(mask) < 4:
        return [], 0.0
    x_arr, y_arr = x_arr[mask], y_arr[mask]
    try:
        coeffs = np.polyfit(x_arr, y_arr, 1)
        y_pred = np.polyval(coeffs, x_arr)
        ss_res = np.sum((y_arr - y_pred) ** 2)
        ss_tot = np.sum((y_arr - np.mean(y_arr)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return list(coeffs), max(0.0, r2)
    except Exception:
        return [], 0.0


def fit_models(
    observations: list[dict],
    deltas: list[dict],
    car: str,
    track: str,
) -> EmpiricalModelSet:
    """Fit all empirical models from accumulated observations and deltas.

    This is called after ingesting a new session. It looks at ALL historical
    data for this car/track combination and updates the models.
    """
    models = EmpiricalModelSet(
        car=car,
        track=track,
        last_updated=datetime.now(timezone.utc).isoformat(),
        observation_count=len(observations),
    )

    if len(observations) < 2:
        return models

    # ── 1. Roll gradient: actual roll vs lateral G ──────────────────
    _fit_roll_gradient(observations, models)

    # ── 2. Roll-distribution proxy provenance ──────────────────────
    # The historical "lltd_measured" field is a ride-height proxy, not true
    # wheel-load LLTD.  Do not fit ARB calibration relationships from it.
    _record_roll_distribution_proxy(observations, models)

    # ── 3. Heave spring → platform variance ────────────────────────
    _fit_heave_to_variance(observations, models)

    # ── 4. Third spring → rear variance ────────────────────────────
    _fit_third_to_variance(observations, models)

    # ── 5. Aero compression model ──────────────────────────────────
    _fit_aero_compression(observations, models)

    # ── 6. Settle time vs damper clicks ────────────────────────────
    _fit_settle_time(observations, models)

    # ── 6b. Damper click → telemetry physics correlations ──────────
    _fit_damper_physics(observations, models)

    # ── 6c. Back-calculate force-per-click from shock vel correlations ──
    _calibrate_force_per_click(observations, models)

    # ── 7. Lap time sensitivity from deltas ────────────────────────
    # NOTE: lap time is a noisy signal. Physics-channel correlations above
    # are the primary calibration signal. Lap time sensitivity is supplementary.
    _fit_lap_time_sensitivity(deltas, models)

    # ── 8. Physics model corrections ───────────────────────────────
    _compute_corrections(observations, models)

    # ── 9. Roll gain calibration from tyre thermals ─────────────────
    thermal_cal = calibrate_roll_gain_from_thermals(observations)
    if thermal_cal["front_roll_gain"] is not None:
        models.corrections["calibrated_front_roll_gain"] = thermal_cal["front_roll_gain"]
        models.corrections["calibrated_rear_roll_gain"] = thermal_cal["rear_roll_gain"]
        models.corrections["roll_gain_calibration_confidence"] = thermal_cal["confidence"]
        models.corrections["roll_gain_calibration_samples"] = thermal_cal["sample_count"]

    # ── 10. Prediction-vs-measurement feedback loop ───────────────
    fit_prediction_errors(observations, models)

    return models


def _fit_roll_gradient(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit actual roll gradient (deg/g) from observations."""
    x, y = [], []
    for obs in obs_list:
        rg = obs.get("telemetry", {}).get("roll_gradient_deg_per_g", 0)
        lat = obs.get("telemetry", {}).get("peak_lat_g", 0)
        if rg > 0.1 and lat > 0.5:
            # x = nothing (single value per session), accumulate as distribution
            x.append(lat)
            y.append(rg)

    if len(x) >= 4:
        mean_rg = float(np.mean(y))
        std_rg = float(np.std(y))
        # For a constant model, R² measures consistency of the data (low std = high R²)
        # Use coefficient of variation to assess whether the mean is reliable
        cv = std_rg / mean_rg if mean_rg > 0 else 1.0
        r2_estimate = max(0.0, 1.0 - cv ** 2)  # low CV → high R²
        models.relationships["roll_gradient"] = FittedRelationship(
            name="Roll gradient (measured)",
            x_param="peak_lat_g",
            y_param="roll_gradient_deg_per_g",
            fit_type="linear",
            coefficients=[0.0, mean_rg],  # constant model (slope=0, intercept=mean)
            r_squared=r2_estimate,
            sample_count=len(x),
            residual_std=std_rg,
            x_values=x,
            y_values=y,
            x_min=min(x),
            x_max=max(x),
        )


def _record_roll_distribution_proxy(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Record that legacy LLTD telemetry is proxy-only, not a calibration target."""
    proxy_values = []
    for obs in obs_list:
        telemetry = obs.get("telemetry", {})
        proxy = telemetry.get("roll_distribution_proxy", telemetry.get("lltd_measured", 0))
        if proxy and proxy > 0:
            proxy_values.append(float(proxy))

    if proxy_values:
        pv = np.array(proxy_values, dtype=float)
        models.corrections["roll_distribution_proxy_mean"] = float(np.mean(pv))
        models.corrections["roll_distribution_proxy_std"] = float(np.std(pv))
        models.corrections["roll_distribution_proxy_sample_count"] = len(proxy_values)
        # Backward-compatible keys are retained only so downstream guards can
        # explicitly skip applying the proxy as an LLTD target.
        models.corrections["lltd_measured_mean"] = float(np.mean(pv))
        models.corrections["lltd_measured_std"] = float(np.std(pv))
        models.corrections["lltd_is_proxy"] = True


def _fit_heave_to_variance(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit front ride height variance as function of heave spring rate."""
    x, y = [], []
    for obs in obs_list:
        heave = obs.get("setup", {}).get("front_heave_nmm")
        var = obs.get("telemetry", {}).get("front_rh_std_mm", 0)
        if heave and heave > 0 and var > 0:
            x.append(float(heave))
            y.append(var)

    if len(x) >= 4:
        coeffs, r2 = _safe_linear_fit(x, y)
        if coeffs:
            models.relationships["front_rh_var_vs_heave"] = FittedRelationship(
                name="Front RH variance vs heave spring",
                x_param="front_heave_nmm",
                y_param="front_rh_std_mm",
                fit_type="linear",
                coefficients=coeffs,
                r_squared=r2,
                sample_count=len(x),
                x_values=x,
                y_values=y,
                x_min=min(x),
                x_max=max(x),
            )


def _fit_third_to_variance(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit rear ride height variance as function of third spring rate."""
    x, y = [], []
    for obs in obs_list:
        third = obs.get("setup", {}).get("rear_third_nmm")
        var = obs.get("telemetry", {}).get("rear_rh_std_mm", 0)
        if third and third > 0 and var > 0:
            x.append(float(third))
            y.append(var)

    if len(x) >= 4:
        coeffs, r2 = _safe_linear_fit(x, y)
        if coeffs:
            models.relationships["rear_rh_var_vs_third"] = FittedRelationship(
                name="Rear RH variance vs third spring",
                x_param="rear_third_nmm",
                y_param="rear_rh_std_mm",
                fit_type="linear",
                coefficients=coeffs,
                r_squared=r2,
                sample_count=len(x),
                x_values=x,
                y_values=y,
                x_min=min(x),
                x_max=max(x),
            )


def _fit_aero_compression(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Track the measured aero compression across sessions."""
    front_comp, rear_comp = [], []
    for obs in obs_list:
        fc = obs.get("telemetry", {}).get("dynamic_front_rh_mm", 0)
        rc = obs.get("telemetry", {}).get("dynamic_rear_rh_mm", 0)
        sf = obs.get("setup", {}).get("front_rh_static", 0)
        sr = obs.get("setup", {}).get("rear_rh_static", 0)
        if sf > 0 and fc > 0:
            front_comp.append(sf - fc)
        if sr > 0 and rc > 0:
            rear_comp.append(sr - rc)

    if front_comp:
        models.corrections["aero_compression_front_mean_mm"] = float(np.mean(front_comp))
        models.corrections["aero_compression_front_std_mm"] = float(np.std(front_comp))
    if rear_comp:
        models.corrections["aero_compression_rear_mean_mm"] = float(np.mean(rear_comp))
        models.corrections["aero_compression_rear_std_mm"] = float(np.std(rear_comp))


def _fit_settle_time(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit settle time vs damper LS rebound clicks."""
    # This requires damper data in the setup — may not always be present
    x, y = [], []
    for obs in obs_list:
        settle = obs.get("telemetry", {}).get("front_rh_settle_time_ms", 0)
        dampers = obs.get("setup", {}).get("dampers", {})
        lf = dampers.get("lf", {})
        ls_rbd = lf.get("ls_rbd")
        if settle > 0 and ls_rbd is not None:
            x.append(float(ls_rbd))
            y.append(settle)

    if len(x) >= 4:
        coeffs, r2 = _safe_linear_fit(x, y)
        if coeffs:
            models.relationships["settle_time_vs_ls_rbd"] = FittedRelationship(
                name="Settle time vs front LS rebound",
                x_param="front_ls_rbd",
                y_param="front_rh_settle_time_ms",
                fit_type="linear",
                coefficients=coeffs,
                r_squared=r2,
                sample_count=len(x),
                x_values=x,
                y_values=y,
                x_min=min(x),
                x_max=max(x),
            )


def _extract_damper_flat(obs: dict) -> dict[str, float]:
    """Flatten nested damper struct into front/rear averaged click values."""
    dampers = obs.get("setup", {}).get("dampers", {})
    if not dampers:
        return {}
    flat: dict[str, float] = {}
    for click in ["ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope"]:
        lf_val = dampers.get("lf", {}).get(click)
        rf_val = dampers.get("rf", {}).get(click)
        lr_val = dampers.get("lr", {}).get(click)
        rr_val = dampers.get("rr", {}).get(click)
        if lf_val is not None and rf_val is not None:
            flat[f"front_{click}"] = (float(lf_val) + float(rf_val)) / 2.0
        if lr_val is not None and rr_val is not None:
            flat[f"rear_{click}"] = (float(lr_val) + float(rr_val)) / 2.0
    return flat


# Damper click → telemetry signal pairs to correlate.
# Each tuple: (damper_param, telemetry_key, relationship_name, human_label)
_DAMPER_PHYSICS_PAIRS: list[tuple[str, str, str, str]] = [
    # LS comp → platform control signals
    ("front_ls_comp", "front_rh_std_mm", "front_ls_comp_vs_rh_var", "Front LS comp → front RH variance"),
    ("front_ls_comp", "pitch_range_braking_deg", "front_ls_comp_vs_pitch", "Front LS comp → braking pitch"),
    ("front_ls_comp", "front_shock_oscillation_hz", "front_ls_comp_vs_osc", "Front LS comp → front shock oscillation"),
    ("rear_ls_comp", "rear_rh_std_mm", "rear_ls_comp_vs_rh_var", "Rear LS comp → rear RH variance"),
    ("rear_ls_comp", "rear_shock_oscillation_hz", "rear_ls_comp_vs_osc", "Rear LS comp → rear shock oscillation"),
    # LS rbd → settle/rebound signals
    ("front_ls_rbd", "front_rh_std_mm", "front_ls_rbd_vs_rh_var", "Front LS rbd → front RH variance"),
    ("front_ls_rbd", "front_shock_oscillation_hz", "front_ls_rbd_vs_osc", "Front LS rbd → front shock oscillation"),
    ("rear_ls_rbd", "rear_rh_std_mm", "rear_ls_rbd_vs_rh_var", "Rear LS rbd → rear RH variance"),
    ("rear_ls_rbd", "rear_shock_oscillation_hz", "rear_ls_rbd_vs_osc", "Rear LS rbd → rear shock oscillation"),
    # HS comp → bump absorption signals
    ("front_hs_comp", "front_shock_vel_p99_mps", "front_hs_comp_vs_sv99", "Front HS comp → front shock vel p99"),
    ("front_hs_comp", "front_rh_excursion_measured_mm", "front_hs_comp_vs_excursion", "Front HS comp → front excursion"),
    ("front_hs_comp", "front_rh_std_hs_mm", "front_hs_comp_vs_rh_hs", "Front HS comp → front RH std (high-speed)"),
    ("rear_hs_comp", "rear_shock_vel_p99_mps", "rear_hs_comp_vs_sv99", "Rear HS comp → rear shock vel p99"),
    ("rear_hs_comp", "rear_rh_std_mm", "rear_hs_comp_vs_rh_var", "Rear HS comp → rear RH variance"),
    # HS rbd → rebound control at speed
    ("front_hs_rbd", "front_shock_vel_p99_mps", "front_hs_rbd_vs_sv99", "Front HS rbd → front shock vel p99"),
    ("rear_hs_rbd", "rear_shock_vel_p99_mps", "rear_hs_rbd_vs_sv99", "Rear HS rbd → rear shock vel p99"),
    # Cross-axis: damper balance → vehicle dynamics
    ("front_ls_comp", "understeer_low_speed_deg", "front_ls_comp_vs_us_low", "Front LS comp → low-speed understeer"),
    ("rear_ls_comp", "understeer_low_speed_deg", "rear_ls_comp_vs_us_low", "Rear LS comp → low-speed understeer"),
    ("front_hs_comp", "understeer_high_speed_deg", "front_hs_comp_vs_us_high", "Front HS comp → high-speed understeer"),
    ("rear_hs_comp", "understeer_high_speed_deg", "rear_hs_comp_vs_us_high", "Rear HS comp → high-speed understeer"),
    ("front_ls_comp", "body_roll_p95_deg", "front_ls_comp_vs_roll", "Front LS comp → body roll p95"),
    ("rear_ls_comp", "body_roll_p95_deg", "rear_ls_comp_vs_roll", "Rear LS comp → body roll p95"),
    ("front_ls_comp", "body_slip_p95_deg", "front_ls_comp_vs_slip", "Front LS comp → body slip p95"),
]


def _fit_damper_physics(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit damper click values against telemetry physics signals.

    This discovers which damper parameters actually affect which telemetry
    measurements — the core physics correlations that let the solver
    calibrate its damper recommendations from real data.
    """
    for damper_param, tel_key, rel_name, human_name in _DAMPER_PHYSICS_PAIRS:
        x_vals: list[float] = []
        y_vals: list[float] = []

        for obs in obs_list:
            flat = _extract_damper_flat(obs)
            d_val = flat.get(damper_param)
            t_val = obs.get("telemetry", {}).get(tel_key)
            if d_val is not None and t_val is not None and isinstance(t_val, (int, float)):
                x_vals.append(float(d_val))
                y_vals.append(float(t_val))

        # Need at least 4 samples AND at least 2 distinct x values
        if len(x_vals) < 4:
            continue
        if len(set(round(v, 1) for v in x_vals)) < 2:
            continue

        coeffs, r2 = _safe_linear_fit(x_vals, y_vals)
        if coeffs is None:
            continue

        residuals = np.array(y_vals) - np.polyval(coeffs, x_vals)
        models.relationships[rel_name] = FittedRelationship(
            name=human_name,
            x_param=damper_param,
            y_param=tel_key,
            fit_type="linear",
            coefficients=coeffs,
            r_squared=r2,
            sample_count=len(x_vals),
            residual_std=float(np.std(residuals)),
            x_values=x_vals,
            y_values=y_vals,
            x_min=min(x_vals),
            x_max=max(x_vals),
        )


def _calibrate_force_per_click(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Extract damper HS velocity slopes from measured shock velocity data.

    From the HS comp sweep we fit shock_vel_p99 vs click count and store the
    signed slope as a telemetry sensitivity metric. The slope captures how much
    peak shock velocity changes per HS comp click at the system level.

    NOTE: This function does NOT compute N/click (force-per-click). Converting
    the velocity slope to Newtons requires system-level knowledge (m_eff, k_eff,
    bump profile) that isn't available in the learner. If N/click calibration
    is ever needed, it should be derived in a separate validation step that has
    access to the car model.

    Emitted correction keys:
        - calibrated_hs_vel_slope_front: signed m/s per click (negative = more
          clicks reduces shock velocity, as physically expected)
        - calibrated_hs_vel_slope_rear: signed m/s per click
        - calibrated_hs_front_r2 / calibrated_hs_rear_r2: fit quality
        - calibrated_hs_front_n / calibrated_hs_rear_n: sample count
        - calibrated_pitch_per_ls_click_front: LS comp → pitch sensitivity
        - calibrated_ls_pitch_r2: fit quality for LS pitch
        - calibrated_rh_var_per_heave_unit_front: heave → RH variance slope
        - calibrated_heave_var_r2: fit quality for heave variance

    Physics first — lap time is not used here at all.
    """
    # Get HS comp → shock vel fitted relationship
    rel_hs_front = models.relationships.get("front_hs_comp_vs_sv99")
    rel_hs_rear = models.relationships.get("rear_hs_comp_vs_sv99")

    # Minimum quality gate: need R² > 0.15 and at least 6 samples
    MIN_R2 = 0.15
    MIN_N = 6

    if rel_hs_front and rel_hs_front.r_squared >= MIN_R2 and rel_hs_front.sample_count >= MIN_N:
        slope = rel_hs_front.coefficients[0] if rel_hs_front.coefficients else None
        if slope is not None and abs(slope) > 1e-6:
            # Store raw measurement (m/s per click) for audit
            models.corrections["calibrated_hs_vel_slope_front"] = float(slope)  # signed: negative = more clicks reduces vel
            models.corrections["calibrated_hs_front_r2"] = float(rel_hs_front.r_squared)
            models.corrections["calibrated_hs_front_n"] = int(rel_hs_front.sample_count)

            # NOTE: Do NOT convert directly to N/click here. The velocity slope
            # is a system-level output (shock vel p99 depends on track, speed,
            # aero load, etc.) — not a direct force measurement. Converting
            # via m_eff * slope gives values 2 orders of magnitude too low.
            #
            # Instead, the solver uses this slope to VALIDATE its force-per-click
            # estimate: if predicted Δvel/Δclick from force model ≠ measured slope,
            # the force-per-click estimate needs adjustment.

    if rel_hs_rear and rel_hs_rear.r_squared >= MIN_R2 and rel_hs_rear.sample_count >= MIN_N:
        slope = rel_hs_rear.coefficients[0] if rel_hs_rear.coefficients else None
        if slope is not None and abs(slope) > 1e-6:
            models.corrections["calibrated_hs_vel_slope_rear"] = float(slope)  # signed: negative = more clicks reduces vel
            models.corrections["calibrated_hs_rear_r2"] = float(rel_hs_rear.r_squared)
            models.corrections["calibrated_hs_rear_n"] = int(rel_hs_rear.sample_count)

            # Same note as front: slope is a validation metric, not N/click.

    # LS comp → braking pitch slope (physics signal, not lap time)
    rel_ls_pitch = models.relationships.get("front_ls_comp_vs_pitch")
    if rel_ls_pitch and rel_ls_pitch.r_squared >= MIN_R2 and rel_ls_pitch.sample_count >= MIN_N:
        slope = rel_ls_pitch.coefficients[0] if rel_ls_pitch.coefficients else None
        if slope is not None:
            models.corrections["calibrated_pitch_per_ls_click_front"] = float(slope)
            models.corrections["calibrated_ls_pitch_r2"] = float(rel_ls_pitch.r_squared)

    # Heave → RH variance slope (calibrates spring model)
    rel_heave_var = models.relationships.get("front_rh_var_vs_heave")
    if rel_heave_var and rel_heave_var.r_squared >= MIN_R2 and rel_heave_var.sample_count >= MIN_N:
        slope = rel_heave_var.coefficients[0] if rel_heave_var.coefficients else None
        if slope is not None:
            models.corrections["calibrated_rh_var_per_heave_unit_front"] = float(slope)
            models.corrections["calibrated_heave_var_r2"] = float(rel_heave_var.r_squared)


def _fit_lap_time_sensitivity(deltas: list[dict], models: EmpiricalModelSet) -> None:
    """Estimate which parameters most affect lap time from delta history.

    Lap time is a NOISY signal — driver consistency, track temp, and traffic
    all contaminate it. This provides a weak prior on parameter importance,
    NOT ground truth on which setting is "better". The solver uses physics-
    channel correlations (damper clicks -> shock vel -> zeta) as primary
    calibration signals. Lap time sensitivity is supplementary context only.

    Weighting by experiment cleanliness:
    - Single-change deltas: weight 1.0 (cleanest signal)
    - Two-change deltas: weight 0.5
    - Multi-change (3+): weight = 1.0 / num_changes
      Each parameter gets credit proportional to its delta magnitude,
      de-weighted by the number of confounding variables. All sessions
      contribute — nothing is hard-excluded.
    """
    param_effects: dict[str, list[tuple[float, float]]] = {}

    for d in deltas:
        lt_delta = d.get("lap_time_delta_s", 0)
        if abs(lt_delta) < 0.01:
            continue
        if abs(lt_delta) > 3.0:
            continue
        if d.get("confidence_level") not in ("high", "medium"):
            continue

        raw_num = d.get("num_setup_changes")
        try:
            num_changes = int(raw_num) if raw_num is not None else 99
        except (TypeError, ValueError):
            num_changes = 99
        num_changes = max(num_changes, 1)  # guard against 0 or negative
        if num_changes <= 1:
            experiment_weight = 1.0
        elif num_changes == 2:
            experiment_weight = 0.5
        else:
            # Multi-variable: contribute with heavy discount (1/n per variable)
            experiment_weight = 1.0 / num_changes

        confidence_weight = 1.0 if d.get("confidence_level") == "high" else 0.6
        weight = experiment_weight * confidence_weight

        for sc in d.get("setup_changes", []):
            if sc.get("significance") == "trivial":
                continue
            param = sc["parameter"]
            delta_val = sc.get("delta")
            if isinstance(delta_val, (int, float)) and abs(delta_val) > 0:
                # Lap time change per unit of parameter change
                sensitivity = lt_delta / delta_val
                param_effects.setdefault(param, []).append((sensitivity, weight))

    # Robust, confidence-weighted, regularized sensitivity per parameter
    sensitivities = []
    total_weight = 0.0
    for param, effects in param_effects.items():
        if not effects:
            continue
        vals = np.array([v for v, _ in effects], dtype=float)
        weights = np.array([w for _, w in effects], dtype=float)
        total_weight += float(np.sum(weights))

        median = float(np.median(vals))
        mad = float(np.median(np.abs(vals - median)))
        if mad > 1e-9:
            robust_sigma = 1.4826 * mad
            keep = np.abs(vals - median) <= 3.0 * robust_sigma
            vals = vals[keep]
            weights = weights[keep]
        if len(vals) == 0:
            continue

        weighted_mean = float(np.average(vals, weights=weights))
        # Shrink toward zero when validated sample weight is low.
        # Increased shrinkage denominator from 3.0 to 5.0 for more conservative estimates
        shrink = float(np.sum(weights) / (np.sum(weights) + 5.0))
        regularized_mean = weighted_mean * shrink
        sensitivities.append((param, abs(regularized_mean), regularized_mean))

    sensitivities.sort(key=lambda t: t[1], reverse=True)
    models.most_sensitive_parameters = [(p, s) for p, _, s in sensitivities[:10]]
    models.corrections["lap_time_surrogate_regularized"] = 1.0
    models.corrections["lap_time_surrogate_sample_weight"] = round(total_weight, 3)


def calibrate_roll_gain_from_thermals(observations: list[dict]) -> dict:
    """Calibrate front (and rear) roll gain from tyre inner/outer temperature spread.

    Physics:
        Inner shoulder hotter than outer → too much negative camber (inner loaded)
        Outer shoulder hotter than inner → too little negative camber (outer loaded)

        contact_error_deg = (inner_temp - outer_temp) * k_thermal
        Where k_thermal = 0.025 deg/°C (empirical constant)

        actual_camber = current_camber + contact_error_deg

        Since optimal_camber = -(roll_deg * roll_gain):
            roll_gain = -actual_camber / roll_deg

    Args:
        observations: List of session observation dicts. Each must contain:
            - telemetry.front_tyre_inner_temp
            - telemetry.front_tyre_outer_temp
            - telemetry.body_roll_p95_deg
            - setup.front_camber_deg (current static camber used that session)

    Returns:
        dict with keys: front_roll_gain, rear_roll_gain, sample_count, confidence
    """
    k_thermal = 0.025  # deg/°C — empirical constant (calibrated from Vision tread data)

    front_gains: list[float] = []
    rear_gains: list[float] = []

    for obs in observations:
        tel = obs.get("telemetry", {})
        setup = obs.get("setup", {})

        # ── Front roll gain calibration ──────────────────────────────
        inner_temp = tel.get("front_tyre_inner_temp")
        outer_temp = tel.get("front_tyre_outer_temp")
        body_roll = tel.get("body_roll_p95_deg")
        current_camber = setup.get("front_camber_deg")

        if all(v is not None for v in [inner_temp, outer_temp, body_roll, current_camber]):
            if abs(body_roll) > 0.2:  # guard against near-zero division
                contact_error_deg = (inner_temp - outer_temp) * k_thermal
                actual_camber = current_camber + contact_error_deg
                roll_gain = -actual_camber / body_roll
                # Sanity bounds: realistic roll gain range for GTP cars
                if 0.1 < roll_gain < 2.5:
                    front_gains.append(roll_gain)

        # ── Rear roll gain calibration ───────────────────────────────
        rear_inner = tel.get("rear_tyre_inner_temp")
        rear_outer = tel.get("rear_tyre_outer_temp")
        rear_camber = setup.get("rear_camber_deg")

        if all(v is not None for v in [rear_inner, rear_outer, body_roll, rear_camber]):
            if abs(body_roll) > 0.2:
                rear_contact_error = (rear_inner - rear_outer) * k_thermal
                rear_actual_camber = rear_camber + rear_contact_error
                rear_gain = -rear_actual_camber / body_roll
                if 0.1 < rear_gain < 2.5:
                    rear_gains.append(rear_gain)

    sample_count = len(front_gains)

    if sample_count < 3:
        return {
            "front_roll_gain": None,
            "rear_roll_gain": None,
            "sample_count": sample_count,
            "confidence": "insufficient",
        }

    front_mean = float(np.mean(front_gains))
    rear_mean = float(np.mean(rear_gains)) if rear_gains else None

    # Confidence from coefficient of variation (CV)
    front_cv = float(np.std(front_gains)) / front_mean if front_mean > 0 else 1.0
    if front_cv < 0.05 and sample_count >= 6:
        confidence = "high"
    elif front_cv < 0.15 and sample_count >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "front_roll_gain": round(front_mean, 4),
        "rear_roll_gain": round(rear_mean, 4) if rear_mean is not None else None,
        "sample_count": sample_count,
        "confidence": confidence,
    }


def _compute_corrections(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Compute correction factors where empirical data diverges from physics model.

    These corrections are the key output — they tell the solver "your physics
    predicts X, but the data consistently shows Y, so multiply by Y/X."

    Time decay: recent observations carry more weight than old ones.
    Minimum sessions gate: need >= MIN_SESSIONS_FOR_CORRECTIONS for non-prediction corrections.
    """
    now = datetime.now(timezone.utc)

    # Roll stiffness correction (time-weighted)
    roll_vals = []
    roll_weights = []
    for obs in obs_list:
        rg = obs.get("telemetry", {}).get("roll_gradient_deg_per_g", 0)
        if rg > 0.1:
            roll_vals.append(rg)
            roll_weights.append(_obs_time_weight(obs, now))

    if len(roll_vals) >= MIN_SESSIONS_FOR_CORRECTIONS:
        rv = np.array(roll_vals, dtype=float)
        rw = np.array(roll_weights, dtype=float)
        models.corrections["roll_gradient_measured_mean"] = float(np.average(rv, weights=rw))
        models.corrections["roll_gradient_measured_std"] = float(np.std(rv))
        models.corrections["roll_gradient_sample_count"] = len(roll_vals)

    # Roll-distribution proxy summary (time-weighted).  This is intentionally
    # not a true LLTD correction and must not be applied to ARB calibration.
    proxy_vals = []
    proxy_weights = []
    for obs in obs_list:
        telemetry = obs.get("telemetry", {})
        proxy = telemetry.get("roll_distribution_proxy", telemetry.get("lltd_measured", 0))
        if proxy > 0:
            proxy_vals.append(proxy)
            proxy_weights.append(_obs_time_weight(obs, now))
    if len(proxy_vals) >= MIN_SESSIONS_FOR_CORRECTIONS:
        pv = np.array(proxy_vals, dtype=float)
        pw = np.array(proxy_weights, dtype=float)
        models.corrections["roll_distribution_proxy_mean"] = float(np.average(pv, weights=pw))
        models.corrections["roll_distribution_proxy_std"] = float(np.std(pv))
        models.corrections["roll_distribution_proxy_sample_count"] = len(proxy_vals)
        models.corrections["lltd_measured_mean"] = float(np.average(pv, weights=pw))
        models.corrections["lltd_measured_std"] = float(np.std(pv))
        models.corrections["lltd_is_proxy"] = True

    # Effective mass correction (from variance data + spring rates).
    #
    # CRITICAL: `obs.setup.front_heave_nmm` stores the RAW garage value for the
    # heave control. For BMW/Porsche the garage exposes N/mm directly (typical
    # range 50-600). For Ferrari/Acura the garage exposes an INDEX (0-18 for
    # Ferrari, 1-26 for Acura) that must be decoded via the car's heave-spring
    # lookup before any rate arithmetic. Without that decode, `k_nm = heave *
    # 1000` becomes ~1000-26000 N/m instead of the actual ~50000-600000 N/m,
    # giving m_eff values 1-2 orders of magnitude wrong that can still pass the
    # [100, 4000] kg sanity guard for some index/(exc/v) combinations.
    #
    # The car decode lookup is optional: if it can't be loaded (e.g. test
    # fixtures without car_model imported), we fall back to a magnitude-based
    # heuristic (`heave < 30` → looks index-like → skip this observation
    # rather than corrupt the correction).
    car_for_decode = _get_car_for_decode(models.car)

    for obs in obs_list:
        heave_raw = obs.get("setup", {}).get("front_heave_nmm", 0) or 0.0
        telem = obs.get("telemetry", {})

        # Prefer high-speed filtered stats (>200kph) for m_eff correction to avoid
        # overestimation from low-speed segments where aero load is negligible.
        var = telem.get("front_rh_std_hs_mm", 0) or telem.get("front_rh_std_mm", 0)
        sv_p99 = telem.get("front_heave_vel_p95_hs_mps", 0) or telem.get("front_shock_vel_p99_mps", 0)

        if heave_raw <= 0 or var <= 0 or sv_p99 <= 0:
            continue

        heave_nmm = _decode_front_heave_nmm(heave_raw, car_for_decode)
        if heave_nmm is None or heave_nmm < 30.0:
            # Skip: looks index-like with no decode available, or implausibly
            # soft. Better to drop one observation than corrupt the mean.
            continue

        # excursion_p99 ≈ 2.33 * sigma
        exc = var * 2.33
        # exc = v_p99 * sqrt(m_eff / k) → m_eff = k * (exc/v_p99)^2
        k_nm = heave_nmm * 1000.0
        m_eff = k_nm * (exc / 1000 / sv_p99) ** 2
        # Sanity check: high-downforce cars (Ferrari LMH) at high speed have aero spring
        # k_aero >> k_mechanical, collapsing apparent m_eff toward 0. Any value below the
        # minimum physically plausible sprung corner mass is an aero-contaminated reading
        # and must be discarded. Range: [100 kg, 4000 kg].
        if m_eff < 100.0 or m_eff > 4000.0:
            continue
        m_eff_val = round(m_eff, 1)
        models.corrections.setdefault("m_eff_front_values", [])
        models.corrections["m_eff_front_values"].append(m_eff_val)

    m_eff_samples = models.corrections.get("m_eff_front_values", [])
    if m_eff_samples:
        models.corrections["m_eff_front_empirical_mean"] = float(np.mean(m_eff_samples))
        models.corrections["m_eff_front_empirical_std"] = float(np.std(m_eff_samples))


def _get_car_for_decode(car_name: str) -> Any | None:
    """Lazy-load a CarModel for indexed-heave decoding.

    Returns None if the car can't be loaded (avoids hard-coupling the learner
    to `car_model.cars` for tests / minimal environments).
    """
    if not car_name:
        return None
    try:
        from car_model.cars import get_car  # local import to avoid cycle
        return get_car(car_name, apply_calibration=False)
    except Exception:
        return None


def _decode_front_heave_nmm(raw: float, car: Any | None) -> float | None:
    """Convert a raw garage front-heave value to N/mm.

    Logic:
    - If `car.heave_spring.front_rate_from_setting` decodes (returns a
      meaningfully different value), use that — handles Ferrari/Acura
      indexed-control cars that expose `front_setting_index_range`.
    - Else if the car has a `HeaveSpringTable` with `front_heave_rate_from_index`,
      use that — defensive path for any car that switches to the table form.
    - Else if the raw value looks like an N/mm rate (>= 30), accept it as-is —
      handles BMW/Porsche.
    - Else return None to signal the caller to skip this observation.
    """
    raw = float(raw)
    if raw <= 0:
        return None

    # Try linear-slope decode (HeaveSpringModel) first.
    hs = getattr(car, "heave_spring", None) if car is not None else None
    if hs is not None and hasattr(hs, "front_rate_from_setting"):
        try:
            decoded = float(hs.front_rate_from_setting(raw))
            # If the decode meaningfully differs from `raw` (i.e. the car
            # actually uses indexed controls), trust it.
            if decoded > 0 and abs(decoded - raw) > 1e-3:
                return decoded
        except Exception:
            pass

    # Try lookup-table decode (HeaveSpringTable on Ferrari etc.).
    table = getattr(car, "heave_spring_table", None) if car is not None else None
    if table is not None and hasattr(table, "front_heave_rate_from_index"):
        try:
            decoded = float(table.front_heave_rate_from_index(raw))
            if decoded > 0 and abs(decoded - raw) > 1e-3:
                return decoded
        except Exception:
            pass

    # Fall through: raw value is already N/mm if it's plausibly large.
    if raw >= 30.0:
        return raw
    return None


def _obs_time_weight(obs: dict, now: datetime | None = None) -> float:
    """Compute time-decay weight for an observation.

    Returns TIME_DECAY_BASE ^ days_since_observation.
    Recent sessions weigh more; old sessions decay toward zero.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts_str = obs.get("timestamp", "")
    if not ts_str:
        return 0.5  # unknown age → half weight
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - ts).total_seconds() / 86400.0)
        return TIME_DECAY_BASE ** days
    except (ValueError, TypeError):
        return 0.5


def fit_prediction_errors(
    observations: list[dict],
    models: EmpiricalModelSet,
) -> None:
    """Fit prediction-vs-measurement corrections from solver predictions.

    For each observation that has both solver_predictions and measured telemetry,
    compute the error (measured - predicted) and store an exponentially-weighted
    moving average as correction factors the solver can query.

    This is the core feedback loop: solver predicts → we measure → we correct.
    """
    # Metrics we can compare: solver prediction key → telemetry measurement key
    PREDICTION_METRICS = {
        "front_rh_std_mm": "front_rh_std_mm",
        "rear_rh_std_mm": "rear_rh_std_mm",
        "lltd_predicted": "lltd_measured",
        "body_roll_predicted_deg_per_g": "roll_gradient_deg_per_g",
        "front_bottoming_predicted": "front_bottoming_events",
        "front_heave_travel_used_pct": "front_heave_travel_used_pct",
        "front_excursion_mm": "front_rh_excursion_measured_mm",
        "braking_pitch_deg": "pitch_range_braking_deg",
        "front_lock_p95": "front_braking_lock_ratio_p95",
        "rear_power_slip_p95": "rear_power_slip_ratio_p95",
        "body_slip_p95_deg": "body_slip_p95_deg",
        "understeer_low_deg": "understeer_low_speed_deg",
        "understeer_high_deg": "understeer_high_speed_deg",
        "front_pressure_hot_kpa": "front_pressure_mean_kpa",
        "rear_pressure_hot_kpa": "rear_pressure_mean_kpa",
        "m_eff_front_kg": None,  # no direct telemetry equivalent
    }

    now = datetime.now(timezone.utc)

    for pred_key, meas_key in PREDICTION_METRICS.items():
        if meas_key is None:
            continue

        errors: list[float] = []
        weights: list[float] = []

        for obs in observations:
            pred = obs.get("solver_predictions", {}).get(pred_key)
            meas = obs.get("telemetry", {}).get(meas_key)
            if pred is None or meas is None:
                continue
            try:
                error = float(meas) - float(pred)
            except (TypeError, ValueError):
                continue

            w = _obs_time_weight(obs, now)
            errors.append(error)
            weights.append(w)

        if len(errors) < 3:
            continue

        errors_arr = np.array(errors, dtype=float)
        weights_arr = np.array(weights, dtype=float)

        # Exponentially-weighted mean error = correction
        weighted_mean = float(np.average(errors_arr, weights=weights_arr))
        weighted_std = float(np.sqrt(
            np.average((errors_arr - weighted_mean) ** 2, weights=weights_arr)
        ))

        correction_key = f"prediction_correction_{pred_key}"
        models.corrections[correction_key] = round(weighted_mean, 4)
        models.corrections[f"{correction_key}_std"] = round(weighted_std, 4)
        models.corrections[f"{correction_key}_n"] = len(errors)
