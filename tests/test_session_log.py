from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from utils.session_log import SessionLog


def _at(offset_s: float) -> str:
    """A fixed timestamp offset_s after the base, so durations are exact."""
    base = datetime(2026, 11, 6, 12, 0, 0).astimezone()
    return (base + timedelta(seconds=offset_s)).isoformat()


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

    def _log(self) -> SessionLog:
        return SessionLog(session_id="session_test", cfg=SimpleNamespace(participant_id="wenfei"))

    def test_distraction_end_uses_the_given_stop_time(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(12.5), recovered_at_stage="sound")

        event = log.distraction_events[-1]
        self.assertEqual(event["distraction_end_time"], _at(12.5))
        self.assertEqual(event["distraction_duration_s"], 12.5)
        self.assertEqual(event["recovered_at_stage"], "sound")

    def test_new_event_defaults(self) -> None:
        log = self._log()
        log.record_distraction_start()

        event = log.distraction_events[-1]
        self.assertEqual(event["post_return_highlight"], {"sent": False, "ok": False, "time": None})
        self.assertEqual(
            event["post_return_recap"],
            {"status": "not_applicable", "start_time": None, "delay_from_return_s": None, "used_fallback": False},
        )
        self.assertIsNone(event["recovered_at_stage"])
        self.assertIsNone(event["next_disengagement_start_time"])
        self.assertIsNone(event["time_to_next_disengagement_s"])
        self.assertIsNone(event["resumption_support_interrupted"])
        self.assertIsNone(event["resumption_support_interrupted_at"])
        self.assertFalse(event["disengaged_during_recap"])

    def _event_with_highlight(self, log: SessionLog) -> dict:
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(30), recovered_at_stage="sound")
        log.record_post_return_highlight(sent=True, ok=True, time=_at(30), duration_ms=5000)
        return log.distraction_events[-1]

    def test_start_during_highlight_is_backfilled(self) -> None:
        log = self._log()
        previous = self._event_with_highlight(log)

        log.record_distraction_start(start_time=_at(33))

        self.assertEqual(previous["next_disengagement_start_time"], _at(33))
        self.assertEqual(previous["time_to_next_disengagement_s"], 3.0)
        self.assertIs(previous["resumption_support_interrupted"], True)
        self.assertEqual(previous["resumption_support_interrupted_at"], "during_highlight")
        self.assertFalse(previous["disengaged_during_recap"])

    def test_start_after_highlight_is_not_an_interruption(self) -> None:
        log = self._log()
        previous = self._event_with_highlight(log)

        log.record_distraction_start(start_time=_at(36))

        self.assertIs(previous["resumption_support_interrupted"], False)
        self.assertIsNone(previous["resumption_support_interrupted_at"])

    def test_no_next_disengagement_leaves_interruption_null(self) -> None:
        previous = self._event_with_highlight(self._log())

        self.assertIsNone(previous["resumption_support_interrupted"])
        self.assertIsNone(previous["next_disengagement_start_time"])

    def test_self_recovery_has_no_support_to_interrupt(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(5), recovered_at_stage="self_recovery")
        previous = log.distraction_events[-1]

        log.record_distraction_start(start_time=_at(6))

        self.assertIsNone(previous["resumption_support_interrupted"])
        self.assertEqual(previous["time_to_next_disengagement_s"], 1.0)

    def test_failed_highlight_counts_as_no_support(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(30), recovered_at_stage="sound")
        log.record_post_return_highlight(sent=True, ok=False, time=_at(30), duration_ms=5000)
        previous = log.distraction_events[-1]

        log.record_distraction_start(start_time=_at(31))

        self.assertIsNone(previous["resumption_support_interrupted"])

    def test_start_after_recap_end_is_not_an_interruption(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(30), recovered_at_stage="head_redirect_wait")
        log.record_post_return_highlight(sent=True, ok=True, time=_at(30), duration_ms=5000)
        log.record_post_return_recap(
            status="played", start_time=_at(33), delay_from_return_s=3.0, end_time=_at(50),
        )
        previous = log.distraction_events[-1]

        log.record_distraction_start(start_time=_at(51))

        self.assertIs(previous["resumption_support_interrupted"], False)
        self.assertFalse(previous["disengaged_during_recap"])

    def test_start_time_and_processing_delay(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=(datetime.now().astimezone() - timedelta(seconds=4)).isoformat())

        delay = log.distraction_events[-1]["processing_delayed_by_recap_s"]
        self.assertGreaterEqual(delay, 4.0)
        self.assertLess(delay, 5.0)

    def test_summary_counts_stages_and_interruptions(self) -> None:
        log = self._log()
        log.record_distraction_start(start_time=_at(0))
        log.record_distraction_end(end_time=_at(5), recovered_at_stage="self_recovery")
        log.record_distraction_start(start_time=_at(10))
        log.record_distraction_end(end_time=_at(30), recovered_at_stage="sound")
        log.record_post_return_highlight(sent=True, ok=True, time=_at(30), duration_ms=5000)
        log.record_distraction_start(start_time=_at(31))
        log.record_distraction_end(end_time=_at(100), recovered_at_stage="head_redirect_wait")
        log.record_post_return_recap(status="cancelled_new_disengagement")
        log.record_distraction_start(start_time=_at(101))

        summary = log.as_dict()

        self.assertEqual(summary["recovered_at_stage_counts"], {
            "self_recovery": 1,
            "sound": 1,
            "head_redirect_motion": 0,
            "head_redirect_wait": 1,
            "fatigue_support": 0,
        })
        self.assertEqual(summary["resumption_support_interrupted_count"], 2)
        self.assertEqual(summary["resumption_support_interrupted_at_counts"], {
            "during_highlight": 1,
            "before_recap": 1,
            "during_recap": 0,
        })


if __name__ == "__main__":
    unittest.main()