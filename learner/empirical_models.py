"""Empirical models fitted from accumulated observations.

These models don't REPLACE the physics solver — they INFORM it. Each model
captures a relationship that the physics engine approximates with theory,
and the empirical model provides a data-driven correction factor.

The key models:
1. Aero compression model: how much does the car actually compress vs V²?
2. Roll gradient model: actual deg/g vs what the spring model predicts
3. Heave effective mass: calibrated m_eff per track surface (varies!)
4. LLTD vs ARB blade: real LLTD response vs model prediction
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
    """Fit y = a*x + b, return (coefficients, r²)."""
    if len(x) < 2:
        return [], 0.0
    x_arr = np.array(x, dtype=float)
    y_arr = np.array(y, dtype=float)
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

    # ── 2. LLTD vs rear ARB blade ──────────────────────────────────
    _fit_lltd_vs_arb(observations, models)

    # ── 3. Heave spring → platform variance ────────────────────────
    _fit_heave_to_variance(observations, models)

    # ── 4. Third spring → rear variance ────────────────────────────
    _fit_third_to_variance(observations, models)

    # ── 5. Aero compression model ──────────────────────────────────
    _fit_aero_compression(observations, models)

    # ── 6. Settle time vs damper clicks ────────────────────────────
    _fit_settle_time(observations, models)

    # ── 7. Lap time sensitivity from deltas ────────────────────────
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

    if len(x) >= 2:
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


def _fit_lltd_vs_arb(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit LLTD as a function of rear ARB blade."""
    x, y = [], []
    for obs in obs_list:
        blade = obs.get("setup", {}).get("rear_arb_blade")
        lltd = obs.get("telemetry", {}).get("lltd_measured", 0)
        if blade is not None and lltd > 0:
            x.append(float(blade))
            y.append(lltd)

    if len(x) >= 2:
        coeffs, r2 = _safe_linear_fit(x, y)
        if coeffs:
            models.relationships["lltd_vs_rear_arb"] = FittedRelationship(
                name="LLTD vs rear ARB blade",
                x_param="rear_arb_blade",
                y_param="lltd_measured",
                fit_type="linear",
                coefficients=coeffs,
                r_squared=r2,
                sample_count=len(x),
                residual_std=float(np.std(np.array(y) - np.polyval(coeffs, x))),
                x_values=x,
                y_values=y,
                x_min=min(x),
                x_max=max(x),
            )


def _fit_heave_to_variance(obs_list: list[dict], models: EmpiricalModelSet) -> None:
    """Fit front ride height variance as function of heave spring rate."""
    x, y = [], []
    for obs in obs_list:
        heave = obs.get("setup", {}).get("front_heave_nmm")
        var = obs.get("telemetry", {}).get("front_rh_std_mm", 0)
        if heave and heave > 0 and var > 0:
            x.append(float(heave))
            y.append(var)

    if len(x) >= 2:
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

    if len(x) >= 2:
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

    if len(x) >= 2:
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


def _fit_lap_time_sensitivity(deltas: list[dict], models: EmpiricalModelSet) -> None:
    """Estimate which parameters most affect lap time from delta history."""
    param_effects: dict[str, list[float]] = {}

    for d in deltas:
        lt_delta = d.get("lap_time_delta_s", 0)
        if abs(lt_delta) < 0.01:
            continue
        if d.get("confidence_level") not in ("high", "medium"):
            continue

        for sc in d.get("setup_changes", []):
            if sc.get("significance") == "trivial":
                continue
            param = sc["parameter"]
            delta_val = sc.get("delta")
            if isinstance(delta_val, (int, float)) and abs(delta_val) > 0:
                # Lap time change per unit of parameter change
                sensitivity = lt_delta / delta_val
                param_effects.setdefault(param, []).append(sensitivity)

    # Average sensitivity per parameter
    sensitivities = []
    for param, effects in param_effects.items():
        if len(effects) >= 1:
            mean_sens = float(np.mean(effects))
            sensitivities.append((param, abs(mean_sens), mean_sens))

    sensitivities.sort(key=lambda t: t[1], reverse=True)
    models.most_sensitive_parameters = [(p, s) for p, _, s in sensitivities[:10]]


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
    """
    # Roll stiffness correction
    # Physics model predicts roll. If measured roll is consistently different,
    # the roll stiffness in the model needs a correction factor.
    roll_pred = []
    roll_meas = []
    for obs in obs_list:
        rg = obs.get("telemetry", {}).get("roll_gradient_deg_per_g", 0)
        if rg > 0.1:
            roll_meas.append(rg)

    if len(roll_meas) >= 2:
        models.corrections["roll_gradient_measured_mean"] = float(np.mean(roll_meas))
        models.corrections["roll_gradient_measured_std"] = float(np.std(roll_meas))
        models.corrections["roll_gradient_sample_count"] = len(roll_meas)

    # LLTD correction
    lltd_vals = []
    for obs in obs_list:
        lltd = obs.get("telemetry", {}).get("lltd_measured", 0)
        if lltd > 0:
            lltd_vals.append(lltd)
    if lltd_vals:
        models.corrections["lltd_measured_mean"] = float(np.mean(lltd_vals))
        models.corrections["lltd_measured_std"] = float(np.std(lltd_vals))

    # Effective mass correction (from variance data + spring rates)
    for obs in obs_list:
        heave = obs.get("setup", {}).get("front_heave_nmm", 0)
        var = obs.get("telemetry", {}).get("front_rh_std_mm", 0)
        sv_p99 = obs.get("telemetry", {}).get("front_shock_vel_p99_mps", 0)
        if heave > 0 and var > 0 and sv_p99 > 0:
            # excursion_p99 ≈ 2.33 * sigma
            exc = var * 2.33
            # exc = v_p99 * sqrt(m_eff / k) → m_eff = k * (exc/v_p99)^2
            # NOTE: var and sv_p99 are lap-wide statistics (not filtered to
            # high-speed straights), which inflates m_eff estimates. Treat
            # these as rough indicators, not precise calibration values.
            k_nm = heave * 1000
            m_eff = k_nm * (exc / 1000 / sv_p99) ** 2
            m_eff_val = round(m_eff, 1)
            models.corrections.setdefault("m_eff_front_values", [])
            models.corrections["m_eff_front_values"].append(m_eff_val)

    m_eff_samples = models.corrections.get("m_eff_front_values", [])
    if m_eff_samples:
        models.corrections["m_eff_front_empirical_mean"] = float(np.mean(m_eff_samples))
        models.corrections["m_eff_front_empirical_std"] = float(np.std(m_eff_samples))
