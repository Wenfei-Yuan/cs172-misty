from __future__ import annotations

import unittest
from types import SimpleNamespace

import stages.distraction as distraction
from utils.expressions import DISTRACTION_FACE, SPEAKING_FACE


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeLog:
    def __init__(self) -> None:
        self.records = []
        self.results = []

    def record_distraction_start(self) -> None:
        self.records.append(("start", {}))

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))

    def record_distraction_result(self, outcome: str, gaze_seen: bool, latency_s) -> None:
        self.results.append((outcome, gaze_seen, latency_s))


class DistractionTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "speak_text": distraction.speak_text,
            "show_image": distraction.show_image,
            "shake_head_only": distraction.shake_head_only,
            "acknowledge_gaze_recovery": distraction.acknowledge_gaze_recovery,
            "look_at_screen": distraction.look_at_screen,
            "capture_frame_result": distraction.capture_frame_result,
            "analyze_gaze_capture": distraction.analyze_gaze_capture,
            "time": distraction.time,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(distraction, name, value)

    def test_gaze_poll_interval_prefers_fast_window(self) -> None:
        cfg = SimpleNamespace(
            gaze_poll_interval_s=2.0,
            fast_gaze_poll_interval_s=0.6,
            fast_gaze_poll_window_s=8.0,
        )

        self.assertEqual(distraction._gaze_poll_interval(cfg, 0.0), 0.6)
        self.assertEqual(distraction._gaze_poll_interval(cfg, 7.9), 0.6)
        self.assertEqual(distraction._gaze_poll_interval(cfg, 8.0), 2.0)

    def test_run_distraction_sleeps_only_remaining_fast_poll_time(self) -> None:
        clock = _FakeClock(start=100.0)
        sleep_calls = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            clock.sleep(seconds)

        distraction.time = SimpleNamespace(time=clock.time, monotonic=clock.monotonic, sleep=fake_sleep)
        distraction.speak_text = lambda *args, **kwargs: None
        distraction.show_image = lambda *args, **kwargs: None
        distraction.shake_head_only = lambda *args, **kwargs: None
        distraction.acknowledge_gaze_recovery = lambda *args, **kwargs: None
        distraction.look_at_screen = lambda *args, **kwargs: None
        distraction.capture_frame_result = lambda misty: SimpleNamespace(ok=True, base64="frame", reason=None)

        statuses = ["not_gazing", "gazing"]

        def fake_analyze(frame, cfg):
            clock.now += 0.3
            return SimpleNamespace(status=statuses.pop(0), reason=None)

        distraction.analyze_gaze_capture = fake_analyze

        cfg = SimpleNamespace(
            gaze_timeout_s=10.0,
            gaze_poll_interval_s=2.0,
            fast_gaze_poll_interval_s=0.5,
            fast_gaze_poll_window_s=8.0,
        )
        log = _FakeLog()

        result = distraction.run_distraction(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=None,
            consume_interrupt=lambda: None,
        )

        self.assertEqual(result.outcome, "gaze")
        self.assertTrue(result.gaze_seen)
        self.assertEqual(len(sleep_calls), 1)
        self.assertAlmostEqual(sleep_calls[0], 0.2, places=6)
        self.assertAlmostEqual(result.gaze_latency_s, 0.8, places=6)

    def test_run_distraction_acknowledges_without_extra_screen_return_on_gaze(self) -> None:
        clock = _FakeClock(start=50.0)
        calls = []
        screen_pos = SimpleNamespace(yaw=18.0, pitch=-6.0)

        distraction.time = SimpleNamespace(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep)
        distraction.speak_text = lambda misty, cfg, text, **kwargs: calls.append(("speak", text))
        distraction.show_image = lambda misty, filename: calls.append(("face", filename))

        def fake_shake(misty, cfg, stop_event, position_callback=None):
            if position_callback is not None:
                position_callback(9.0)

        distraction.shake_head_only = fake_shake
        distraction.acknowledge_gaze_recovery = (
            lambda misty, cfg, current_yaw=None: calls.append(("ack", current_yaw))
        )
        distraction.look_at_screen = lambda misty, position: calls.append(("look", position))
        distraction.capture_frame_result = lambda misty: SimpleNamespace(ok=True, base64="frame", reason=None)
        distraction.analyze_gaze_capture = lambda frame, cfg: SimpleNamespace(status="gazing", reason=None)

        cfg = SimpleNamespace(
            gaze_timeout_s=10.0,
            gaze_poll_interval_s=0.5,
            fast_gaze_poll_interval_s=0.5,
            fast_gaze_poll_window_s=0.0,
        )
        log = _FakeLog()

        result = distraction.run_distraction(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=screen_pos,
            consume_interrupt=lambda: None,
        )

        self.assertEqual(result.outcome, "gaze")
        self.assertEqual(
            calls,
            [
                ("face", DISTRACTION_FACE),
                ("face", SPEAKING_FACE),
                ("speak", "I see you! Let me check what you were working on."),
                ("ack", 9.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
