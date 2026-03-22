from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.session_log import SessionLog


class SessionLogTests(unittest.TestCase):
    def test_record_distraction_result_closes_event(self) -> None:
        cfg = SimpleNamespace(participant_id="wenfei")
        log = SessionLog(session_id="session_test", cfg=cfg)

        log.record_distraction_start()
        log.record_distraction_result(outcome="gaze", gaze_seen=True, latency_s=2.5)

        event = log.distraction_events[-1]
        self.assertEqual(event["exit_reason"], "gaze")
        self.assertTrue(event["gaze_detected"])
        self.assertEqual(event["gaze_latency_s"], 2.5)
        self.assertIsNotNone(event["distraction_end_time"])
        self.assertIsNotNone(event["distraction_duration_s"])


if __name__ == "__main__":
    unittest.main()