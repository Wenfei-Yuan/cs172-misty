from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from config import Config
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
            "time": screen_watch.time,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(screen_watch, name, value)

    def test_returns_to_known_position(self) -> None:
        calls = []
        screen_watch.show_image = lambda misty, face: calls.append(("face", face))
        screen_watch.look_at_screen = lambda misty, position: calls.append(("look", position))
        screen_watch.time = SimpleNamespace(sleep=lambda seconds: calls.append(("sleep", seconds)))

        log = _FakeLog()
        cfg = SimpleNamespace(screen_settle_s=0.7, return_to_screen_pause_s=2.0)
        screen_pos = screen_watch.ScreenPos(yaw=45.0, pitch=0.0)

        result = screen_watch.run_screen_watch(misty=object(), cfg=cfg, log=log, screen_pos=screen_pos)

        self.assertEqual(result, screen_pos)
        self.assertEqual(
            calls,
            [
                ("face", screen_watch.READING_FACE),
                ("sleep", 2.0),
                ("look", screen_pos),
                ("sleep", 0.7),
            ],
        )
        self.assertIn(("screen_cache_hit", {"yaw": 45.0, "pitch": 0.0}), log.records)
        self.assertEqual(log.observations, ["Screen alignment verified via cached position."])

    def test_never_touches_camera_or_openai(self) -> None:
        screen_watch.show_image = lambda *args, **kwargs: None
        screen_watch.look_at_screen = lambda *args, **kwargs: None
        screen_watch.time = SimpleNamespace(sleep=lambda seconds: None)
        cfg = SimpleNamespace(screen_settle_s=0.0, return_to_screen_pause_s=0.0)

        with (
            mock.patch("requests.get") as http_get,
            mock.patch("openai.OpenAI") as openai_constructor,
        ):
            screen_watch.run_screen_watch(
                misty=object(), cfg=cfg, log=_FakeLog(), screen_pos=screen_watch.ScreenPos(yaw=45.0, pitch=0.0)
            )

        http_get.assert_not_called()
        openai_constructor.assert_not_called()

    def test_default_screen_position_is_left_of_center(self) -> None:
        cfg = Config(ip="127.0.0.1", openai_api_key="unused", participant_id="test")

        result = screen_watch.default_screen_position(cfg)

        self.assertEqual(result, screen_watch.ScreenPos(yaw=45.0, pitch=0.0))
        self.assertGreater(result.yaw, 0.0)

    def test_default_screen_position_clamps_to_head_range(self) -> None:
        cfg = SimpleNamespace(screen_yaw=120.0, screen_pitch=-60.0)

        result = screen_watch.default_screen_position(cfg)

        self.assertEqual(result, screen_watch.ScreenPos(yaw=90.0, pitch=-40.0))


if __name__ == "__main__":
    unittest.main()
