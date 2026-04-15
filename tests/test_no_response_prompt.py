from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

import stages.no_response_prompt as no_response_prompt


class _FakeLog:
    def __init__(self) -> None:
        self.records = []
        self.voice_attempts = []

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))

    def record_voice_prompt(self) -> None:
        self.voice_attempts.append(True)


class NoResponsePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "show_image": no_response_prompt.show_image,
            "generate_focus_reminder_from_text": no_response_prompt.generate_focus_reminder_from_text,
            "speak_text": no_response_prompt.speak_text,
            "cue_screen_with_left_arm": no_response_prompt.cue_screen_with_left_arm,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(no_response_prompt, name, value)

    def test_run_no_response_speaks_then_cues_left_arm(self) -> None:
        calls = []
        log = _FakeLog()
        cfg = SimpleNamespace()
        dynamic = SimpleNamespace(
            summary="user is off task",
            reminder="Please come back to the task.",
            used_fallback=False,
            reason=None,
        )

        no_response_prompt.show_image = lambda misty, filename: calls.append(("show_image", filename))
        no_response_prompt.generate_focus_reminder_from_text = lambda current_text, current_cfg: dynamic
        no_response_prompt.speak_text = lambda misty, current_cfg, text, **kwargs: calls.append(("speak_text", text, kwargs))
        no_response_prompt.cue_screen_with_left_arm = lambda misty, current_cfg, repetitions=1, stop_event=None: calls.append(("cue_left_arm", repetitions))

        no_response_prompt.run_no_response(object(), cfg, log, attempt=2, current_text="draft essay")

        self.assertEqual(calls[0], ("show_image", no_response_prompt.SPEAKING_FACE))
        self.assertEqual(calls[1][0], "speak_text")
        self.assertEqual(calls[1][1], "Please come back to the task.")
        self.assertEqual(calls[2], ("cue_left_arm", 1))
        self.assertEqual(log.voice_attempts, [True])
        self.assertEqual(log.records[0][0], "no_response_prompt_generated")

    def test_run_no_response_skips_when_stop_already_set(self) -> None:
        calls = []
        log = _FakeLog()
        cfg = SimpleNamespace()
        stop_event = threading.Event()
        stop_event.set()

        no_response_prompt.show_image = lambda misty, filename: calls.append(("show_image", filename))
        no_response_prompt.generate_focus_reminder_from_text = lambda current_text, current_cfg: calls.append(("generate", current_text))
        no_response_prompt.speak_text = lambda misty, current_cfg, text, **kwargs: calls.append(("speak_text", text, kwargs))
        no_response_prompt.cue_screen_with_left_arm = lambda misty, current_cfg, repetitions=1, stop_event=None: calls.append(("cue_left_arm", repetitions))

        no_response_prompt.run_no_response(object(), cfg, log, attempt=1, current_text="draft essay", stop_event=stop_event)

        self.assertEqual(calls, [])
        self.assertEqual(log.voice_attempts, [])
        self.assertEqual(log.records[0], ("no_response_skipped", {"attempt": 1, "reason": "stop_received_before_prompt"}))

    def test_run_no_response_skips_arm_cue_when_speech_interrupted(self) -> None:
        calls = []
        log = _FakeLog()
        cfg = SimpleNamespace()
        dynamic = SimpleNamespace(
            summary="user is off task",
            reminder="Please come back to the task.",
            used_fallback=False,
            reason=None,
        )

        no_response_prompt.show_image = lambda misty, filename: calls.append(("show_image", filename))
        no_response_prompt.generate_focus_reminder_from_text = lambda current_text, current_cfg: dynamic
        no_response_prompt.speak_text = lambda misty, current_cfg, text, **kwargs: {"overall_success": False, "speech": None, "interrupted": True, "timed_out": False}
        no_response_prompt.cue_screen_with_left_arm = lambda misty, current_cfg, repetitions=1, stop_event=None: calls.append(("cue_left_arm", repetitions))

        no_response_prompt.run_no_response(object(), cfg, log, attempt=2, current_text="draft essay", stop_event=threading.Event())

        self.assertEqual(calls, [("show_image", no_response_prompt.SPEAKING_FACE)])
        self.assertEqual(log.voice_attempts, [])
        self.assertEqual(log.records[0], ("no_response_skipped", {"attempt": 2, "reason": "stop_received_during_speech"}))


if __name__ == "__main__":
    unittest.main()