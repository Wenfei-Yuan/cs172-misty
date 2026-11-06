from __future__ import annotations

import unittest

from generate_csv import build_intervention_events, build_session_summary

START = "2026-11-06T12:00:00-05:00"
END = "2026-11-06T12:01:30-05:00"
HIGHLIGHT = "2026-11-06T12:01:30.100000-05:00"
NEXT_START = "2026-11-06T12:02:00-05:00"


def _event(**fields) -> dict:
    return {
        "distraction_start_time": START,
        "distraction_end_time": END,
        "distraction_duration_s": 90.0,
        "distraction_end_signal_received": True,
        "exit_reason": "stop",
        "voice_prompt_used": False,
        **fields,
    }


def _highlight_request(timestamp: str, ok: bool = True) -> dict:
    return {
        "timestamp": timestamp,
        "name": "extension_sentence_highlight_requested",
        "payload": {"ok": ok},
    }


def _row(distraction: dict, events: list[dict] | None = None) -> dict:
    session = {
        "session_id": "s",
        "participant_id": "p",
        "start_time": START,
        "end_time": NEXT_START,
        "events": events or [],
        "distraction_events": [distraction],
    }
    return build_intervention_events([session])[0]


class HighlightShownTests(unittest.TestCase):
    def test_delivered_post_return_highlight_is_shown(self) -> None:
        row = _row(_event(post_return_highlight={"sent": True, "ok": True, "time": HIGHLIGHT}))

        self.assertEqual(row["highlight_shown"], 1)
        self.assertEqual(row["highlight_shown_ts"], HIGHLIGHT)

    def test_failed_post_return_highlight_is_not_shown(self) -> None:
        row = _row(_event(post_return_highlight={"sent": True, "ok": False, "time": HIGHLIGHT}))

        self.assertEqual(row["highlight_shown"], 0)
        self.assertEqual(row["highlight_shown_ts"], "")

    def test_voice_does_not_imply_a_highlight(self) -> None:
        speech = {
            "timestamp": HIGHLIGHT,
            "name": "speech_attempt",
            "payload": {"stage": "no_response_prompt", "utterance": "Welcome back"},
        }
        row = _row(
            _event(
                voice_prompt_used=True,
                post_return_highlight={"sent": False, "ok": False, "time": None},
            ),
            [speech],
        )

        self.assertEqual(row["escalated_to_voice"], 1)
        self.assertEqual(row["highlight_shown"], 0)

    def test_highlight_does_not_require_voice(self) -> None:
        row = _row(_event(post_return_highlight={"sent": True, "ok": True, "time": HIGHLIGHT}))

        self.assertEqual(row["escalated_to_voice"], 0)
        self.assertEqual(row["highlight_shown"], 1)

    def test_older_session_reads_the_logged_request(self) -> None:
        row = _row(_event(), [_highlight_request(HIGHLIGHT)])

        self.assertEqual(row["highlight_shown"], 1)
        self.assertEqual(row["highlight_shown_ts"], HIGHLIGHT)

    def test_older_session_ignores_a_failed_request(self) -> None:
        row = _row(_event(), [_highlight_request(HIGHLIGHT, ok=False)])

        self.assertEqual(row["highlight_shown"], 0)

    def test_older_session_uses_the_first_delivered_request(self) -> None:
        first_try = "2026-11-06T12:00:30-05:00"
        delivered = "2026-11-06T12:00:50-05:00"
        later = "2026-11-06T12:01:00-05:00"
        row = _row(_event(), [
            _highlight_request(first_try, ok=False),
            _highlight_request(delivered),
            _highlight_request(later),
        ])

        self.assertEqual(row["highlight_shown_ts"], delivered)

    def test_session_summary_counts_real_highlights(self) -> None:
        session = {
            "session_id": "s",
            "participant_id": "p",
            "start_time": START,
            "end_time": NEXT_START,
            "events": [],
            "distraction_events": [
                _event(
                    voice_prompt_used=True,
                    post_return_highlight={"sent": False, "ok": False, "time": None},
                ),
                _event(post_return_highlight={"sent": True, "ok": True, "time": HIGHLIGHT}),
            ],
        }
        rows = build_intervention_events([session])

        summary = build_session_summary([session], rows)[0]

        self.assertEqual(summary["num_highlight_events"], 1)

    def test_session_without_highlight_records_is_blank_not_zero(self) -> None:
        session = {
            "session_id": "s",
            "participant_id": "p",
            "start_time": START,
            "end_time": NEXT_START,
            "events": [],
            "distraction_events": [_event()],
        }
        rows = build_intervention_events([session])

        self.assertEqual(rows[0]["highlight_shown"], "")
        self.assertEqual(rows[0]["highlight_shown_ts"], "")
        self.assertEqual(build_session_summary([session], rows)[0]["num_highlight_events"], "")

    def test_event_without_highlight_in_a_logging_session_is_zero(self) -> None:
        session = {
            "session_id": "s",
            "participant_id": "p",
            "start_time": START,
            "end_time": NEXT_START,
            "events": [_highlight_request(HIGHLIGHT)],
            "distraction_events": [
                _event(),
                _event(
                    distraction_start_time="2026-11-06T12:03:00-05:00",
                    distraction_end_time="2026-11-06T12:03:10-05:00",
                ),
            ],
        }

        rows = build_intervention_events([session])

        self.assertEqual([row["highlight_shown"] for row in rows], [1, 0])


