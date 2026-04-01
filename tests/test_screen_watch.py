from __future__ import annotations

import unittest
from types import SimpleNamespace

import stages.screen_watch as screen_watch


class _FakeLog:
    def __init__(self) -> None:
        self.records = []
        self.observations = []

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))

    def record_screen_observation(self, analysis: str) -> None:
        self.observations.append(analysis)


class ScreenWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "show_image": screen_watch.show_image,
            "look_at_screen": screen_watch.look_at_screen,
            "speak_text": screen_watch.speak_text,
            "find_screen_position_with_vlm": screen_watch.find_screen_position_with_vlm,
            "_check_screen_alignment": screen_watch._check_screen_alignment,
            "time": screen_watch.time,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(screen_watch, name, value)

    def test_reuses_bootup_position_without_new_search(self) -> None:
        look_calls = []
        search_calls = []

        screen_watch.show_image = lambda *args, **kwargs: None
        screen_watch.look_at_screen = lambda misty, position: look_calls.append(position)
        screen_watch.speak_text = lambda *args, **kwargs: None
        screen_watch.find_screen_position_with_vlm = lambda *args, **kwargs: search_calls.append(True)
        screen_watch._check_screen_alignment = lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            status="visible",
            reason="needs_recheck",
        )
        screen_watch.time = SimpleNamespace(sleep=lambda seconds: None)

        cfg = SimpleNamespace(screen_settle_s=0.0)
        log = _FakeLog()
        screen_pos = screen_watch.ScreenPos(yaw=18.0, pitch=-6.0)

        result = screen_watch.run_screen_watch(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=screen_pos,
        )

        self.assertEqual(result, screen_pos)
        self.assertEqual(search_calls, [])
        self.assertEqual(look_calls, [screen_pos])
        self.assertIn(("screen_position_reused", {"yaw": 18.0, "pitch": -6.0, "source": "bootup"}), log.records)


if __name__ == "__main__":
    unittest.main()