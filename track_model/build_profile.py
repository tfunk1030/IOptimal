"""Build a TrackProfile from an IBT telemetry file.

Usage:
    python -m track_model.build_profile path/to/file.ibt [--output path/to/output.json]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from track_model.ibt_parser import IBTFile
from track_model.profile import TrackProfile, BrakingZone, Corner, KerbEvent


def build_profile(ibt_path: str | Path) -> TrackProfile:
    """Parse an IBT file and extract a complete TrackProfile."""
    ibt = IBTFile(ibt_path)

    track = ibt.track_info()
    car = ibt.car_info()

    # Parse track length from session info (e.g., "5.7938 km")
    track_length_str = track.get("track_length", "0 km")
    track_length_m = float(track_length_str.split()[0]) * 1000

    # Find best lap
    best_range = ibt.best_lap_indices(min_time=60.0)
    if best_range is None:
        raise ValueError("No valid laps found in IBT file")

    start, end = best_range
    lap_time_ch = ibt.channel("LapCurrentLapTime")
    best_lap_time = float(lap_time_ch[end])

    # Extract channels for best lap only
    speed_ms = ibt.channel("Speed")[start:end + 1]
    lat_accel = ibt.channel("LatAccel")[start:end + 1]
    long_accel = ibt.channel("LongAccel")[start:end + 1]
    vert_accel = ibt.channel("VertAccel")[start:end + 1]
    brake = ibt.channel("Brake")[start:end + 1]
    throttle = ibt.channel("Throttle")[start:end + 1]
    lap_dist = ibt.channel("LapDist")[start:end + 1]
    alt = ibt.channel("Alt")[start:end + 1] if ibt.has_channel("Alt") else None
    steering = ibt.channel("SteeringWheelAngle")[start:end + 1]

    # Shock velocities (absolute values for spectrum analysis)
    lf_sv = ibt.channel("LFshockVel")[start:end + 1]
    rf_sv = ibt.channel("RFshockVel")[start:end + 1]
    lr_sv = ibt.channel("LRshockVel")[start:end + 1]
    rr_sv = ibt.channel("RRshockVel")[start:end + 1]

    # Rumble strip channels for kerb detection
    rumble_lf = ibt.channel("TireLF_RumblePitch")[start:end + 1] if ibt.has_channel("TireLF_RumblePitch") else None
    rumble_rf = ibt.channel("TireRF_RumblePitch")[start:end + 1] if ibt.has_channel("TireRF_RumblePitch") else None

    # Unit conversions
    speed_kph = speed_ms * 3.6
    lat_g = lat_accel / 9.81
    long_g = long_accel / 9.81
    vert_g = vert_accel / 9.81

    # === Session-wide peaks (track demands include all laps, not just cleanest) ===
    on_track = ibt.channel("IsOnTrack")
    ot_mask = on_track > 0.5
    all_speed_kph = ibt.channel("Speed") * 3.6
    all_lat_g = ibt.channel("LatAccel") / 9.81
    all_long_g = ibt.channel("LongAccel") / 9.81
    all_vert_g = ibt.channel("VertAccel") / 9.81

    peak_lat_g = float(np.max(np.abs(all_lat_g[ot_mask])))
    peak_braking_g = float(np.max(-all_long_g[ot_mask]))
    peak_accel_g = float(np.max(all_long_g[ot_mask]))
    peak_vert_g = float(np.max(np.abs(all_vert_g[ot_mask])))
    session_max_speed = float(np.max(all_speed_kph[ot_mask]))

    # === Speed profile (best lap) ===
    speed_profile = _build_speed_profile(speed_kph)
    peak_vert_g = float(np.max(np.abs(vert_g)))

    # === Braking zones ===
    braking_zones = _find_braking_zones(
        speed_kph, long_g, brake, lap_dist, ibt.dt,
    )

    # === Corners ===
    corners = _find_corners(speed_kph, lat_g, steering, lap_dist)

    # === Shock velocity spectrum ===
    front_sv = np.concatenate([np.abs(lf_sv), np.abs(rf_sv)])
    rear_sv = np.concatenate([np.abs(lr_sv), np.abs(rr_sv)])

    shock_hist_front = _build_shock_histogram(front_sv)
    shock_hist_rear = _build_shock_histogram(rear_sv)
    shock_by_sector = _shock_by_sector(
        np.abs(lf_sv), np.abs(rf_sv), np.abs(lr_sv), np.abs(rr_sv),
        lap_dist, track_length_m,
    )

    # === Kerb events ===
    kerb_events = _find_kerb_events(vert_g, rumble_lf, rumble_rf, lap_dist)

    # === Elevation ===
    elev_profile = []
    elev_change = 0.0
    if alt is not None:
        elev_profile = _build_elevation_profile(alt, lap_dist, track_length_m)
        elev_change = float(np.max(alt) - np.min(alt))

    # === Lateral G distribution (session-wide, on-track only) ===
    abs_lat_g_ot = np.abs(all_lat_g[ot_mask])
    lateral_g_dist = {
        "mean_abs": round(float(np.mean(abs_lat_g_ot)), 2),
        "p90": round(float(np.percentile(abs_lat_g_ot, 90)), 2),
        "p95": round(float(np.percentile(abs_lat_g_ot, 95)), 2),
        "p99": round(float(np.percentile(abs_lat_g_ot, 99)), 2),
        "max": round(float(np.max(abs_lat_g_ot)), 2),
    }

    # === Body roll distribution ===
    body_roll_dist: dict[str, float] = {}
    roll_gradient = 0.0

    if ibt.has_channel("Roll"):
        all_roll = ibt.channel("Roll")[ot_mask]  # radians in iRacing
        all_roll_deg = np.degrees(all_roll)
        abs_roll = np.abs(all_roll_deg)
        body_roll_dist = {
            "mean_abs": round(float(np.mean(abs_roll)), 2),
            "p95": round(float(np.percentile(abs_roll, 95)), 2),
            "max": round(float(np.max(abs_roll)), 2),
        }

        # Roll gradient: derived from body roll statistics and lateral G
        # Direct linear fit of |roll| vs |lat_g| is unreliable because:
        # 1. Roll channel sign convention varies
        # 2. Aero roll resistance is speed-dependent (non-linear)
        # 3. Combined lateral+longitudinal events muddy the correlation
        #
        # Instead: use the p95 ratio as a robust estimator.
        # At p95 cornering (~2g), the car exhibits p95 roll (~1.6°).
        # This gives roll_gradient ≈ p95_roll / p95_lat_g.
        p95_lat = float(np.percentile(abs_lat_g_ot, 95))
        if p95_lat > 0.5 and float(np.percentile(abs_roll, 95)) > 0.1:
            roll_gradient = round(
                float(np.percentile(abs_roll, 95)) / p95_lat, 3
            )
    else:
        # Derive roll from ride height differential (LF-RF, LR-RR)
        if (ibt.has_channel("LFrideHeight") and ibt.has_channel("RFrideHeight")):
            lf_rh = ibt.channel("LFrideHeight")[ot_mask]
            rf_rh = ibt.channel("RFrideHeight")[ot_mask]
            # Roll angle ≈ atan((LF-RF) / track_width)
            # iRacing ride heights are in meters
            track_w_m = 1.6  # approximate front track width
            roll_from_rh = np.degrees(np.arctan((lf_rh - rf_rh) / track_w_m))
            abs_roll_rh = np.abs(roll_from_rh)
            body_roll_dist = {
                "mean_abs": round(float(np.mean(abs_roll_rh)), 2),
                "p95": round(float(np.percentile(abs_roll_rh, 95)), 2),
                "max": round(float(np.max(abs_roll_rh)), 2),
            }
            p95_lat = float(np.percentile(abs_lat_g_ot, 95))
            if p95_lat > 0.5 and float(np.percentile(abs_roll_rh, 95)) > 0.1:
                roll_gradient = round(
                    float(np.percentile(abs_roll_rh, 95)) / p95_lat, 3
                )

    # === Ride height statistics ===
    ride_heights: dict[str, dict] = {}
    for ch_name, label in [
        ("LFrideHeight", "LF"), ("RFrideHeight", "RF"),
        ("LRrideHeight", "LR"), ("RRrideHeight", "RR"),
    ]:
        if ibt.has_channel(ch_name):
            rh = ibt.channel(ch_name)[ot_mask] * 1000  # m → mm
            ride_heights[label] = {
                "mean_mm": round(float(np.mean(rh)), 1),
                "min_mm": round(float(np.min(rh)), 1),
                "max_mm": round(float(np.max(rh)), 1),
                "p05_mm": round(float(np.percentile(rh, 5)), 1),
                "p95_mm": round(float(np.percentile(rh, 95)), 1),
                "std_mm": round(float(np.std(rh)), 1),
            }

    # === LLTD from ride height deflections in corners ===
    lltd_measured = 0.0
    if all(ibt.has_channel(c) for c in
           ["LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]):
        lf_rh_ot = ibt.channel("LFrideHeight")[ot_mask] * 1000
        rf_rh_ot = ibt.channel("RFrideHeight")[ot_mask] * 1000
        lr_rh_ot = ibt.channel("LRrideHeight")[ot_mask] * 1000
        rr_rh_ot = ibt.channel("RRrideHeight")[ot_mask] * 1000

        # In corners (|lat_g| > 1.0), compute front vs rear deflection
        # Deflection = |left - right| ride height difference (proportional to
        # roll stiffness contribution from that axle)
        corner_mask = abs_lat_g_ot > 1.0
        if np.sum(corner_mask) > 100:
            front_deflection = np.abs(lf_rh_ot[corner_mask] - rf_rh_ot[corner_mask])
            rear_deflection = np.abs(lr_rh_ot[corner_mask] - rr_rh_ot[corner_mask])
            mean_front = float(np.mean(front_deflection))
            mean_rear = float(np.mean(rear_deflection))
            total = mean_front + mean_rear
            if total > 0.1:
                lltd_measured = round(mean_front / total, 3)

    # === Surface profile (detailed breakdown) ===
    surface_profile_data = {
        "front_p50_mmps": round(float(np.percentile(front_sv * 1000, 50)), 1),
        "front_p95_mmps": round(float(np.percentile(front_sv * 1000, 95)), 1),
        "front_p99_mmps": round(float(np.percentile(front_sv * 1000, 99)), 1),
        "rear_p50_mmps": round(float(np.percentile(rear_sv * 1000, 50)), 1),
        "rear_p95_mmps": round(float(np.percentile(rear_sv * 1000, 95)), 1),
        "rear_p99_mmps": round(float(np.percentile(rear_sv * 1000, 99)), 1),
        "front_p99_p95_ratio": round(float(
            np.percentile(front_sv, 99) / max(np.percentile(front_sv, 95), 1e-6)
        ), 2),
        "rear_p99_p95_ratio": round(float(
            np.percentile(rear_sv, 99) / max(np.percentile(rear_sv, 95), 1e-6)
        ), 2),
    }

    # === Telemetry source ===
    telemetry_src = f"{Path(ibt_path).name} — best lap {best_lap_time:.3f}s"

    profile = TrackProfile(
        track_name=track.get("track_name", "Unknown"),
        track_config=track.get("track_config", ""),
        track_length_m=track_length_m,
        car=car.get("car", "Unknown"),
        best_lap_time_s=round(best_lap_time, 3),
        speed_bands_kph=speed_profile,
        median_speed_kph=round(float(np.median(speed_kph)), 1),
        max_speed_kph=round(session_max_speed, 1),
        min_speed_kph=round(float(np.min(speed_kph[speed_kph > 10])), 1),
        peak_lat_g=round(peak_lat_g, 2),
        peak_braking_g=round(peak_braking_g, 2),
        peak_accel_g=round(peak_accel_g, 2),
        peak_vertical_g=round(float(peak_vert_g), 2),
        braking_zones=braking_zones,
        corners=corners,
        shock_vel_histogram_front=shock_hist_front,
        shock_vel_histogram_rear=shock_hist_rear,
        shock_vel_by_sector=shock_by_sector,
        shock_vel_p50_front_mps=round(float(np.percentile(front_sv, 50)), 4),
        shock_vel_p95_front_mps=round(float(np.percentile(front_sv, 95)), 4),
        shock_vel_p99_front_mps=round(float(np.percentile(front_sv, 99)), 4),
        shock_vel_p50_rear_mps=round(float(np.percentile(rear_sv, 50)), 4),
        shock_vel_p95_rear_mps=round(float(np.percentile(rear_sv, 95)), 4),
        shock_vel_p99_rear_mps=round(float(np.percentile(rear_sv, 99)), 4),
        kerb_events=kerb_events,
        elevation_profile=elev_profile,
        elevation_change_m=round(elev_change, 1),
        lateral_g=lateral_g_dist,
        body_roll_deg=body_roll_dist,
        ride_heights_mm=ride_heights,
        roll_gradient_deg_per_g=roll_gradient,
        lltd_measured=lltd_measured,
        surface_profile=surface_profile_data,
        telemetry_source=telemetry_src,
    )
    return profile


def _build_speed_profile(speed_kph: np.ndarray) -> dict[str, float]:
    """Build speed band distribution (% of lap in each 20 kph band)."""
    bands = {}
    total = len(speed_kph)
    for lo in range(0, 320, 20):
        hi = lo + 20
        label = f"{lo}-{hi}"
        count = np.sum((speed_kph >= lo) & (speed_kph < hi))
        pct = round(float(count / total * 100), 1)
        if pct > 0:
            bands[label] = pct
    return bands


def _find_braking_zones(
    speed_kph: np.ndarray,
    long_g: np.ndarray,
    brake: np.ndarray,
    lap_dist: np.ndarray,
    dt: float,
) -> list[BrakingZone]:
    """Detect braking zones from brake pedal and longitudinal g."""
    # Find contiguous braking regions (brake > 10%)
    braking = brake > 0.10
    zones = []

    # Find edges
    edges = np.diff(braking.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    # Handle edge cases
    if braking[0]:
        starts = np.insert(starts, 0, 0)
    if braking[-1]:
        ends = np.append(ends, len(braking))

    n_zones = min(len(starts), len(ends))
    for i in range(n_zones):
        s, e = starts[i], ends[i]
        duration_samples = e - s
        if duration_samples < 10:  # Skip very brief brake taps (< 0.17s at 60Hz)
            continue

        entry_speed = float(speed_kph[s])
        min_speed = float(np.min(speed_kph[s:e]))
        peak_decel = float(np.max(-long_g[s:e]))
        dist_start = float(lap_dist[s])
        braking_dist = float(lap_dist[min(e, len(lap_dist) - 1)] - lap_dist[s])

        # Handle wrap-around at S/F line
        if braking_dist < 0:
            continue

        if entry_speed < 50:  # Skip pit lane braking
            continue

        zones.append(BrakingZone(
            lap_dist_m=round(dist_start, 1),
            entry_speed_kph=round(entry_speed, 1),
            min_speed_kph=round(min_speed, 1),
            peak_decel_g=round(peak_decel, 2),
            braking_dist_m=round(braking_dist, 1),
        ))

    return zones


def _find_corners(
    speed_kph: np.ndarray,
    lat_g: np.ndarray,
    steering: np.ndarray,
    lap_dist: np.ndarray,
) -> list[Corner]:
    """Detect corners from lateral g and steering angle."""
    # Smooth lateral g to avoid noise
    kernel = np.ones(15) / 15  # 0.25s window at 60Hz
    lat_smooth = np.convolve(np.abs(lat_g), kernel, mode="same")

    # Corners are where |lat_g| > 0.5g sustained
    in_corner = lat_smooth > 0.5
    edges = np.diff(in_corner.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    if in_corner[0]:
        starts = np.insert(starts, 0, 0)
    if in_corner[-1]:
        ends = np.append(ends, len(in_corner))

    corners = []
    n = min(len(starts), len(ends))
    for i in range(n):
        s, e = starts[i], ends[i]
        if e - s < 10:  # Skip very short events
            continue

        segment_lat = lat_g[s:e]
        segment_speed = speed_kph[s:e]
        segment_steer = steering[s:e]

        # Find apex (minimum speed in corner)
        apex_idx = s + int(np.argmin(segment_speed))
        apex_speed = float(segment_speed[np.argmin(segment_speed)])
        peak_lat = float(np.max(np.abs(segment_lat)))

        # Direction from average steering angle
        avg_steer = float(np.mean(segment_steer))
        direction = "left" if avg_steer > 0 else "right"

        # Estimate radius: r = v^2 / (a_lat)
        apex_speed_ms = apex_speed / 3.6
        apex_lat_ms2 = peak_lat * 9.81
        radius = (apex_speed_ms ** 2) / apex_lat_ms2 if apex_lat_ms2 > 0.5 else 999

        dist = float(lap_dist[min(apex_idx, len(lap_dist) - 1)])

        corners.append(Corner(
            lap_dist_m=round(dist, 1),
            speed_kph=round(apex_speed, 1),
            peak_lat_g=round(peak_lat, 2),
            radius_m=round(radius, 1),
            direction=direction,
        ))

    return corners


def _build_shock_histogram(shock_vel_abs: np.ndarray) -> dict[str, int]:
    """Build histogram of absolute shock velocities.

    Bins: 0-5, 5-10, 10-20, 20-50, 50-100, 100-200, 200+ mm/s
    """
    vel_mm = shock_vel_abs * 1000  # Convert m/s to mm/s
    bins = [0, 5, 10, 20, 50, 100, 200, 500]
    labels = ["0-5", "5-10", "10-20", "20-50", "50-100", "100-200", "200+"]
    counts, _ = np.histogram(vel_mm, bins=bins + [np.inf])
    # Merge last two bins
    counts[-2] += counts[-1]
    counts = counts[:-1]
    return {label: int(count) for label, count in zip(labels, counts)}


def _shock_by_sector(
    lf_abs: np.ndarray, rf_abs: np.ndarray,
    lr_abs: np.ndarray, rr_abs: np.ndarray,
    lap_dist: np.ndarray, track_length_m: float,
    n_sectors: int = 10,
) -> dict[str, dict]:
    """Shock velocity statistics broken down by track sector."""
    sector_len = track_length_m / n_sectors
    result = {}

    for s in range(n_sectors):
        lo = s * sector_len
        hi = (s + 1) * sector_len
        mask = (lap_dist >= lo) & (lap_dist < hi)

        if np.sum(mask) < 5:
            continue

        front = np.concatenate([lf_abs[mask], rf_abs[mask]])
        rear = np.concatenate([lr_abs[mask], rr_abs[mask]])

        label = f"S{s + 1}"
        result[label] = {
            "dist_range_m": [round(lo, 0), round(hi, 0)],
            "front_p50_mps": round(float(np.percentile(front, 50)), 4),
            "front_p95_mps": round(float(np.percentile(front, 95)), 4),
            "front_p99_mps": round(float(np.percentile(front, 99)), 4),
            "rear_p50_mps": round(float(np.percentile(rear, 50)), 4),
            "rear_p95_mps": round(float(np.percentile(rear, 95)), 4),
            "rear_p99_mps": round(float(np.percentile(rear, 99)), 4),
        }

    return result


def _find_kerb_events(
    vert_g: np.ndarray,
    rumble_lf: np.ndarray | None,
    rumble_rf: np.ndarray | None,
    lap_dist: np.ndarray,
) -> list[KerbEvent]:
    """Detect kerb strikes from rumble strip channels and vertical g spikes."""
    events = []

    # Primary method: rumble strip channels (iRacing reports kerb contact directly)
    if rumble_lf is not None and rumble_rf is not None:
        # Rumble pitch > 0 means tyre is on a rumble strip
        lf_on = rumble_lf > 0
        rf_on = rumble_rf > 0
        either_on = lf_on | rf_on

        if np.any(either_on):
            edges = np.diff(either_on.astype(int))
            starts = np.where(edges == 1)[0] + 1
            ends = np.where(edges == -1)[0] + 1

            if either_on[0]:
                starts = np.insert(starts, 0, 0)
            if either_on[-1]:
                ends = np.append(ends, len(either_on))

            n = min(len(starts), len(ends))
            for i in range(n):
                s, e = starts[i], ends[i]
                if e - s < 2:  # Skip single-sample noise
                    continue
                # Severity from vertical g deviation during kerb contact
                vert_deviation = np.abs(vert_g[s:e] - np.median(vert_g))
                severity = float(np.max(vert_deviation)) if len(vert_deviation) > 0 else 0.0
                dist = float(lap_dist[min(s, len(lap_dist) - 1)])

                lf_active = np.any(lf_on[s:e])
                rf_active = np.any(rf_on[s:e])
                if lf_active and not rf_active:
                    side = "left"
                elif rf_active and not lf_active:
                    side = "right"
                else:
                    side = "both"

                events.append(KerbEvent(
                    lap_dist_m=round(dist, 1),
                    severity=round(severity, 2),
                    side=side,
                ))

    # Fallback: vertical g spikes if no rumble channels or no events found
    if not events:
        vert_deviation = np.abs(vert_g - np.median(vert_g))
        threshold = 0.8  # g deviation (lowered from 1.5)

        spike_mask = vert_deviation > threshold
        if np.any(spike_mask):
            edges = np.diff(spike_mask.astype(int))
            starts = np.where(edges == 1)[0] + 1
            ends = np.where(edges == -1)[0] + 1
            if spike_mask[0]:
                starts = np.insert(starts, 0, 0)
            if spike_mask[-1]:
                ends = np.append(ends, len(spike_mask))

            n = min(len(starts), len(ends))
            for i in range(n):
                s, e = starts[i], ends[i]
                severity = float(np.max(vert_deviation[s:e]))
                dist = float(lap_dist[min(s, len(lap_dist) - 1)])
                events.append(KerbEvent(
                    lap_dist_m=round(dist, 1),
                    severity=round(severity, 2),
                    side="both",
                ))

    # Merge events that are very close together (< 30m)
    if len(events) > 1:
        merged = [events[0]]
        for evt in events[1:]:
            if evt.lap_dist_m - merged[-1].lap_dist_m < 30:
                if evt.severity > merged[-1].severity:
                    merged[-1] = evt
            else:
                merged.append(evt)
        events = merged

    return events


def _build_elevation_profile(
    alt: np.ndarray, lap_dist: np.ndarray, track_length_m: float,
) -> list[dict]:
    """Sample elevation at regular intervals around the track."""
    samples = []
    step = 50  # Every 50m
    for d in range(0, int(track_length_m), step):
        mask = (lap_dist >= d) & (lap_dist < d + step)
        if np.sum(mask) > 0:
            samples.append({
                "dist_m": d,
                "alt_m": round(float(np.mean(alt[mask])), 2),
            })
    return samples


def main():
    parser = argparse.ArgumentParser(description="Build TrackProfile from IBT")
    parser.add_argument("ibt_file", help="Path to .ibt or .zip file")
    parser.add_argument("--output", "-o", help="Output JSON path (default: auto)")
    args = parser.parse_args()

    ibt_path = Path(args.ibt_file)
    print(f"Parsing: {ibt_path.name}")

    profile = build_profile(ibt_path)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path("data/tracks") / f"{profile.track_name.lower().replace(' ', '_')}_{profile.track_config.lower()}.json"

    profile.save(out_path)
    print(f"\nSaved: {out_path}")
    print()
    print(profile.summary())


if __name__ == "__main__":
    main()
