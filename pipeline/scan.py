"""Full IBT scan — discover, ingest, calibrate, and optimize all sessions.

Scans a directory for IBT files, auto-detects car and track from each file's
header, then runs the complete pipeline: learner ingest, track accumulation,
auto-calibration, and physics-based setup optimization.

Usage:
    python -m pipeline.scan                          # scan default dirs
    python -m pipeline.scan --dir /path/to/ibts      # scan specific dir
    python -m pipeline.scan --dry-run                # preview without executing
    python -m pipeline.scan --filter porsche         # only Porsche IBTs
    python -m pipeline.scan --skip-produce           # ingest + calibrate only
    python -m pipeline.scan --force --sto-dir ./out  # force all steps, output here
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class IBTInfo:
    """Discovered IBT file with auto-detected metadata."""
    path: Path
    car_screen: str
    car_canonical: str
    car_display: str
    track_name: str
    track_config: str
    mtime: float

    @property
    def label(self) -> str:
        config = f" — {self.track_config}" if self.track_config else ""
        return f"{self.car_display} at {self.track_name}{config}"

    @property
    def group_key(self) -> tuple[str, str]:
        return (self.car_canonical, self.track_name)


@dataclass
class ScanResult:
    """Result of processing one IBT file."""
    ibt: IBTInfo
    ingested: bool = False
    ingest_error: str = ""
    calibrated: bool = False
    cal_unique_setups: int = 0
    cal_error: str = ""
    produced: bool = False
    produce_error: str = ""
    sto_path: str = ""
    skipped_reason: str = ""


def _discover_ibts(scan_dir: Path, car_filter: str | None = None) -> list[IBTInfo]:
    """Recursively find IBT files and extract car/track from headers."""
    from track_model.ibt_parser import IBTFile
    from car_model.registry import resolve_car

    ibt_paths = sorted(scan_dir.rglob("*.ibt"), key=lambda p: p.stat().st_mtime)
    results: list[IBTInfo] = []
    skipped = 0

    for path in ibt_paths:
        try:
            ibt = IBTFile(str(path))
            car = ibt.car_info()
            track = ibt.track_info()

            car_screen = car.get("car", "Unknown")
            identity = resolve_car(car_screen)
            if identity is None:
                skipped += 1
                logger.debug("Unknown car %r in %s — skipping", car_screen, path.name)
                continue

            if car_filter and car_filter.lower() not in identity.canonical.lower():
                continue

            results.append(IBTInfo(
                path=path,
                car_screen=car_screen,
                car_canonical=identity.canonical,
                car_display=identity.display_name,
                track_name=track.get("track_name", "Unknown"),
                track_config=track.get("track_config", ""),
                mtime=path.stat().st_mtime,
            ))
        except Exception as e:
            logger.debug("Failed to parse %s: %s", path.name, e)
            skipped += 1

    if skipped:
        logger.info("Skipped %d IBT files (unknown car or parse error)", skipped)

    return results


def _ingest_one(info: IBTInfo, verbose: bool = False) -> tuple[bool, str]:
    """Run learner ingest for one IBT. Returns (success, error_msg)."""
    try:
        from learner.ingest import ingest_ibt

        ingest_ibt(
            car_name=info.car_canonical,
            ibt_path=str(info.path),
            verbose=verbose,
        )
        return True, ""
    except Exception as e:
        return False, str(e)


def _calibrate_one(info: IBTInfo, verbose: bool = False) -> tuple[bool, int, str]:
    """Run auto-calibration for one IBT. Returns (success, n_unique_setups, error_msg)."""
    try:
        from learner.ingest import _update_auto_calibration

        session_id = info.path.stem
        result = _update_auto_calibration(
            car_name=info.car_canonical,
            ibt_path=str(info.path),
            session_id=session_id,
            assessment="scan",
            lap_time_s=0.0,
            verbose=verbose,
        )
        n_unique = result.get("cal_unique_setups", 0)
        return result.get("cal_point_added", False), n_unique, ""
    except Exception as e:
        return False, 0, str(e)


def _produce_one(
    info: IBTInfo,
    sto_dir: Path,
    force: bool = False,
    verbose: bool = False,
) -> tuple[bool, str, str]:
    """Run full pipeline for one IBT via subprocess. Returns (success, sto_path, error_msg).

    Uses subprocess for isolation — each produce run gets a clean state.
    """
    sto_dir.mkdir(parents=True, exist_ok=True)
    track_slug = info.track_name.lower().replace(" ", "_")[:30]
    sto_name = f"{info.car_canonical}_{track_slug}_{info.path.stem}.sto"
    sto_path = sto_dir / sto_name

    cmd = [
        sys.executable, "-m", "pipeline.produce",
        "--car", info.car_canonical,
        "--ibt", str(info.path),
        "--sto", str(sto_path),
        "--json", str(sto_path.with_suffix(".json")),
    ]
    if force:
        cmd.append("--force")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode == 0:
            return True, str(sto_path), ""
        else:
            # Extract last meaningful error line
            err_lines = [l for l in result.stderr.strip().split("\n") if l.strip()]
            err_msg = err_lines[-1] if err_lines else "unknown error"
            return False, "", err_msg
    except subprocess.TimeoutExpired:
        return False, "", "timeout (300s)"
    except Exception as e:
        return False, "", str(e)


def _print_header(scan_dir: Path, ibts: list[IBTInfo]) -> None:
    cars = len({i.car_canonical for i in ibts})
    tracks = len({i.track_name for i in ibts})
    w = 55
    print(f"\n{'=' * w}")
    print(f"  iOPTIMAL FULL SCAN")
    print(f"  Directory: {scan_dir}")
    print(f"  Found: {len(ibts)} IBT file{'s' if len(ibts) != 1 else ''} "
          f"({cars} car{'s' if cars != 1 else ''}, "
          f"{tracks} track{'s' if tracks != 1 else ''})")
    print(f"{'=' * w}\n")


def _print_summary(results: list[ScanResult]) -> None:
    n_total = len(results)
    n_ingested = sum(1 for r in results if r.ingested)
    n_calibrated = sum(1 for r in results if r.calibrated)
    n_produced = sum(1 for r in results if r.produced)
    n_skipped = sum(1 for r in results if r.skipped_reason)
    n_failed = sum(1 for r in results if r.ingest_error or r.produce_error)

    w = 55
    print(f"\n{'=' * w}")
    print(f"  SUMMARY")
    print(f"  Processed: {n_total} IBTs")
    print(f"  Ingested: {n_ingested}  Calibrated: {n_calibrated}  Produced: {n_produced}")
    if n_skipped:
        print(f"  Skipped: {n_skipped}")
    if n_failed:
        print(f"  Errors: {n_failed}")
    print(f"{'=' * w}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan IBT files and run full iOPTIMAL pipeline on each.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir", type=str, default=None,
        help="Directory to scan for IBT files (default: ibtfiles/ or iRacing telemetry dir)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be processed without executing",
    )
    parser.add_argument(
        "--filter", type=str, default=None,
        help="Only process IBTs matching this car name (e.g., porsche, bmw)",
    )
    parser.add_argument(
        "--produce", action="store_true",
        help="Also run the full solver/optimizer on each IBT (off by default)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Pass --force to produce (bypass calibration gate, requires --produce)",
    )
    parser.add_argument(
        "--sto-dir", type=str, default="output/setups",
        help="Directory for .sto output files (default: output/setups/, requires --produce)",
    )
    parser.add_argument(
        "--refit", action="store_true",
        help="Force refit all calibration models per-track after processing",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed per-IBT output",
    )
    args = parser.parse_args()

    # Resolve scan directory
    if args.dir:
        scan_dir = Path(args.dir)
    elif Path("ibtfiles").is_dir():
        scan_dir = Path("ibtfiles")
    else:
        from watcher.monitor import default_telemetry_dir
        scan_dir = default_telemetry_dir()

    if not scan_dir.exists():
        print(f"ERROR: Directory not found: {scan_dir}")
        sys.exit(1)

    # Phase 1: Discovery
    ibts = _discover_ibts(scan_dir, car_filter=args.filter)
    if not ibts:
        print(f"No IBT files found in {scan_dir}")
        if args.filter:
            print(f"  (filter: {args.filter})")
        sys.exit(0)

    _print_header(scan_dir, ibts)

    # Group by (car, track) for display
    groups: dict[tuple[str, str], list[IBTInfo]] = defaultdict(list)
    for info in ibts:
        groups[info.group_key].append(info)

    if args.dry_run:
        for (car, track), group in groups.items():
            display = group[0].car_display
            config = group[0].track_config
            config_str = f" — {config}" if config else ""
            print(f"{display} at {track}{config_str} ({len(group)} sessions)")
            for i, info in enumerate(group, 1):
                print(f"  [{i}/{len(group)}] {info.path.name}")
        print(f"\nDry run complete. Use without --dry-run to process.")
        return

    # Phase 2 + 3: Process each IBT
    results: list[ScanResult] = []
    sto_dir = Path(args.sto_dir)
    total = len(ibts)
    idx = 0

    for (car, track), group in groups.items():
        display = group[0].car_display
        config = group[0].track_config
        config_str = f" — {config}" if config else ""
        print(f"\n{display} at {track}{config_str} ({len(group)} sessions)")
        print(f"{'-' * 50}")

        for i, info in enumerate(group, 1):
            idx += 1
            r = ScanResult(ibt=info)
            t0 = time.time()

            print(f"  [{i}/{len(group)}] {info.path.name}  ({idx}/{total} overall)")

            # Ingest
            try:
                ok, err = _ingest_one(info, verbose=args.verbose)
                r.ingested = ok
                r.ingest_error = err
            except Exception as e:
                r.ingest_error = str(e)

            # Calibrate
            try:
                ok, n_unique, err = _calibrate_one(info, verbose=args.verbose)
                r.calibrated = ok
                r.cal_unique_setups = n_unique
                r.cal_error = err
            except Exception as e:
                r.cal_error = str(e)

            # Produce (only if explicitly requested)
            if args.produce:
                try:
                    ok, sto, err = _produce_one(info, sto_dir, force=args.force, verbose=args.verbose)
                    r.produced = ok
                    r.sto_path = sto
                    r.produce_error = err
                except Exception as e:
                    r.produce_error = str(e)

            elapsed = time.time() - t0

            # Status line
            parts = []
            parts.append(f"Ingest: {'OK' if r.ingested else 'FAIL'}")
            if r.calibrated:
                parts.append(f"Cal: OK ({r.cal_unique_setups} setups)")
            elif r.cal_error:
                parts.append(f"Cal: FAIL")
            else:
                parts.append("Cal: skip")
            if args.produce:
                if r.produced:
                    parts.append(f"Produce: OK")
                elif r.produce_error:
                    parts.append(f"Produce: FAIL")
            print(f"        {' | '.join(parts)}  [{elapsed:.1f}s]")

            if r.produced and r.sto_path:
                print(f"        -> {r.sto_path}")
            if r.ingest_error and not args.verbose:
                print(f"        Ingest error: {r.ingest_error[:80]}")
            if r.produce_error and not args.verbose:
                print(f"        Produce error: {r.produce_error[:80]}")

            results.append(r)

    # ── Post-scan: per-track calibration refit + consensus rebuild ──
    if args.refit:
        _refit_per_track(ibts)
        _rebuild_consensus(ibts)

    _print_summary(results)


def _refit_per_track(ibts: list[IBTInfo]) -> None:
    """Refit calibration models per (car, track) from all accumulated data."""
    # Suppress noisy overfit warnings during refit (the fallback models are clean)
    logging.getLogger("car_model.auto_calibrate").setLevel(logging.ERROR)

    from car_model.auto_calibrate import (
        load_calibration_points,
        fit_models_from_points,
        save_calibrated_models,
        _setup_key,
    )
    from car_model.registry import track_key as _track_key

    # Collect unique (car, track) pairs from discovered IBTs
    car_tracks: dict[str, set[str]] = defaultdict(set)
    for info in ibts:
        tk = _track_key(info.track_name)
        if tk:
            car_tracks[info.car_canonical].add(tk)

    print(f"\n{'=' * 55}")
    print(f"  CALIBRATION REFIT (per-track)")
    print(f"{'=' * 55}")

    for car_name, tracks in sorted(car_tracks.items()):
        try:
            points = load_calibration_points(car_name)
        except Exception as e:
            print(f"\n{car_name}: failed to load calibration points ({e})")
            continue
        if not points:
            print(f"\n{car_name}: no calibration points")
            continue

        # Group by track
        track_groups: dict[str, list] = {}
        for pt in points:
            tk = _track_key(pt.track) if pt.track else ""
            if tk:
                track_groups.setdefault(tk, []).append(pt)

        print(f"\n{car_name}: {len(points)} total points, {len(track_groups)} tracks")

        for tk in sorted(track_groups):
            track_pts = track_groups[tk]
            tk_unique = len({_setup_key(pt) for pt in track_pts})
            if tk_unique < 5:
                print(f"  {tk}: {len(track_pts)} points, {tk_unique} unique setups — need 5+, skipping")
                continue

            models = fit_models_from_points(car_name, track_pts)
            models.track = tk

            # Preserve zeta/torsion from existing
            from car_model.auto_calibrate import load_calibrated_models
            existing = load_calibrated_models(car_name, track=tk)
            if not existing:
                existing = load_calibrated_models(car_name)
            if existing:
                if existing.front_ls_zeta is not None and models.front_ls_zeta is None:
                    models.front_ls_zeta = existing.front_ls_zeta
                    models.rear_ls_zeta = existing.rear_ls_zeta
                    models.front_hs_zeta = existing.front_hs_zeta
                    models.rear_hs_zeta = existing.rear_hs_zeta
                    models.zeta_n_sessions = existing.zeta_n_sessions
                if existing.front_torsion_lookup and not models.front_torsion_lookup:
                    models.front_torsion_lookup = existing.front_torsion_lookup
                if existing.rear_torsion_lookup and not models.rear_torsion_lookup:
                    models.rear_torsion_lookup = existing.rear_torsion_lookup

            save_calibrated_models(car_name, models, track=tk)

            # Report key model health
            # TODO(W5.1+): GT3 cars have no `heave_spring_defl_static` model; the
            # health row drops silently for GT3 instead of reporting a coil-only
            # equivalent (e.g. `front_corner_spring_defl_static`). Cosmetic per
            # docs/audits/gt3_phase2/pipeline.md F23 / DEGRADED 23 — leaves the
            # user without a "model present but not yet fit" signal on GT3.
            health = []
            for name in ["front_ride_height", "rear_ride_height", "heave_spring_defl_static"]:
                m = getattr(models, name, None)
                if m and m.is_calibrated:
                    ratio = m.loo_rmse / max(m.rmse, 0.001)
                    health.append(f"{name}: R2={m.r_squared:.3f} LOO/train={ratio:.1f}x")
                elif m:
                    health.append(f"{name}: uncalibrated")
            print(f"  {tk}: {len(track_pts)} pts, {tk_unique} unique -> refit OK")
            for h in health:
                print(f"    {h}")


def _rebuild_consensus(ibts: list[IBTInfo]) -> None:
    """Verify track stores and report consensus health.

    Does NOT write to shared track profile files — those are car-specific
    and the produce pipeline loads from the per-car store directly.
    Writing to a shared file would overwrite one car's data with another's
    (e.g., Porsche shock velocities overwriting Ferrari's at Algarve).
    """
    from track_model.track_store import TrackProfileStore
    from car_model.registry import track_slug as _track_slug

    # Collect unique (track_slug, car_slug) pairs
    seen: set[tuple[str, str]] = set()
    for info in ibts:
        ts = _track_slug(info.track_name, info.track_config or "default")
        cs = info.car_canonical.lower().replace(" ", "_")
        seen.add((ts, cs))

    print(f"\n{'=' * 55}")
    print(f"  TRACK STORE STATUS")
    print(f"{'=' * 55}")

    for ts, cs in sorted(seen):
        store = TrackProfileStore(ts, cs)
        if store.n_sessions == 0:
            print(f"  {ts} ({cs}): empty")
            continue
        consensus = store.consensus()
        p99 = consensus.shock_vel_p99_front_mps
        print(f"  {ts} ({cs}): {store.n_sessions} sessions, p99={p99:.4f} m/s")


if __name__ == "__main__":
    main()
