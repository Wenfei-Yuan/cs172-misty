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
            "_search_screen_position_with_vlm": screen_watch._search_screen_position_with_vlm,
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

        cfg = SimpleNamespace(screen_settle_s=0.0, cache_screen_pos=True)
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
        self.assertIn(("screen_cache_hit", {"yaw": 18.0, "pitch": -6.0}), log.records)

    def test_reuses_position_with_vlm_check_when_cache_disabled(self) -> None:
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

        cfg = SimpleNamespace(screen_settle_s=0.0, cache_screen_pos=False)
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

    def test_search_uses_fine_adjustment_after_visible_detection(self) -> None:
        look_calls = []
        statuses = iter(
            [
                SimpleNamespace(ok=True, status="visible", reason="screen_visible_not_centered"),
                SimpleNamespace(ok=True, status="visible", reason="screen_visible_not_centered"),
                SimpleNamespace(ok=True, status="aligned", reason=None),
                SimpleNamespace(ok=True, status="aligned", reason=None),
            ]
        )

        screen_watch.look_at_screen = lambda misty, position: look_calls.append(position)
        screen_watch._check_screen_alignment = lambda *args, **kwargs: next(statuses)
        screen_watch.time = SimpleNamespace(sleep=lambda seconds: None)

        cfg = SimpleNamespace(
            screen_settle_s=0.0,
            screen_search_yaw_offsets=(0.0,),
            screen_search_pitch_offsets=(0.0,),
            screen_search_fine_yaw_offsets=(0.0, 5.0),
            screen_search_fine_pitch_offsets=(0.0,),
            screen_alignment_confirm_checks=2,
            screen_alignment_confirm_settle_s=0.0,
        )
        log = _FakeLog()
        seed = screen_watch.ScreenPos(yaw=10.0, pitch=2.0)

        result = screen_watch._search_screen_position_with_vlm(
            misty=object(),
            cfg=cfg,
            seed=seed,
            log=log,
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.position, screen_watch.ScreenPos(yaw=15.0, pitch=2.0))
        self.assertEqual(
            look_calls,
            [
                screen_watch.ScreenPos(yaw=10.0, pitch=2.0),
                screen_watch.ScreenPos(yaw=10.0, pitch=2.0),
                screen_watch.ScreenPos(yaw=15.0, pitch=2.0),
            ],
        )

    def test_default_screen_position_uses_front_facing_seed(self) -> None:
        cfg = SimpleNamespace(screen_search_seed_yaw=0.0, screen_search_seed_pitch=0.0)

        result = screen_watch.default_screen_position(cfg)

        self.assertEqual(result, screen_watch.ScreenPos(yaw=0.0, pitch=0.0))

    def test_find_screen_position_starts_from_front_facing_seed(self) -> None:
        look_calls = []

        def fake_search(*args, **kwargs):
            return screen_watch.ScreenSearchResult(status="not_found", reason="screen_not_found")

        screen_watch.look_at_screen = lambda misty, position: look_calls.append(position)
        screen_watch._search_screen_position_with_vlm = fake_search
        screen_watch.time = SimpleNamespace(sleep=lambda seconds: None)

        cfg = SimpleNamespace(
            screen_search_seed_yaw=0.0,
            screen_search_seed_pitch=0.0,
            screen_settle_s=0.0,
        )

        screen_watch.find_screen_position_with_vlm(misty=object(), cfg=cfg, log=None)

        self.assertEqual(look_calls, [screen_watch.ScreenPos(yaw=0.0, pitch=0.0)])


if __name__ == "__main__":
    unittest.main()
