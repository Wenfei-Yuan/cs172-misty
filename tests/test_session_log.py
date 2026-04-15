from __future__ import annotations

import unittest
from types import SimpleNamespace

from utils.session_log import SessionLog


class SessionLogTests(unittest.TestCase):
    def test_record_distraction_result_closes_event(self) -> None:
        cfg = SimpleNamespace(participant_id="wenfei")
        log = SessionLog(session_id="session_test", cfg=cfg)

        log.record_distraction_start()
        log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=2.5)

        event = log.distraction_events[-1]
        self.assertEqual(event["exit_reason"], "sequence_complete")
        self.assertTrue(event["sequence_completed"])
        self.assertEqual(event["completion_latency_s"], 2.5)
        self.assertIsNotNone(event["motion_end_time"])
        self.assertIsNotNone(event["motion_duration_s"])
        self.assertIsNone(event["distraction_end_time"])
        self.assertIsNone(event["distraction_duration_s"])

    def test_record_distraction_end_closes_event_after_motion_result(self) -> None:
        cfg = SimpleNamespace(participant_id="wenfei")
        log = SessionLog(session_id="session_test", cfg=cfg)

        log.record_distraction_start()
        log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=2.5)
        log.record_distraction_end()

        event = log.distraction_events[-1]
        self.assertTrue(event["distraction_end_signal_received"])
        self.assertIsNotNone(event["distraction_end_time"])
        self.assertIsNotNone(event["distraction_duration_s"])


if __name__ == "__main__":
    unittest.main()