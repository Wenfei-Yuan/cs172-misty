from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from config import Config
import stages.bootup as bootup


class _FakeLog:
    def __init__(self) -> None:
        self.records = []

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))


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
            raise RuntimeError("head command stalled")
        return _FakeResponse()


def _spoken(timeline: list) -> list[str]:
    return [data["Text"] for name, data in timeline if name == "speak"]


class BootupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_time = bootup.time
        self.timeline = []
        bootup.time = SimpleNamespace(sleep=lambda seconds: self.timeline.append(("sleep", seconds)))
        self.cfg = Config(ip="127.0.0.1", openai_api_key="unused", participant_id="test")

    def tearDown(self) -> None:
        bootup.time = self.original_time

    def test_turns_left_to_screen_between_the_original_lines(self) -> None:
        misty = _RecordingMisty(self.timeline)
        log = _FakeLog()

        result = bootup.run_bootup(misty, self.cfg, log)

        self.assertEqual((result.yaw, result.pitch), (45.0, 0.0))
        self.assertEqual(
            [
                (name, data["Text"] if name == "speak" else data)
                for name, data in self.timeline
                if name not in {"arms_move", "sleep"}
            ],
            [
                ("image_show", {"FileName": bootup.BOOT_FACE}),
                ("head_move", {"Pitch": 0, "Roll": 0, "Yaw": 0, "Velocity": 60}),
                ("speak", "Hello! I'm Misty. Let's focus today!"),
                ("speak", "I'm looking for your screen now."),
                ("head_move", {"Yaw": 45.0, "Pitch": 0.0, "Velocity": 90}),
                ("speak", "I've found the screen!"),
                ("speak", "Let's start reading. You can open the browser extension and start reading now."),
                ("image_show", {"FileName": bootup.SPEAKING_FACE}),
            ],
        )

    def test_turns_after_speech_finishes_and_holds_before_announcing_the_screen(self) -> None:
        bootup.run_bootup(_RecordingMisty(self.timeline), self.cfg, _FakeLog())

        head_index = self.timeline.index(("head_move", {"Yaw": 45.0, "Pitch": 0.0, "Velocity": 90}))
        self.assertEqual(self.timeline[head_index - 2][1]["Text"], "I'm looking for your screen now.")
        self.assertEqual(self.timeline[head_index - 1], ("sleep", self.cfg.bootup_intro_speech_wait_s))
        self.assertEqual(self.timeline[head_index + 1], ("sleep", self.cfg.screen_settle_s))
        self.assertEqual(self.timeline[head_index + 2], ("sleep", 3.0))
        self.assertEqual(self.timeline[head_index + 3][1]["Text"], "I've found the screen!")

    def test_resets_head_and_lets_it_settle_before_speaking(self) -> None:
        bootup.run_bootup(_RecordingMisty(self.timeline), self.cfg, _FakeLog())

        names = [name for name, _ in self.timeline]
        head_index = names.index("head_move")
        self.assertEqual(
            self.timeline[head_index],
            ("head_move", {"Pitch": 0, "Roll": 0, "Yaw": 0, "Velocity": 60}),
        )
        self.assertEqual(self.timeline[head_index + 1], ("sleep", self.cfg.head_reset_settle_s))
        self.assertLess(head_index, names.index("speak"))

    def test_never_touches_camera_or_openai(self) -> None:
        with (
            mock.patch("requests.get") as http_get,
            mock.patch("openai.OpenAI") as openai_constructor,
            mock.patch("utils.openai_client.get_openai_client") as get_client,
            mock.patch("utils.vision.capture_frame_result") as capture_frame,
            mock.patch("utils.vision.analyze_gaze_capture") as analyze_gaze,
        ):
            bootup.run_bootup(_RecordingMisty(self.timeline), self.cfg, _FakeLog())

        http_get.assert_not_called()
        openai_constructor.assert_not_called()
        get_client.assert_not_called()
        capture_frame.assert_not_called()
        analyze_gaze.assert_not_called()

    def test_logs_configured_position_and_completion(self) -> None:
        log = _FakeLog()

        bootup.run_bootup(_RecordingMisty(self.timeline), self.cfg, log)

        self.assertIn(
            ("screen_position_initialized", {"yaw": 45.0, "pitch": 0.0, "source": "config"}),
            log.records,
        )
        self.assertEqual(log.records[-1], ("bootup_complete", {}))

    def test_failed_head_move_keeps_the_script_and_returns_a_position(self) -> None:
        misty = _RecordingMisty(self.timeline, failing_action="head_move")

        result = bootup.run_bootup(misty, self.cfg, _FakeLog())

        self.assertEqual((result.yaw, result.pitch), (45.0, 0.0))
        self.assertEqual(
            _spoken(self.timeline),
            [
                "Hello! I'm Misty. Let's focus today!",
                "I'm looking for your screen now.",
                "I've found the screen!",
                "Let's start reading. You can open the browser extension and start reading now.",
            ],
        )

    def test_does_not_use_arm_gestures_for_intro(self) -> None:
        bootup.run_bootup(_RecordingMisty(self.timeline), self.cfg, _FakeLog())

        arm_moves = [data for name, data in self.timeline if name == "arms_move"]
        self.assertEqual(len(arm_moves), 1)
        self.assertEqual(arm_moves[0]["LeftArmPosition"], 80)
        self.assertEqual(arm_moves[0]["RightArmPosition"], 80)


if __name__ == "__main__":
    unittest.main()
