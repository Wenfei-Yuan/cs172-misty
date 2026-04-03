from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.head_control import shake_head_only, swing_arms_only


class _FakeMisty:
    def __init__(self) -> None:
        self.actions = []

    def perform_action(self, name: str, payload: dict) -> None:
        self.actions.append((name, payload))


class _FakeStopEvent:
    def __init__(self, wait_results: list[bool]) -> None:
        self.wait_results = list(wait_results)
        self.wait_calls = []

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return self.wait_results.pop(0)


class HeadControlTests(unittest.TestCase):
    def test_shake_head_only_pauses_at_both_endpoints(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(shake_amplitude_deg=60, shake_period_s=0.6, shake_pause_s=0.5)
        stop_event = _FakeStopEvent([False, False, False, True])

        shake_head_only(misty, cfg, stop_event)

        self.assertEqual(
            misty.actions,
            [
                ("head_move", {"Yaw": 60, "Velocity": 80}),
                ("head_move", {"Yaw": -60, "Velocity": 80}),
            ],
        )
        self.assertEqual(stop_event.wait_calls, [0.6, 0.5, 0.6, 0.5])

    def test_swing_arms_only_pauses_at_both_endpoints(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(
            arm_swing_up_deg=-80,
            arm_swing_down_deg=80,
            arm_swing_velocity=55,
            arm_swing_period_s=0.5,
            arm_swing_pause_s=0.2,
        )
        stop_event = _FakeStopEvent([False, False, False, True])

        swing_arms_only(misty, cfg, stop_event)

        self.assertEqual(
            misty.actions,
            [
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -80,
                        "RightArmPosition": -80,
                        "LeftArmVelocity": 55,
                        "RightArmVelocity": 55,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 55,
                        "RightArmVelocity": 55,
                    },
                ),
            ],
        )
        self.assertEqual(stop_event.wait_calls, [0.5, 0.2, 0.5, 0.2])


if __name__ == "__main__":
    unittest.main()