class ResumeStageTests(unittest.TestCase):
    def _stage(self, recovered_at_stage: str, events: list[dict] | None = None, **fields) -> dict:
        fields.setdefault("distraction_duration_s", 30.0)
        return _row(_event(recovered_at_stage=recovered_at_stage, **fields), events)

    def test_self_recovery_had_no_intervention(self) -> None:
        self.assertEqual(self._stage("self_recovery")["resume_stage"], "self_recovered")

    def test_sound_and_stage3_returns_are_nonverbal(self) -> None:
        for stage in ("sound", "head_redirect_motion"):
            self.assertEqual(self._stage(stage)["resume_stage"], "resumed_after_nonverbal", stage)

    def test_recap_after_the_return_does_not_make_it_a_voice_resumption(self) -> None:
        row = self._stage("head_redirect_wait", voice_prompt_used=True)

        self.assertEqual(row["escalated_to_voice"], 1)
        self.assertEqual(row["resume_stage"], "resumed_after_nonverbal")

    def test_fatigue_return_after_the_spoken_offer_is_voice(self) -> None:
        offer = {"timestamp": HIGHLIGHT, "name": "speech_attempt", "payload": {"stage": "fatigue_support"}}

        self.assertEqual(self._stage("fatigue_support", [offer])["resume_stage"], "resumed_after_voice")

    def test_fatigue_return_without_a_spoken_offer_is_nonverbal(self) -> None:
        self.assertEqual(self._stage("fatigue_support")["resume_stage"], "resumed_after_nonverbal")

    def test_older_session_still_uses_voice_prompt_used(self) -> None:
        row = _row(_event(voice_prompt_used=True))

        self.assertEqual(row["resume_stage"], "resumed_after_voice")

    def test_voice_success_excludes_a_recap_after_the_return(self) -> None:
        session = {
            "session_id": "s",
            "participant_id": "p",
            "start_time": START,
            "end_time": NEXT_START,
            "events": [],
            "distraction_events": [
                _event(recovered_at_stage="head_redirect_wait", voice_prompt_used=True, distraction_duration_s=30.0),
            ],
        }
        rows = build_intervention_events([session])

        summary = build_session_summary([session], rows)[0]

        self.assertEqual(summary["num_voice_escalations"], 1)
        self.assertEqual(summary["num_voice_success"], 0)
        self.assertEqual(summary["num_nonverbal_only_success"], 1)


if __name__ == "__main__":
    unittest.main()
