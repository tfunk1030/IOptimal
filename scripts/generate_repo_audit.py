"""Generate a repo audit summary and file inventory for the current workspace."""

from __future__ import annotations

import ast
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_MD = ROOT / "docs" / "repo_audit.md"
OUTPUT_JSON = ROOT / "docs" / "repo_inventory.json"
VALIDATION_JSON = ROOT / "validation" / "objective_validation.json"

DETAIL_EXTENSIONS = {
    ".py",
    ".md",
    ".json",
    ".html",
    ".css",
    ".js",
    ".toml",
    ".sto",
}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", "node_modules"}
FIRST_PARTY_PACKAGES = {
    "aero_model",
    "analyzer",
    "car_model",
    "comparison",
    "learner",
    "output",
    "pipeline",
    "research",
    "scripts",
    "solver",
    "tests",
    "track_model",
    "validation",
    "validator",
    "webapp",
}
VALUE_CLASS_LABELS = {
    "source_of_truth": "Source of truth",
    "calibration_evidence": "Calibration evidence",
    "generated_artifact": "Generated artifact",
    "disposable_scratch_history": "Disposable scratch/history",
}
DIRECTORY_ROLE_HINTS = {
    "(root)": "Top-level utilities, metadata, and ad-hoc helper files.",
    "aero_model": "Aero surfaces, drag/downforce interpolation, and platform response models.",
    "analyzer": "Telemetry extraction, diagnosis, session context, and driver/style inference.",
    "car_model": "Car definitions, garage ranges, setup registry, and OEM/car-specific constraints.",
    "comparison": "Session comparison, scoring, and synthesized recommendation tooling.",
    "learner": "Observation storage, empirical corrections, and prior-session knowledge.",
    "output": "Report rendering, garage correlation, and .sto export.",
    "pipeline": "Top-level single-session and multi-session orchestration entrypoints.",
    "research": "Supporting engineering notes and manual calibration writeups.",
    "scripts": "Repo maintenance, diagnostics, and generated-document utilities.",
    "solver": "Setup solve chain, objective function, legality checks, and search strategies.",
    "tests": "Regression coverage and fixture-backed validation.",
    "track_model": "IBT parsing, track-profile building, and track metadata.",
    "validation": "Calibration/evidence reporting and schema normalization.",
    "validator": "Cross-checks for solver behavior and garage consistency.",
    "webapp": "FastAPI UI, service adapters, templates, and persistence.",
    "data": "Track profiles, observations, empirical models, and sample artifacts.",
    "docs": "User-facing repo documentation and research snapshots.",
    "outputs": "Generated run outputs and saved reports.",
    "tmp": "Scratch artifacts and temporary work products.",
    "ibtfiles": "Local telemetry/session files kept outside the product source tree.",
}
ANCHOR_PATHS = {
    "pipeline/produce.py",
    "solver/objective.py",
    "validation/run_validation.py",
}
KNOWN_OUTPUTS = {
    "pipeline/produce.py": ["report text", ".sto file", "single-session JSON payload"],
    "pipeline/reason.py": ["report text", ".sto file", "multi-session JSON payload", "setup JSON export"],
    "pipeline/preset_compare.py": ["preset comparison reports", "scenario-specific solver outputs"],
    "solver/solve.py": ["track-only report", ".sto file", "saved JSON summary"],
    "solver/solve_chain.py": ["SolveChainResult objects", "materialized candidate outputs"],
    "solver/legal_search.py": ["LegalSearchResult objects", "accepted candidate selection"],
    "validation/run_validation.py": ["validation/objective_validation.md", "validation/objective_validation.json"],
    "webapp/app.py": ["HTTP routes", "run submissions"],
    "webapp/services.py": ["web summary payloads", "report/json/sto artifacts per run"],
    "webapp/storage.py": ["SQLite-backed run metadata"],
}
KNOWN_ROLE_OVERRIDES = {
    "pipeline/produce.py": "Single-IBT telemetry-backed setup pipeline and CLI entrypoint.",
    "pipeline/reason.py": "Multi-IBT reasoning pipeline that aggregates sessions before solving.",
    "pipeline/preset_compare.py": "Scenario/preset comparison runner that aligns quali, sprint, and race flows.",
    "solver/objective.py": "Canonical candidate scoring model and objective breakdown calculator.",
    "solver/legal_search.py": "Legal-manifold exploration and scenario-aware candidate acceptance.",
    "solver/solve_chain.py": "Pinned-seed base solve, override materialization, and legality/prediction finalization.",
    "solver/candidate_search.py": "Candidate family generation and canonical-parameter override conversion.",
    "solver/scenario_profiles.py": "Typed scenario objectives and telemetry sanity thresholds.",
    "solver/solve.py": "Standalone track-only solver entrypoint.",
    "validation/run_validation.py": "Reproducible objective evidence report for current observations.",
    "validation/observation_mapping.py": "Canonical setup and telemetry normalization shared by validation/calibration.",
    "validation/objective_calibration.py": "Objective calibration support using canonical observation mappings.",
    "webapp/app.py": "FastAPI route layer for run submission, results, and downloads.",
    "webapp/services.py": "Service adapters between web requests and solver/pipeline backends.",
    "webapp/storage.py": "SQLite persistence for run metadata, inputs, summaries, and artifacts.",
}
OFFICIAL_SOURCES = [
    {
        "title": "BMW M Hybrid V8 user manual",
        "url": "https://s100.iracing.com/wp-content/uploads/2023/10/BMW-M-Hybrid-V8.pdf",
        "note": "Baseline setup workflow, aero calculator usage, hybrid modes, brake bias, TC, gear stack, and diff behavior.",
    },
    {
        "title": "2025 Season 1 release notes",
        "url": "https://support.iracing.com/support/solutions/articles/31000174324-2025-season-1-release-notes-2024-12-09-03-",
        "note": "GTP aerodynamic-property refresh and standardized ride-height sensor reference at the skid/axle measurement points.",
    },
    {
        "title": "2025 Season 4 Patch 2 release notes",
        "url": "https://support.iracing.com/support/solutions/articles/31000177221-2025-season-4-patch-2-release-notes-2025-09-24-01-",
        "note": "Current GTP hybrid/fuel-economy equivalence update plus BMW TC label/control fixes.",
    },
    {
        "title": "2025 Season 3 Patch 4 release notes",
        "url": "https://support.iracing.com/support/solutions/articles/31000176931-2025-season-3-patch-4-release-notes-2025-07-25-02-",
        "note": "GTP low-fuel warning trigger/clear behavior update in the garage workflow.",
    },
    {
        "title": "Load custom setups onto your racecar",
        "url": "https://support.iracing.com/support/solutions/articles/31000133513-load-custom-setups-onto-your-racecar-",
        "note": "Official garage/setup loading and sharing behavior for .sto workflows.",
    },
    {
        "title": "Filepath for active iRacing cars",
        "url": "https://support.iracing.com/support/solutions/articles/31000172625-filepath-for-active-iracing-cars",
        "note": "Canonical active-car folder names, including BMW/Cadillac/Acura/Ferrari/Porsche GTP entries.",
    },
    {
        "title": "iRacing car setup guide",
        "url": "https://ir-core-sites.iracing.com/members/pdfs/iRacing_Car_Setup_Guide_20100910.pdf",
        "note": "General setup-adjustment discipline: baseline first, one change at a time, no magic setup assumptions.",
    },
]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def top_level(path: Path) -> str:
    parts = path.relative_to(ROOT).parts
    if not parts or len(parts) == 1:
        return "(root)"
    return parts[0]


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts)


