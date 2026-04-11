"""Setup regression tests.

Captures current pipeline output as fixtures and verifies future runs produce
identical output. This is the safety net for the overhaul: any unintended
change to BMW or Porsche setup generation will fail these tests.

Regenerating fixtures (only when an intentional change is made):
    python -m pipeline.produce --car bmw \\
        --ibt "data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt" \\
        --wing 17 --sto tests/fixtures/baselines/bmw_sebring_baseline.sto

    python -m pipeline.produce --car porsche \\
        --ibt "ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt" \\
        --fuel 58 --wing 17 --sto tests/fixtures/baselines/porsche_algarve_baseline.sto
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "baselines"

# Keys extracted from .sto that matter for setup correctness.
# Excludes the generation timestamp and other cosmetic fields.
SETUP_KEY_PATTERN = re.compile(
    r'Id="(CarSetup_[^"]+)"\s+Value="([^"]+)"'
)


def _extract_setup_values(sto_path: Path) -> dict[str, str]:
    """Parse a .sto XML file and return {parameter_id: value_string}."""
    text = sto_path.read_text(encoding="utf-8")
    return dict(SETUP_KEY_PATTERN.findall(text))


def _run_pipeline(car: str, ibt: str, wing: int, fuel: int | None, out_sto: Path) -> None:
    """Run pipeline.produce with given args and write output to out_sto."""
    cmd = [
        sys.executable, "-m", "pipeline.produce",
        "--car", car,
        "--ibt", ibt,
        "--wing", str(wing),
        "--sto", str(out_sto),
    ]
    if fuel is not None:
        cmd += ["--fuel", str(fuel)]
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


def test_bmw_sebring_regression(tmp_path: Path) -> None:
    """BMW/Sebring pipeline output must match fixture exactly."""
    baseline = FIXTURES / "bmw_sebring_baseline.sto"
    if not baseline.exists():
        import pytest
        pytest.skip(f"Baseline fixture missing (regenerate with pipeline.produce): {baseline}")
    current = tmp_path / "bmw_current.sto"
    _run_pipeline(
        car="bmw",
        ibt="data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt",
        wing=17,
        fuel=None,
        out_sto=current,
    )
    _assert_setup_matches(baseline, current, "BMW/Sebring")


def test_porsche_algarve_regression(tmp_path: Path) -> None:
    """Porsche/Algarve pipeline output must match fixture exactly."""
    baseline = FIXTURES / "porsche_algarve_baseline.sto"
    if not baseline.exists():
        import pytest
        pytest.skip(f"Baseline fixture missing (regenerate with pipeline.produce): {baseline}")
    current = tmp_path / "porsche_current.sto"
    _run_pipeline(
        car="porsche",
        ibt="ibtfiles/porsche963gtp_algarve gp 2026-04-06 16-46-36.ibt",
        wing=17,
        fuel=58,
        out_sto=current,
    )
    _assert_setup_matches(baseline, current, "Porsche/Algarve")


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
        try:
            test_bmw_sebring_regression(tmp_path)
            print("[OK] BMW/Sebring regression")
        except AssertionError as e:
            print(f"[FAIL] BMW/Sebring: {e}")
        except _PytestSkipped as e:  # type: ignore[misc]
            print(f"[SKIP] BMW/Sebring: {e}")
        try:
            test_porsche_algarve_regression(tmp_path)
            print("[OK] Porsche/Algarve regression")
        except AssertionError as e:
            print(f"[FAIL] Porsche/Algarve: {e}")
        except _PytestSkipped as e:  # type: ignore[misc]
            print(f"[SKIP] Porsche/Algarve: {e}")
