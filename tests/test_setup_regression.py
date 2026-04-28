"""Setup regression tests.

Captures current pipeline output as fixtures and verifies future runs produce
identical output. This is the safety net for the overhaul: any unintended
change to BMW or Porsche setup generation will fail these tests.

Regenerating fixtures (only when an intentional change is made):
    # GTP baselines
    python -m pipeline.produce --car bmw \\
        --ibt "data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt" \\
        --wing 17 --sto tests/fixtures/baselines/bmw_sebring_baseline.sto

    python -m pipeline.produce --car porsche \\
        --ibt "ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt" \\
        --fuel 58 --wing 17 --sto tests/fixtures/baselines/porsche_algarve_baseline.sto

    # GT3 baselines (W9.2; --force bypasses calibration gate — GT3 is intercept-only)
    python -m pipeline.produce --car bmw_m4_gt3 \\
        --ibt "data/gt3_ibts/bmwm4gt3_spielberg gp 2026-04-26 21-34-43.ibt" \\
        --force --sto tests/fixtures/baselines/bmw_m4_gt3_spielberg_baseline.sto

    python -m pipeline.produce --car aston_martin_vantage_gt3 \\
        --ibt "data/gt3_ibts/amvantageevogt3_spielberg gp 2026-04-26 21-25-55.ibt" \\
        --force --sto tests/fixtures/baselines/aston_vantage_gt3_spielberg_baseline.sto

    python -m pipeline.produce --car porsche_992_gt3r \\
        --ibt "data/gt3_ibts/porsche992rgt3_spielberg gp 2026-04-26 21-42-39.ibt" \\
        --force --sto tests/fixtures/baselines/porsche_992_gt3r_spielberg_baseline.sto
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "baselines"

# Keys extracted from .sto that matter for setup correctness.
# Excludes the generation timestamp and other cosmetic fields.
SETUP_KEY_PATTERN = re.compile(
    r'Id="(CarSetup_[^"]+)"\s+Value="([^"]+)"'
)


@dataclass(frozen=True)
class RegressionCase:
    """One parametrized regression scenario."""

    label: str
    car: str
    ibt: str
    wing: int | None
    fuel: int | None
    baseline: str
    force: bool = False  # GT3 cars need --force until calibration lands


# Parametrized cases. GTP entries pin against full-pipeline calibrated output;
# GT3 entries lock the W1-W8 intercept-only pipeline output (the .sto values
# are estimates per the ESTIMATE WARNINGS block, but the structure + per-car
# PARAM_IDS dispatch is what we're guarding against drift).
REGRESSION_CASES: list[RegressionCase] = [
    RegressionCase(
        label="BMW/Sebring",
        car="bmw",
        ibt="data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt",
        wing=17,
        fuel=None,
        baseline="bmw_sebring_baseline.sto",
    ),
    RegressionCase(
        label="Porsche/Algarve",
        car="porsche",
        ibt="ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt",
        wing=17,
        fuel=58,
        baseline="porsche_algarve_baseline.sto",
    ),
    RegressionCase(
        label="BMW M4 GT3/Spielberg",
        car="bmw_m4_gt3",
        ibt="data/gt3_ibts/bmwm4gt3_spielberg gp 2026-04-26 21-34-43.ibt",
        wing=None,
        fuel=None,
        baseline="bmw_m4_gt3_spielberg_baseline.sto",
        force=True,
    ),
    RegressionCase(
        label="Aston Vantage GT3/Spielberg",
        car="aston_martin_vantage_gt3",
        ibt="data/gt3_ibts/amvantageevogt3_spielberg gp 2026-04-26 21-25-55.ibt",
        wing=None,
        fuel=None,
        baseline="aston_vantage_gt3_spielberg_baseline.sto",
        force=True,
    ),
    RegressionCase(
        label="Porsche 992 GT3R/Spielberg",
        car="porsche_992_gt3r",
        ibt="data/gt3_ibts/porsche992rgt3_spielberg gp 2026-04-26 21-42-39.ibt",
        wing=None,
        fuel=None,
        baseline="porsche_992_gt3r_spielberg_baseline.sto",
        force=True,
    ),
]


def _extract_setup_values(sto_path: Path) -> dict[str, str]:
    """Parse a .sto XML file and return {parameter_id: value_string}."""
    text = sto_path.read_text(encoding="utf-8")
    return dict(SETUP_KEY_PATTERN.findall(text))


def _run_pipeline(
    car: str,
    ibt: str,
    wing: int | None,
    fuel: int | None,
    out_sto: Path,
    *,
    force: bool = False,
) -> None:
    """Run pipeline.produce with given args and write output to out_sto."""
    cmd = [
        sys.executable, "-m", "pipeline.produce",
        "--car", car,
        "--ibt", ibt,
        "--sto", str(out_sto),
    ]
    if wing is not None:
        cmd += ["--wing", str(wing)]
    if fuel is not None:
        cmd += ["--fuel", str(fuel)]
    if force:
        cmd += ["--force"]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert result.returncode == 0, (
        f"pipeline.produce failed for {car}:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def _assert_setup_matches(baseline: Path, current: Path, label: str) -> None:
    """Compare two .sto files by setup parameter values (ignoring metadata)."""
    baseline_values = _extract_setup_values(baseline)
    current_values = _extract_setup_values(current)

    # Every baseline key must exist in current output with the same value.
    missing_keys = set(baseline_values.keys()) - set(current_values.keys())
    added_keys = set(current_values.keys()) - set(baseline_values.keys())
    changed = {
        k: (baseline_values[k], current_values[k])
        for k in baseline_values.keys() & current_values.keys()
        if baseline_values[k] != current_values[k]
    }

    problems: list[str] = []
    if missing_keys:
        problems.append(f"Keys removed from output: {sorted(missing_keys)}")
    if added_keys:
        problems.append(f"Keys added to output: {sorted(added_keys)}")
    if changed:
        lines = [f"  {k}: baseline={b!r} current={c!r}" for k, (b, c) in sorted(changed.items())]
        problems.append(f"Values changed:\n" + "\n".join(lines))

    if problems:
        msg = f"\n{label} regression FAILED:\n" + "\n".join(problems)
        raise AssertionError(msg)


@pytest.mark.parametrize(
    "case",
    REGRESSION_CASES,
    ids=[c.label for c in REGRESSION_CASES],
)
def test_setup_regression(tmp_path: Path, case: RegressionCase) -> None:
    """Pipeline output for each (car, track) baseline must match its fixture."""
    baseline = FIXTURES / case.baseline
    if not baseline.exists():
        pytest.skip(f"Baseline fixture missing (regenerate with pipeline.produce): {baseline}")
    ibt_path = REPO_ROOT / case.ibt
    if not ibt_path.exists():
        pytest.skip(f"IBT not present in checkout (LFS / gitignored): {ibt_path}")
    current = tmp_path / f"{case.car}_current.sto"
    _run_pipeline(
        car=case.car,
        ibt=case.ibt,
        wing=case.wing,
        fuel=case.fuel,
        out_sto=current,
        force=case.force,
    )
    _assert_setup_matches(baseline, current, case.label)


if __name__ == "__main__":
    # Run directly: python tests/test_setup_regression.py
    # Import pytest's skip exception so missing baselines print [SKIP] rather
    # than crashing with an unhandled BaseException.
    try:
        from _pytest.outcomes import Skipped as _PytestSkipped
    except ImportError:
        _PytestSkipped = type(None)  # type: ignore[misc,assignment]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for case in REGRESSION_CASES:
            try:
                test_setup_regression(tmp_path, case)
                print(f"[OK] {case.label} regression")
            except AssertionError as e:
                print(f"[FAIL] {case.label}: {e}")
            except _PytestSkipped as e:  # type: ignore[misc]
                print(f"[SKIP] {case.label}: {e}")