def is_generated(path: Path) -> bool:
    rel_path = rel(path)
    if rel_path.startswith("outputs/"):
        return True
    if rel_path in {"validation/objective_validation.json", "validation/objective_validation.md"}:
        return True
    if len(path.relative_to(ROOT).parts) == 1 and path.suffix == ".json" and path.name not in {"package.json", "package-lock.json"}:
        return True
    if path.suffix == ".sto":
        return True
    return False


def is_disposable(path: Path) -> bool:
    rel_path = rel(path)
    return rel_path.startswith("tmp/") or rel_path.startswith("ibtfiles/")


def value_class_for(path: Path) -> str:
    rel_path = rel(path)
    if is_disposable(path):
        return "disposable_scratch_history"
    if is_generated(path):
        return "generated_artifact"
    if rel_path.startswith("data/tracks/"):
        return "calibration_evidence"
    if rel_path.startswith("data/learnings/"):
        return "calibration_evidence"
    if rel_path.startswith("research/"):
        return "calibration_evidence"
    if rel_path.startswith("docs/") and "research" in rel_path:
        return "calibration_evidence"
    return "source_of_truth"


def include_in_detailed_inventory(path: Path) -> bool:
    if is_excluded(path):
        return False
    if path.suffix not in DETAIL_EXTENSIONS:
        return False
    return True


