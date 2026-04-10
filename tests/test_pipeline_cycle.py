from __future__ import annotations

import unittest
from types import SimpleNamespace

import pipeline
from stages.distraction import DistractionResult
from utils.triggers import StartSignalResult


class FakeLog:
    def __init__(self, session_id: str, cfg):
        self.session_id = session_id
        self.cfg = cfg
        self.events = []
        self.distraction_end_calls = 0

    def record(self, name: str, **payload) -> None:
        self.events.append((name, payload))

    def record_distraction_end(self) -> None:
        self.distraction_end_calls += 1


class FakeReceiver:
    def __init__(self, host: str, port: int):
        self.wait_results = [
            StartSignalResult(event="start"),
            StartSignalResult(event="start", stale_stop_events_cleared=1),
            StartSignalResult(event="shutdown"),
        ]
        self.stop_wait_results = []
        self.stop_wait_calls = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def has_shutdown_event(self) -> bool:
        return False

    def wait_for_start(self) -> StartSignalResult:
        return self.wait_results.pop(0)

    def consume_interrupt(self) -> str | None:
        return None

    def wait_for_stop_or_shutdown(self, timeout_s: float) -> str | None:
        self.stop_wait_calls.append(timeout_s)
        if self.stop_wait_results:
            return self.stop_wait_results.pop(0)
        return None

    def current_text(self) -> str:
        return "test reading text"

    def clear_redirect_stop(self) -> None:
        pass

    @property
    def redirect_stop_event(self):
        import threading
        return threading.Event()


class PipelineCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "SessionLog": pipeline.SessionLog,
            "ExternalSignalReceiver": pipeline.ExternalSignalReceiver,
            "generate_session_id": pipeline.generate_session_id,
            "run_bootup": pipeline.run_bootup,
            "run_screen_watch": pipeline.run_screen_watch,
            "run_distraction": pipeline.run_distraction,
            "run_no_response": pipeline.run_no_response,
            "run_summary": pipeline.run_summary,
            "speak_text": pipeline.speak_text,
            "ensure_audio_ready": pipeline.ensure_audio_ready,
            "redirect_attention_to_screen": pipeline.redirect_attention_to_screen,
            "cue_screen_with_left_arm": pipeline.cue_screen_with_left_arm,
        }

    def tearDown(self) -> None:
        for name, value in self.originals.items():
            setattr(pipeline, name, value)

    def test_pipeline_rearms_for_multiple_distractions(self) -> None:
        calls = {
            "distraction": 0,
            "no_response": 0,
            "summary": 0,
            "speeches": [],
        }

        pipeline.SessionLog = FakeLog
        pipeline.ExternalSignalReceiver = FakeReceiver
        pipeline.generate_session_id = lambda participant_id: "session_test"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=1.0, pitch=2.0)
        pipeline.run_screen_watch = lambda misty, cfg, log, screen_pos: screen_pos

        outcomes = [
            DistractionResult(outcome="stop"),
            DistractionResult(outcome="stop"),
        ]

        def fake_run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None):
            calls["distraction"] += 1
            return outcomes.pop(0)

        pipeline.run_distraction = fake_run_distraction
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **kwargs: calls.__setitem__("no_response", calls["no_response"] + 1)
        pipeline.run_summary = lambda misty, cfg, log: calls.__setitem__("summary", calls["summary"] + 1)
        pipeline.speak_text = lambda misty, cfg, text, **kwargs: calls["speeches"].append(text)
        pipeline.ensure_audio_ready = lambda misty, cfg: {}

        cfg = SimpleNamespace(participant_id="wenfei", signal_host="127.0.0.1", signal_port=5050, max_attempts=3)

        pipeline.run(misty=object(), cfg=cfg)

        self.assertEqual(calls["distraction"], 2)
        self.assertEqual(calls["no_response"], 0)
        self.assertEqual(calls["summary"], 1)
        self.assertEqual(calls["speeches"], [])

    def test_gaze_redirect_waits_for_stop_confirmation(self) -> None:
        calls = {
            "redirect": 0,
            "no_response": 0,
            "summary": 0,
        }

        class GazeThenStopReceiver(FakeReceiver):
            def __init__(self, host: str, port: int):
                super().__init__(host, port)
                self.wait_results = [
                    StartSignalResult(event="start"),
                    StartSignalResult(event="shutdown"),
                ]
                self.stop_wait_results = ["stop"]

        pipeline.SessionLog = FakeLog
        pipeline.ExternalSignalReceiver = GazeThenStopReceiver
        pipeline.generate_session_id = lambda participant_id: "session_test"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=1.0, pitch=2.0)
        pipeline.run_screen_watch = lambda misty, cfg, log, screen_pos: screen_pos
        pipeline.run_distraction = lambda misty, cfg, log, screen_pos, consume_interrupt=None: DistractionResult(outcome="gaze")
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **kwargs: calls.__setitem__("no_response", calls["no_response"] + 1)
        pipeline.run_summary = lambda misty, cfg, log: calls.__setitem__("summary", calls["summary"] + 1)
        pipeline.speak_text = lambda misty, cfg, text, **kwargs: None
        pipeline.ensure_audio_ready = lambda misty, cfg: {}
        pipeline.redirect_attention_to_screen = lambda misty, cfg, screen_pos, stop_event=None: calls.__setitem__("redirect", calls["redirect"] + 1)

        cfg = SimpleNamespace(
            participant_id="wenfei",
            signal_host="127.0.0.1",
            signal_port=5050,
            max_attempts=3,
            redirect_confirmation_wait_s=60.0,
        )

        pipeline.run(misty=object(), cfg=cfg)

        self.assertEqual(calls["redirect"], 1)
        self.assertEqual(calls["no_response"], 0)
        self.assertEqual(calls["summary"], 1)

    def test_gaze_redirect_prompts_once_after_timeout_then_waits_for_stop(self) -> None:
        calls = {
            "redirect": 0,
            "no_response": 0,
            "summary": 0,
        }

        class GazeThenTimeoutReceiver(FakeReceiver):
            def __init__(self, host: str, port: int):
                super().__init__(host, port)
                self.wait_results = [
                    StartSignalResult(event="start"),
                    StartSignalResult(event="shutdown"),
                ]
                self.stop_wait_results = [None, "stop"]

        pipeline.SessionLog = FakeLog
        pipeline.ExternalSignalReceiver = GazeThenTimeoutReceiver
        pipeline.generate_session_id = lambda participant_id: "session_test"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=1.0, pitch=2.0)
        pipeline.run_screen_watch = lambda misty, cfg, log, screen_pos: screen_pos
        pipeline.run_distraction = lambda misty, cfg, log, screen_pos, consume_interrupt=None: DistractionResult(outcome="gaze")
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **kwargs: calls.__setitem__("no_response", calls["no_response"] + 1)
        pipeline.run_summary = lambda misty, cfg, log: calls.__setitem__("summary", calls["summary"] + 1)
        pipeline.speak_text = lambda misty, cfg, text, **kwargs: None
        pipeline.ensure_audio_ready = lambda misty, cfg: {}
        pipeline.redirect_attention_to_screen = lambda misty, cfg, screen_pos, stop_event=None: calls.__setitem__("redirect", calls["redirect"] + 1)

        cfg = SimpleNamespace(
            participant_id="wenfei",
            signal_host="127.0.0.1",
            signal_port=5050,
            max_attempts=3,
            redirect_confirmation_wait_s=60.0,
        )

        pipeline.run(misty=object(), cfg=cfg)

        self.assertEqual(calls["redirect"], 1)
        self.assertEqual(calls["no_response"], 1)
        self.assertEqual(calls["summary"], 1)

    def test_gaze_redirect_uses_quiet_wait_after_single_prompt(self) -> None:
        calls = {
            "redirect": 0,
            "no_response": 0,
            "summary": 0,
        }
        receiver_ref = {"instance": None}

        class GazeThenReminderQuietWaitReceiver(FakeReceiver):
            def __init__(self, host: str, port: int):
                super().__init__(host, port)
                self.wait_results = [
                    StartSignalResult(event="start"),
                    StartSignalResult(event="shutdown"),
                ]
                self.stop_wait_results = [None, "stop"]
                receiver_ref["instance"] = self

        pipeline.SessionLog = FakeLog
        pipeline.ExternalSignalReceiver = GazeThenReminderQuietWaitReceiver
        pipeline.generate_session_id = lambda participant_id: "session_test"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=1.0, pitch=2.0)
        pipeline.run_screen_watch = lambda misty, cfg, log, screen_pos: screen_pos
        pipeline.run_distraction = lambda misty, cfg, log, screen_pos, consume_interrupt=None: DistractionResult(outcome="gaze")
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **kwargs: calls.__setitem__("no_response", calls["no_response"] + 1)
        pipeline.run_summary = lambda misty, cfg, log: calls.__setitem__("summary", calls["summary"] + 1)
        pipeline.speak_text = lambda misty, cfg, text, **kwargs: None
        pipeline.ensure_audio_ready = lambda misty, cfg: {}
        pipeline.redirect_attention_to_screen = lambda misty, cfg, screen_pos, stop_event=None: calls.__setitem__("redirect", calls["redirect"] + 1)

        cfg = SimpleNamespace(
            participant_id="wenfei",
            signal_host="127.0.0.1",
            signal_port=5050,
            max_attempts=3,
            redirect_confirmation_wait_s=60.0,
        )

        pipeline.run(misty=object(), cfg=cfg)

        self.assertEqual(calls["redirect"], 1)
        self.assertEqual(calls["no_response"], 1)
        self.assertEqual(calls["summary"], 1)
        self.assertEqual(receiver_ref["instance"].stop_wait_calls, [60.0, 1.0])

    def test_gaze_redirect_quiet_wait_breaks_on_shutdown(self) -> None:
        calls = {
            "redirect": 0,
            "no_response": 0,
            "summary": 0,
        }
        receiver_ref = {"instance": None}

        class GazeThenReminderShutdownReceiver(FakeReceiver):
            def __init__(self, host: str, port: int):
                super().__init__(host, port)
                self.wait_results = [
                    StartSignalResult(event="start"),
                ]
                self.stop_wait_results = [None, "shutdown"]
                receiver_ref["instance"] = self

            def has_shutdown_event(self) -> bool:
                return False

        pipeline.SessionLog = FakeLog
        pipeline.ExternalSignalReceiver = GazeThenReminderShutdownReceiver
        pipeline.generate_session_id = lambda participant_id: "session_test"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=1.0, pitch=2.0)
        pipeline.run_screen_watch = lambda misty, cfg, log, screen_pos: screen_pos
        pipeline.run_distraction = lambda misty, cfg, log, screen_pos, consume_interrupt=None: DistractionResult(outcome="gaze")
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **kwargs: calls.__setitem__("no_response", calls["no_response"] + 1)
        pipeline.run_summary = lambda misty, cfg, log: calls.__setitem__("summary", calls["summary"] + 1)
        pipeline.speak_text = lambda misty, cfg, text, **kwargs: None
        pipeline.ensure_audio_ready = lambda misty, cfg: {}
        pipeline.redirect_attention_to_screen = lambda misty, cfg, screen_pos, stop_event=None: calls.__setitem__("redirect", calls["redirect"] + 1)

        cfg = SimpleNamespace(
            participant_id="wenfei",
            signal_host="127.0.0.1",
            signal_port=5050,
            max_attempts=3,
            redirect_confirmation_wait_s=60.0,
        )

        pipeline.run(misty=object(), cfg=cfg)

        self.assertEqual(calls["redirect"], 1)
        self.assertEqual(calls["no_response"], 1)
        self.assertEqual(calls["summary"], 1)
        self.assertEqual(receiver_ref["instance"].stop_wait_calls, [60.0, 1.0])


if __name__ == "__main__":
    unittest.main()
