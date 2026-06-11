from __future__ import annotations

import unittest
from types import SimpleNamespace

import stages.distraction as distraction
from utils.expressions import DISTRACTION_FACE


class _FakeLog:
    def __init__(self) -> None:
        self.records = []
        self.results = []

    def record_distraction_start(self, **payload) -> None:
        self.records.append(("start", payload))

    def record(self, name: str, **payload) -> None:
        self.records.append((name, payload))

    def record_distraction_result(self, outcome: str, sequence_completed: bool, latency_s) -> None:
        self.results.append((outcome, sequence_completed, latency_s))


class DistractionShakeCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "show_image": distraction.show_image,
            "perform_distraction_start_sequence": distraction.perform_distraction_start_sequence,
            "look_at_screen": distraction.look_at_screen,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(distraction, name, value)

    def test_sequence_complete_outcome_after_distraction_sequence_completes(self) -> None:
        """After the distraction start sequence completes, outcome is 'sequence_complete'."""
        face_calls = []
        look_calls = []
        sequence_calls = []

        distraction.show_image = lambda misty, f: face_calls.append(f)
        distraction.perform_distraction_start_sequence = lambda misty, cfg, screen_pos, stop_event=None: sequence_calls.append(
            (screen_pos.yaw, screen_pos.pitch, stop_event is not None)
        )
        distraction.look_at_screen = lambda misty, pos: look_calls.append(pos)

        cfg = SimpleNamespace(
            gaze_timeout_s=30.0,
        )
        log = _FakeLog()

        result = distraction.run_distraction(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=SimpleNamespace(yaw=18.0, pitch=-6.0),
            consume_interrupt=lambda: None,
        )

        self.assertEqual(result.outcome, "sequence_complete")
        self.assertTrue(result.sequence_completed)
        self.assertIsNone(result.completion_latency_s)
        self.assertIn(DISTRACTION_FACE, face_calls)
        self.assertEqual(sequence_calls, [(18.0, -6.0, True)])
        self.assertEqual(look_calls, [], "look_at_screen must NOT be called on sequence completion")
        self.assertEqual(len(log.results), 1)
        self.assertEqual(log.results[0][0], "sequence_complete")
        self.assertTrue(log.results[0][1])

    def test_timeout_fallback_calls_look_at_screen(self) -> None:
        """When no callbacks fire and gaze_timeout_s is exceeded, outcome is 'timeout' and look_at_screen is called."""
        look_calls = []

        distraction.show_image = lambda misty, f: None

        def fake_sequence(misty, cfg, screen_pos, stop_event=None):
            stop_event.wait()

        distraction.perform_distraction_start_sequence = fake_sequence
        distraction.look_at_screen = lambda misty, pos: look_calls.append(pos)

        screen_pos = SimpleNamespace(yaw=18.0, pitch=-6.0)

        # gaze_timeout_s=0.0 so the elapsed >= check fires immediately
        cfg = SimpleNamespace(
            gaze_timeout_s=0.0,
            return_to_screen_pause_s=0.0,
        )
        log = _FakeLog()

        result = distraction.run_distraction(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=screen_pos,
            consume_interrupt=lambda: None,
        )

        self.assertEqual(result.outcome, "timeout")
        self.assertFalse(result.sequence_completed)
        self.assertEqual(look_calls, [screen_pos])

    def test_stop_interrupt_skips_look_at_screen(self) -> None:
        """If a stop interrupt arrives, outcome is 'stop' and look_at_screen is NOT called."""
        look_calls = []

        distraction.show_image = lambda misty, f: None

        def fake_sequence(misty, cfg, screen_pos, stop_event=None):
            stop_event.wait()

        distraction.perform_distraction_start_sequence = fake_sequence
        distraction.look_at_screen = lambda misty, pos: look_calls.append(pos)

        interrupt_counter = [0]

        def fake_consume():
            interrupt_counter[0] += 1
            if interrupt_counter[0] >= 2:
                return "stop"
            return None

        cfg = SimpleNamespace(
            gaze_timeout_s=30.0,
        )
        log = _FakeLog()

        result = distraction.run_distraction(
            misty=object(),
            cfg=cfg,
            log=log,
            screen_pos=SimpleNamespace(yaw=18.0, pitch=-6.0),
            consume_interrupt=fake_consume,
        )

        self.assertEqual(result.outcome, "stop")
        self.assertFalse(result.sequence_completed)
        self.assertEqual(look_calls, [], "look_at_screen must NOT be called on stop outcome")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