def _module_name(path: Path) -> str | None:
    if path.suffix != ".py":
        return None
    rel_path = path.relative_to(ROOT).with_suffix("")
    if rel_path.name == "__init__":
        rel_path = rel_path.parent
    return ".".join(rel_path.parts)


def _py_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    imports: set[str] = set()
    current_module = _module_name(path)
    current_package = current_module.rsplit(".", 1)[0] if current_module and "." in current_module else current_module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".", 1)[0]
                if base in FIRST_PARTY_PACKAGES:
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split(".", 1)[0]
                if base in FIRST_PARTY_PACKAGES:
                    imports.add(node.module)
            elif node.level and current_package:
                imports.add(current_package)
    return sorted(imports)


def _data_connections(path: Path) -> list[str]:
    rel_path = rel(path)
    if rel_path.startswith("data/tracks/"):
        return ["track_model.profile", "track_model.build_profile", "validation.run_validation"]
    if rel_path.startswith("data/learnings/observations/"):
        return ["learner.knowledge_store", "validation.run_validation", "validation.objective_calibration"]
    if rel_path.startswith("data/learnings/models/") or rel_path.startswith("data/learnings/heave_calibration"):
        return ["solver.learned_corrections", "validation.run_validation"]
    return []


def direct_dependencies(path: Path) -> list[str]:
    if path.suffix == ".py":
        return _py_imports(path)
    return _data_connections(path)


