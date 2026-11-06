from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from config import Config
import stages.summary as summary


class _FakeResponse:
    def parse_to_dict(self) -> dict:
        return {"overall_success": True}


class _RecordingMisty:
    ip = "127.0.0.1"

    def __init__(self, timeline: list, failing_action: str | None = None) -> None:
        self.timeline = timeline
        self.failing_action = failing_action

    def perform_action(self, action_name: str, data: dict | None = None) -> _FakeResponse:
        self.timeline.append((action_name, data))
        if action_name == self.failing_action:
            raise RuntimeError("robot stalled")
        return _FakeResponse()


class _FakeLog:
    def __init__(self, timeline: list) -> None:
        self.timeline = timeline

    def generate_summary(self) -> str:
        return "Good session."

    def record(self, name: str, **payload) -> None:
        pass

    def save_to_file(self) -> None:
        self.timeline.append(("save_log", None))


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timeline = []
        self.cfg = Config(ip="127.0.0.1", openai_api_key="unused", participant_id="test")
        patches = [
            mock.patch.object(summary, "_regenerate_csvs", lambda: self.timeline.append(("csv", None))),
            mock.patch.object(
                summary,
                "time",
                SimpleNamespace(sleep=lambda seconds: self.timeline.append(("sleep", seconds))),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_restores_neutral_pose_after_the_wave_finishes(self) -> None:
        misty = _RecordingMisty(self.timeline)

        summary.run_summary(misty, self.cfg, _FakeLog(self.timeline))

        wave = ("arms_move", {
            "LeftArmPosition": -80,
            "RightArmPosition": -80,
            "LeftArmVelocity": 50,
            "RightArmVelocity": 50,
        })
        self.assertEqual(
            self.timeline[self.timeline.index(wave):],
            [
                wave,
                ("save_log", None),
                ("csv", None),
                ("sleep", self.cfg.summary_gesture_hold_s),
                ("head_move", {"Pitch": 0, "Roll": 0, "Yaw": 0, "Velocity": 60}),
                ("arms_move", {
                    "LeftArmPosition": 80,
                    "RightArmPosition": 80,
                    "LeftArmVelocity": 60,
                    "RightArmVelocity": 60,
                }),
            ],
        )

    def test_log_is_saved_and_arms_lowered_when_head_reset_fails(self) -> None:
        misty = _RecordingMisty(self.timeline, failing_action="head_move")

        summary.run_summary(misty, self.cfg, _FakeLog(self.timeline))

        names = [name for name, _ in self.timeline]
        self.assertIn("save_log", names)
        self.assertEqual(names[-1], "arms_move")


if __name__ == "__main__":
    unittest.main()
