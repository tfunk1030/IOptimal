#!/usr/bin/env python3
"""weekly_track_prep.py — iOptimal pre-week setup briefing.

Runs every Sunday evening. Checks the season schedule to determine if next
week has an active race. If so:
  1. Reports how many IBT sessions exist for that car/track
  2. Runs solver if IBT data available, else sends physics-baseline warning
  3. Sends a Telegram message with the setup card + week briefing

Usage:
    python3 scripts/weekly_track_prep.py [--car ferrari] [--force-week 4]
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SOLVER_DIR = Path(__file__).parent.parent  # gtp-setup-builder/
SCHEDULE_FILE = SOLVER_DIR / "data" / "season_2026_s2.json"
IBT_DIR = SOLVER_DIR / "ibtfiles"
OBS_DIR = SOLVER_DIR / "data" / "learnings" / "observations"
LOG_FILE = Path(__file__).parent.parent.parent / "auto_solver.log"


def log(msg: str):
    ts = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    line = f"[{ts}] [weekly_prep] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_schedule() -> dict:
    with open(SCHEDULE_FILE) as f:
        return json.load(f)


def get_next_week(schedule: dict, force_week: int | None = None) -> dict | None:
    """Return the schedule entry for next week (or forced week)."""
    today = date.today()
    for entry in schedule["weeks"]:
        start = date.fromisoformat(entry["start_date"])
        end = date.fromisoformat(entry["end_date"])
        wk = entry["week"]

        if force_week and wk == force_week:
            return entry

        # Next week: starts within the next 8 days from today
        days_until = (start - today).days
        if 0 <= days_until <= 8 and not force_week:
            return entry

    return None


def count_sessions(car: str, track_key: str) -> int:
    """Count observation files for this car/track (excluding lap files)."""
    pattern = str(OBS_DIR / f"{car}_{track_key}*.json")
    all_files = glob.glob(pattern)
    return len([f for f in all_files if "__lap_" not in f])


def find_ibt_files(car: str, track_key: str) -> list[str]:
    """Find IBT files for this car/track."""
    # Try car-specific pattern first
    files = glob.glob(str(IBT_DIR / f"{car}*.ibt"))
    # Filter by track keyword (track_key contains underscores, IBT filenames vary)
    track_words = track_key.replace("-", "_").lower().split("_")
    # Use the most distinctive word (skip generic ones)
    stopwords = {"at", "de", "international", "circuit", "raceway", "grand", "gp", "full", "course"}
    keywords = [w for w in track_words if len(w) > 3 and w not in stopwords]
    
    track_files = []
    for f in sorted(files):
        fname = os.path.basename(f).lower()
        if any(kw in fname for kw in keywords):
            track_files.append(f)
    
    # Return last 6 if available
    return sorted(track_files)[-6:]


def run_solver(car: str, ibt_files: list[str], mode: str = "safe") -> tuple[bool, str]:
    """Run the solver and return (success, output)."""
    cmd = [
        sys.executable, "-m", "pipeline.produce",
        "--car", car,
        "--mode", mode,
        "--delta-card",
        "--search-mode", "exhaustive",
        "--top-n", "1",
    ]
    if ibt_files:
        cmd += ["--ibt"] + ibt_files

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(SOLVER_DIR),
            timeout=300,
        )
        if result.returncode == 0:
            return True, result.stdout
        else:
            return False, result.stderr[-2000:]
    except subprocess.TimeoutExpired:
        return False, "Solver timed out after 300s"
    except Exception as e:
        return False, str(e)


def format_briefing(week_entry: dict, car: str, session_count: int,
                    ibt_count: int, solver_ok: bool, solver_output: str,
                    mode: str) -> str:
    wk = week_entry["week"]
    track = week_entry["track"]
    config = week_entry["config"]
    rain = week_entry.get("rain_pct")
    notes = week_entry.get("notes", "")
    start = week_entry["start_date"]

    car_emoji = {
        "ferrari": "🔴", "bmw": "🔵", "cadillac": "🟡",
        "porsche": "⚪", "acura": "🟢"
    }.get(car, "🏎️")

    car_label = {
        "ferrari": "Ferrari 499P", "bmw": "BMW M Hybrid V8",
        "cadillac": "Cadillac V-Series.R", "porsche": "Porsche 963 GTP",
        "acura": "Acura ARX-06"
    }.get(car, car.upper())

    lines = [
        f"{'═' * 52}",
        f"🏁  WEEK {wk} PREP  |  {car_emoji} {car_label}",
        f"    {track} — {config}",
        f"    Week starts: {start}",
    ]

    if rain:
        lines.append(f"    🌧️  Rain probability: {rain}%")

    lines += ["═" * 52, ""]

    # Session data status
    if session_count >= 10:
        lines.append(f"✅ {session_count} sessions in DB — HIGH confidence recommendations")
    elif session_count >= 3:
        lines.append(f"ℹ️  {session_count} sessions in DB — MED confidence (k-NN active)")
    elif session_count > 0:
        lines.append(f"⚠️  {session_count} sessions in DB — LOW data, physics-dominant")
    else:
        lines.append(f"❌ No IBT sessions yet for this track")
        lines.append(f"   First session will auto-generate a card via auto_solver.sh")
        lines.append(f"   Expect physics-only (EST tier) until 3+ sessions")

    if ibt_count > 0:
        lines.append(f"   {ibt_count} IBT file(s) found — solver ran {mode.upper()} mode")
    lines.append("")

    if solver_ok and solver_output.strip():
        lines.append("─" * 52)
        lines.append(solver_output.strip())
    elif not solver_ok and ibt_count > 0:
        lines.append(f"⚠️  Solver error:")
        lines.append(solver_output[:500])
    elif ibt_count == 0:
        lines.append("─" * 52)
        lines.append("💡 Track briefing (physics estimate):")
        lines.append(f"   {notes}")
        lines.append("")
        lines.append("   Get 3+ laps in during first session →")
        lines.append("   auto_solver.sh will generate your card automatically.")

    return "\n".join(lines)


def send_telegram(message: str, to: str = "7112846277"):
    """Send message via OpenClaw."""
    try:
        import subprocess
        # Use openclaw message tool
        result = subprocess.run(
            ["openclaw", "message", "send", "--to", to, "--message", message],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log("Telegram sent OK")
        else:
            log(f"Telegram send failed: {result.stderr[:200]}")
    except Exception as e:
        log(f"Telegram send error: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--car", default="ferrari")
    parser.add_argument("--force-week", type=int, default=None)
    parser.add_argument("--mode", choices=["safe", "aggressive"], default="safe")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    schedule = load_schedule()
    week_entry = get_next_week(schedule, args.force_week)

    if week_entry is None:
        log("No upcoming race week found in next 8 days. HEARTBEAT_OK")
        print("HEARTBEAT_OK — no prep needed this week")
        return

    if week_entry["skip"]:
        log(f"Week {week_entry['week']} ({week_entry['track']}) — SKIP (Taylor out of town)")
        print(f"HEARTBEAT_OK — Week {week_entry['week']} is a skip week")
        return

    wk = week_entry["week"]
    track_key = week_entry["track_key"]
    log(f"Preparing Week {wk}: {week_entry['track']}")

    # Count existing sessions
    session_count = count_sessions(args.car, track_key)
    log(f"  Sessions in DB: {session_count}")

    # Find IBT files
    ibt_files = find_ibt_files(args.car, track_key)
    log(f"  IBT files found: {len(ibt_files)}")

    # Run solver if IBT data available
    solver_ok = False
    solver_output = ""
    if ibt_files:
        log(f"  Running solver ({args.mode} mode)...")
        solver_ok, solver_output = run_solver(args.car, ibt_files, args.mode)
        log(f"  Solver: {'OK' if solver_ok else 'FAILED'}")
    else:
        log("  No IBT files for this track — skipping solver, sending briefing only")

    # Format and send
    msg = format_briefing(
        week_entry, args.car, session_count,
        len(ibt_files), solver_ok, solver_output, args.mode
    )

    if args.dry_run:
        print(msg)
    else:
        send_telegram(msg)
        print(f"Week {wk} prep sent for {week_entry['track']}")


if __name__ == "__main__":
    main()
