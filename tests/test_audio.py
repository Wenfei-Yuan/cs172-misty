from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.audio import DEFAULT_CHILD_VOICE, speak_text


class _FakeResponse:
    def parse_to_dict(self) -> dict:
        return {"overall_success": True}


class _FakeMisty:
    def __init__(self) -> None:
        self.actions = []

    def perform_action(self, action_name: str, data: dict) -> _FakeResponse:
        self.actions.append((action_name, data))
        return _FakeResponse()


class _FakeLog:
    def __init__(self) -> None:
        self.records = []

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))


class AudioTests(unittest.TestCase):
    def test_speak_text_uses_child_voice_by_default(self) -> None:
        misty = _FakeMisty()
        log = _FakeLog()

        result = speak_text(misty, SimpleNamespace(), "Hello!", log=log, stage="test")

        self.assertTrue(result["overall_success"])
        self.assertEqual(misty.actions[0][0], "speak")
        self.assertEqual(misty.actions[0][1]["Text"], "Hello!")
        self.assertEqual(misty.actions[0][1]["voice"], DEFAULT_CHILD_VOICE)
        self.assertEqual(log.records[0][1]["voice"], DEFAULT_CHILD_VOICE)

    def test_speak_text_uses_configured_voice(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(speech_voice="Justin")

        speak_text(misty, cfg, "Hi there.")

        self.assertEqual(misty.actions[0][1]["voice"], "Justin")


if __name__ == "__main__":
    unittest.main()