def runtime_entrypoint(path: Path) -> str | None:
    rel_path = rel(path)
    if path.suffix == ".py":
        module_name = _module_name(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if "__main__" in text and module_name:
            return f"python -m {module_name}"
        if rel_path == "webapp/app.py":
            return "FastAPI ASGI app factory"
    if rel_path.endswith(".html") and rel_path.startswith("webapp/templates/"):
        return "Rendered by FastAPI routes"
    return None


def outputs_for(path: Path) -> list[str]:
    rel_path = rel(path)
    if rel_path in KNOWN_OUTPUTS:
        return KNOWN_OUTPUTS[rel_path]
    if rel_path.startswith("tests/"):
        return ["pytest pass/fail signal"]
    if rel_path.startswith("data/tracks/"):
        return ["track profile consumed by solver/analyzer"]
    if rel_path.startswith("data/learnings/observations/"):
        return ["telemetry/setup observation evidence"]
    if rel_path.startswith("data/learnings/models/"):
        return ["empirical correction model"]
    if rel_path.startswith("research/") or rel_path.startswith("docs/"):
        return ["human-readable documentation"]
    if rel_path.startswith("webapp/templates/"):
        return ["HTML rendered to browser"]
    return []


def role_for(path: Path) -> str:
    rel_path = rel(path)
    if rel_path in KNOWN_ROLE_OVERRIDES:
        return KNOWN_ROLE_OVERRIDES[rel_path]
    stem = path.stem.replace("_", " ")
    tl = top_level(path)
    if rel_path.startswith("tests/"):
        return f"Regression/spec coverage for {stem}."
    if rel_path.startswith("data/learnings/observations/"):
        return "Observed setup/telemetry evidence sample collected from repo-local sessions."
    if rel_path.startswith("data/learnings/models/"):
        return "Persisted empirical correction/calibration model."
    if rel_path.startswith("data/tracks/"):
        return "Track profile and measurement snapshot used by solving/validation."
    if rel_path.startswith("webapp/templates/"):
        return f"Web UI template for {stem}."
    if rel_path.startswith("webapp/static/"):
        return f"Web UI static asset for {stem}."
    if rel_path.startswith("docs/") or rel_path.startswith("research/"):
        return "Reference documentation or engineering notes."
    hint = DIRECTORY_ROLE_HINTS.get(tl, "Repo file")
    return f"{hint} File focus: {stem}."


def failure_risk_for(path: Path) -> str:
    rel_path = rel(path)
    tl = top_level(path)
    if rel_path in ANCHOR_PATHS:
        return "high"
    if tl in {"pipeline", "solver", "webapp", "analyzer", "validation"} and path.suffix in {".py", ".html"}:
        return "high"
    if tl in {"car_model", "track_model", "learner", "comparison", "output", "validator", "data"}:
        return "medium"
    if tl in {"tests", "docs", "research"}:
        return "low"
    if is_generated(path) or is_disposable(path):
        return "low"
    return "medium"


def line_count(path: Path) -> int | None:
    if path.suffix not in {".py", ".md", ".html", ".css", ".js", ".json", ".toml"}:
        return None
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return None


def inventory_record(path: Path) -> dict[str, Any]:
    rel_path = rel(path)
    return {
        "path": rel_path,
        "category": top_level(path),
        "value_class": value_class_for(path),
        "detailed_inventory": not (is_generated(path) or is_disposable(path)),
        "role": role_for(path),
        "direct_dependencies": direct_dependencies(path),
        "runtime_entrypoint": runtime_entrypoint(path),
        "outputs": outputs_for(path),
        "failure_risk": failure_risk_for(path),
        "anchor_file": rel_path in ANCHOR_PATHS,
        "lines": line_count(path),
    }


def gather_inventory() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if is_excluded(path):
            continue
        if not include_in_detailed_inventory(path):
            continue
        records.append(inventory_record(path))
    return records


def summarize_exclusions() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "node_modules": {"files": 0},
        "generated_artifacts": {"files": 0},
        "disposable_scratch_history": {"files": 0},
    }
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part == "node_modules" for part in path.relative_to(ROOT).parts):
            summary["node_modules"]["files"] += 1
            continue
        if is_generated(path):
            summary["generated_artifacts"]["files"] += 1
            continue
        if is_disposable(path):
            summary["disposable_scratch_history"]["files"] += 1
    return summary


