import unittest
from types import SimpleNamespace


def _apply_calibration_step_blocks(
    *,
    step1,
    step2,
    step3,
    step4,
    step5,
    step6,
    blocked_steps: set[int],
) -> tuple[object, object, object, object, object, object]:
    """Mirror pipeline.produce helper without importing scipy-heavy module."""
    if not blocked_steps:
        return step1, step2, step3, step4, step5, step6
    if 1 in blocked_steps:
        step1 = None
    if 2 in blocked_steps:
        step2 = None
    if 3 in blocked_steps:
        step3 = None
    if 4 in blocked_steps:
        step4 = None
    if 5 in blocked_steps:
        step5 = None
    if 6 in blocked_steps:
        step6 = None
    return step1, step2, step3, step4, step5, step6


class ProduceCalibrationGateTests(unittest.TestCase):
    def test_apply_blocks_nuls_only_requested_steps(self) -> None:
        s1 = SimpleNamespace(name="s1")
        s2 = SimpleNamespace(name="s2")
        s3 = SimpleNamespace(name="s3")
        s4 = SimpleNamespace(name="s4")
        s5 = SimpleNamespace(name="s5")
        s6 = SimpleNamespace(name="s6")

        out = _apply_calibration_step_blocks(
            step1=s1,
            step2=s2,
            step3=s3,
            step4=s4,
            step5=s5,
            step6=s6,
            blocked_steps={2, 4, 6},
        )
        self.assertIs(out[0], s1)
        self.assertIsNone(out[1])
        self.assertIs(out[2], s3)
        self.assertIsNone(out[3])
        self.assertIs(out[4], s5)
        self.assertIsNone(out[5])

    def test_apply_blocks_noop_when_empty(self) -> None:
        s1 = SimpleNamespace(name="s1")
        s2 = SimpleNamespace(name="s2")
        s3 = SimpleNamespace(name="s3")
        s4 = SimpleNamespace(name="s4")
        s5 = SimpleNamespace(name="s5")
        s6 = SimpleNamespace(name="s6")

        out = _apply_calibration_step_blocks(
            step1=s1,
            step2=s2,
            step3=s3,
            step4=s4,
            step5=s5,
            step6=s6,
            blocked_steps=set(),
        )
        self.assertIs(out[0], s1)
        self.assertIs(out[1], s2)
        self.assertIs(out[2], s3)
        self.assertIs(out[3], s4)
        self.assertIs(out[4], s5)
        self.assertIs(out[5], s6)


if __name__ == "__main__":
    unittest.main()
