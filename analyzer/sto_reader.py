"""CLI for inspecting binary STO files."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from analyzer.setup_reader import CurrentSetup
from analyzer.sto_adapters import build_diff_rows
from analyzer.sto_binary import decode_sto


def _iter_sto_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(candidate for candidate in path.glob("*.sto") if candidate.is_file())


def _decode_payload(path: Path, output_format: str, car: str | None) -> object:
    decoded = decode_sto(path)
    if output_format == "decoded":
        return decoded.to_dict()
    if output_format == "canonical":
        return dataclasses.asdict(CurrentSetup.from_sto(path))
    return [row.to_dict() for row in build_diff_rows(decoded, car=car)]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer.sto_reader",
        description="Inspect version-3 binary STO files.",
    )
    parser.add_argument("--path", required=True, help="Path to a .sto file or a directory of .sto files.")
    parser.add_argument("--car", default=None, help="Optional car override (for example: acura).")
    parser.add_argument(
        "--format",
        choices=("canonical", "rows", "decoded"),
        default="canonical",
        help="Output format.",
    )
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        raise SystemExit(f"Path does not exist: {target}")

    paths = _iter_sto_paths(target)
    if not paths:
        raise SystemExit(f"No .sto files found under: {target}")

    if len(paths) == 1:
        payload = _decode_payload(paths[0], args.format, args.car)
    else:
        payload = []
        for path in paths:
            try:
                payload.append({"path": str(path), "ok": True, "data": _decode_payload(path, args.format, args.car)})
            except Exception as exc:  # pragma: no cover - surfaced in CLI JSON
                payload.append({"path": str(path), "ok": False, "error": str(exc)})

    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