def load_validation_summary() -> dict[str, Any] | None:
    if not VALIDATION_JSON.exists():
        return None
    try:
        return json.loads(VALIDATION_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render_markdown(records: list[dict[str, Any]], exclusions: dict[str, Any], validation: dict[str, Any] | None) -> str:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    value_counts = Counter(record["value_class"] for record in records)
    high_risk = [record for record in records if record["failure_risk"] == "high"]
    for record in records:
        by_category[record["category"]].append(record)

    lines: list[str] = []
    lines.append("# Repo Audit")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Workflow Map")
    lines.append("")
    lines.append("`IBT -> track/analyzer -> diagnosis/driver/style -> solve_chain/legality -> report/.sto -> webapp`")
    lines.append("")
    lines.append("## Anchor Files")
    lines.append("")
    lines.append("- `pipeline/produce.py`: single-session orchestration and report export.")
    lines.append("- `solver/objective.py`: candidate ranking, breakdown weighting, and scenario-aware scoring.")
    lines.append("- `validation/run_validation.py`: reproducible BMW/Sebring evidence report and support tiers.")
    lines.append("")
    if validation is not None:
        bmw = validation.get("bmw_sebring", {})
        corr = bmw.get("score_correlation", {})
        lines.append("## Current BMW/Sebring Evidence")
        lines.append("")
        lines.append(f"- Samples: `{bmw.get('samples', 'n/a')}`")
        lines.append(f"- Non-vetoed samples: `{bmw.get('non_vetoed_samples', 'n/a')}`")
        lines.append(f"- Pearson (non-vetoed): `{corr.get('pearson_r_non_vetoed', 'n/a')}`")
        lines.append(f"- Spearman (non-vetoed): `{corr.get('spearman_r_non_vetoed', 'n/a')}`")
        lines.append("- Current objective status: `unverified` until score-vs-lap correlation improves materially.")
        lines.append("")
        lines.append("## Support Tiers")
        lines.append("")
        for row in validation.get("support_matrix", []):
            lines.append(
                f"- `{row['car']}` / `{row['track']}` / `{row['track_config']}`: `{row['confidence_tier']}` (`{row['samples']}` samples)"
            )
        lines.append("")
    lines.append("## Value Classes")
    lines.append("")
    for value_class, label in VALUE_CLASS_LABELS.items():
        lines.append(f"- {label}: `{value_counts.get(value_class, 0)}` inventoried files")
    lines.append("")
    lines.append("## Excluded / Summarized Separately")
    lines.append("")
    lines.append(f"- `node_modules`: `{exclusions['node_modules']['files']}` files (third-party, not first-party source)")
    lines.append(f"- Generated artifacts: `{exclusions['generated_artifacts']['files']}` files")
    lines.append(f"- Scratch/history: `{exclusions['disposable_scratch_history']['files']}` files")
    lines.append("")
    lines.append("## Official iRacing Sources Used")
    lines.append("")
    for source in OFFICIAL_SOURCES:
        lines.append(f"- [{source['title']}]({source['url']}): {source['note']}")
    lines.append("")
    lines.append("## Official Constraints Applied")
    lines.append("")
    lines.append("- Legal-manifold search stays inside setup-registry and garage-validated ranges; it does not emit out-of-range `.sto` candidates.")
    lines.append("- BMW M Hybrid V8 optimization treats aero ride height at speed as telemetry-derived, consistent with the official aero-calculator workflow.")
    lines.append("- BMW scenario profiles only bias objective weights and seed assumptions; they do not bypass session-limited or garage-limited controls.")
    lines.append("- Recent GTP release notes are treated as authority for ride-height reference, hybrid/fuel behavior, and low-fuel-control assumptions, and the repo now validates against those legal shapes instead of stale aliases.")
    lines.append("- The general iRacing setup guide still applies: baseline first, deliberate changes, and no claim of a universal magic setup.")
    lines.append("")
    lines.append("## Directory Summary")
    lines.append("")
    lines.append("| Directory | Files | Default Value Class | Notes |")
    lines.append("| --- | ---: | --- | --- |")
    for category in sorted(by_category):
        category_records = by_category[category]
        class_counts = Counter(record["value_class"] for record in category_records)
        dominant = class_counts.most_common(1)[0][0]
        lines.append(
            f"| `{category}` | {len(category_records)} | {VALUE_CLASS_LABELS[dominant]} | {DIRECTORY_ROLE_HINTS.get(category, 'Repo files')} |"
        )
    lines.append("")
    lines.append("## High-Risk Files")
    lines.append("")
    for record in sorted(high_risk, key=lambda item: item["path"])[:40]:
        deps = ", ".join(record["direct_dependencies"][:4]) if record["direct_dependencies"] else "None"
        outputs = ", ".join(record["outputs"][:3]) if record["outputs"] else "None"
        lines.append(f"- `{record['path']}`: {record['role']} Dependencies: {deps}. Outputs: {outputs}.")
    lines.append("")
    lines.append("## Full Inventory")
    lines.append("")
    lines.append("The exhaustive file-by-file inventory is written to `docs/repo_inventory.json`.")
    return "\n".join(lines) + "\n"


def main() -> None:
    records = gather_inventory()
    exclusions = summarize_exclusions()
    validation = load_validation_summary()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_map": [
            "IBT",
            "track/analyzer",
            "diagnosis/driver/style",
            "solve_chain/legality",
            "report/.sto",
            "webapp",
        ],
        "anchors": sorted(ANCHOR_PATHS),
        "official_sources": OFFICIAL_SOURCES,
        "exclusions": exclusions,
        "inventory": records,
        "validation_snapshot": validation,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(records, exclusions, validation), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")


if __name__ == "__main__":
    main()
