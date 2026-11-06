from __future__ import annotations

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pipeline
import stages.distraction as distraction
import utils.audio as audio
from config import Config
from utils.expressions import DISTRACTION_FACE
from utils.focus_prompt import FocusPromptResult, fallback_focus_prompt
from utils.session_log import SessionLog, local_iso, seconds_between
from utils.triggers import ExternalSignalReceiver

ORIGINAL_HEAD_REDIRECT = distraction.perform_head_redirect_only


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


class ConfigDefaultsTests(unittest.TestCase):
    def test_new_default_timings(self) -> None:
        cfg = Config(ip="127.0.0.1", openai_api_key="key", participant_id="p")

        self.assertEqual(cfg.initial_self_recovery_wait_s, 20.0)
        self.assertEqual(cfg.confused_sound_followup_wait_s, 60.0)
        self.assertEqual(cfg.head_redirect_followup_wait_s, 60.0)
        self.assertEqual(cfg.return_highlight_ms, 5000)
        self.assertEqual(cfg.recap_delay_after_return_s, 3.0)
        self.assertEqual(cfg.recap_generation_wait_s, 5.0)


class _StagedTestCase(unittest.TestCase):
    """Real SessionLog and signal queue; Misty, the extension and OpenAI are mocked.

    The on_* hooks fire at the matching point of the flow, which is how each test
    delivers a stop/start at an exact moment. A wait a test expects to be interrupted
    is set to LONG_WAIT_S, so if the interrupt were missed the test would hang visibly.
    """

    LONG_WAIT_S = 5.0

    def setUp(self) -> None:
        self.rx = ExternalSignalReceiver("127.0.0.1", 0)
        self.log = SessionLog(session_id="session_test", cfg=SimpleNamespace(participant_id="p"))
        self.addCleanup(self.rx.stop)

        self.faces = []
        self.sounds = []
        self.audio_stops = []
        self.head_runs = []
        self.arm_runs = []
        self.arm_resets = []
        self.generations = []
        self.highlights = []
        self.reading_mode_offers = []
        self.spoken = []
        self.timeline = []

        self.on_sound = lambda: None
        self.on_head = lambda stop_event: None
        self.on_arm = lambda stop_event: None
        self.on_highlight = lambda: None
        self.on_speak = lambda: None
        self.speak_succeeds = True
        self.generate = lambda text, cfg: FocusPromptResult(summary="so far", reminder="LLM recap.")

        def fake_generate(text, cfg):
            self.generations.append(text)
            return self.generate(text, cfg)

        def fake_play(misty, cfg, name, stop_event=None):
            self.sounds.append(name)
            self.timeline.append("sound")
            self.on_sound()
            return {"overall_success": True, "interrupted": False}

        def fake_head(misty, cfg, screen_pos, stop_event=None):
            self.head_runs.append(screen_pos)
            self.timeline.append("head")
            self.on_head(stop_event)

        def fake_arm(misty, cfg, repetitions=1, stop_event=None):
            self.arm_runs.append(repetitions)
            self.timeline.append("arm")
            self.on_arm(stop_event)

        def fake_show(misty, face):
            self.faces.append(face)
            self.timeline.append(face)

        def fake_stop_audio(misty):
            self.audio_stops.append(True)
            self.timeline.append("audio_stop")
            return {"overall_success": True}

        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            if payload["type"] == "highlight_current_sentence":
                self.highlights.append(payload)
                self.timeline.append("highlight")
                self.on_highlight()
            else:
                self.reading_mode_offers.append(payload)
            response = MagicMock()
            response.__enter__.return_value.read.return_value = b'{"ok": true}'
            return response

        def fake_speak(misty, utterance, voice):
            self.spoken.append(utterance)
            self.timeline.append("speak")
            self.on_speak()
            return {"overall_success": self.speak_succeeds}

        for target, attribute, replacement in (
            (distraction, "show_image", fake_show),
            (distraction, "play_audio_file", fake_play),
            (distraction, "stop_audio", fake_stop_audio),
            (distraction, "perform_head_redirect_only", fake_head),
            (distraction, "cue_screen_with_left_arm", fake_arm),
            (distraction, "reset_arms_down", lambda misty, cfg: self.arm_resets.append(True)),
            (distraction, "generate_focus_reminder_from_text", fake_generate),
            (distraction.request, "urlopen", fake_urlopen),
            (audio, "_speak_with_voice", fake_speak),
        ):
            patcher = patch.object(target, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def cfg(self, **overrides) -> SimpleNamespace:
        values = {
            "initial_self_recovery_wait_s": 0.01,
            "confused_sound_followup_wait_s": 0.01,
            "head_redirect_followup_wait_s": 0.01,
            "return_highlight_ms": 5000,
            "recap_delay_after_return_s": 0.01,
            "recap_generation_wait_s": 0.01,
            "recap_speech_words_per_s": 10000.0,
            "recap_speech_end_padding_s": 0.0,
            "speech_timeout_s": 2.0,
            "speech_voice": "Justin",
            "fatigue_offer_wait_s": 0.01,
            "fatigue_default_mode": "para",
            "fatigue_offer_enabled": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def stop_soon(self) -> None:
        timer = threading.Timer(0.05, self.rx._enqueue, args=("stop",))
        timer.daemon = True
        timer.start()

    def run_staged(self, cfg, **kwargs):
        return distraction.run_staged_distraction(
            object(),
            cfg,
            self.log,
            SimpleNamespace(yaw=45.0, pitch=0.0),
            consume_interrupt=self.rx.consume_interrupt,
            stop_event=self.rx.redirect_stop_event,
            current_text_getter=lambda: "current sentence",
            current_mode_getter=lambda: "full",
            signal_rx=self.rx,
            **kwargs,
        )

    def event(self, index: int = -1) -> dict:
        return self.log.distraction_events[index]

    def event_names(self) -> list[str]:
        return [entry["name"] for entry in self.log.events]

    def assertHighlightedOnce(self, source: str) -> None:
        self.assertEqual([(h["durationMs"], h["source"]) for h in self.highlights], [(5000, source)])
        self.assertIs(self.event()["post_return_highlight"]["sent"], True)
        self.assertIs(self.event()["post_return_highlight"]["ok"], True)
        self.assertIsNotNone(self.event()["post_return_highlight"]["time"])

    def assertNoRecap(self) -> None:
        self.assertEqual(self.spoken, [])
        self.assertEqual(self.event()["post_return_recap"]["status"], "not_applicable")
        self.assertEqual(self.log.total_voice_prompts, 0)


class StagedReturnTests(_StagedTestCase):
    def test_stage1_return_has_no_highlight_or_speech(self) -> None:
        self.rx._enqueue("stop")

        result = self.run_staged(self.cfg(initial_self_recovery_wait_s=self.LONG_WAIT_S))

        self.assertEqual(result.outcome, "stop")
        self.assertEqual(self.event()["recovered_at_stage"], "self_recovery")
        self.assertEqual(self.highlights, [])
        self.assertEqual(self.event()["post_return_highlight"], {"sent": False, "ok": False, "time": None})
        self.assertNoRecap()
        self.assertEqual(self.sounds, [])
        self.assertEqual(self.head_runs, [])

    def test_stage2_return_while_sound_plays(self) -> None:
        seen = {}

        def on_sound():
            seen["highlights_at_sound"] = len(self.highlights)
            self.rx._enqueue("stop")

        self.on_sound = on_sound

        result = self.run_staged(self.cfg(confused_sound_followup_wait_s=self.LONG_WAIT_S))

        self.assertEqual(seen["highlights_at_sound"], 0, "no highlight when the sound starts")
        self.assertEqual(result.outcome, "stop")
        self.assertEqual(self.event()["recovered_at_stage"], "sound")
        self.assertHighlightedOnce("stage2_return")
        self.assertEqual(self.audio_stops, [True])
        self.assertLess(self.timeline.index("audio_stop"), self.timeline.index("highlight"))
        self.assertNoRecap()
        self.assertEqual(self.head_runs, [])
        self.assertEqual(self.event()["exit_reason"], "stop")

    def test_stage2_return_during_wait(self) -> None:
        self.on_sound = self.stop_soon

        result = self.run_staged(self.cfg(confused_sound_followup_wait_s=self.LONG_WAIT_S))

        self.assertEqual(result.outcome, "stop")
        self.assertEqual(self.event()["recovered_at_stage"], "sound")
        self.assertHighlightedOnce("stage2_return")
        self.assertEqual(self.audio_stops, [True])
        self.assertNoRecap()
        self.assertEqual(self.head_runs, [])

    def test_stage2_shutdown_ends_without_highlight(self) -> None:
        self.on_sound = lambda: self.rx._enqueue("shutdown")

        result = self.run_staged(self.cfg(confused_sound_followup_wait_s=self.LONG_WAIT_S))

        self.assertEqual(result.outcome, "shutdown")
        self.assertEqual(self.highlights, [])
        self.assertIsNone(self.event()["recovered_at_stage"])

    def test_stage3_start_sends_no_highlight_and_generates_before_motion(self) -> None:
        generated = threading.Event()
        seen = {}
        self.generate = lambda text, cfg: generated.set() or FocusPromptResult(summary="", reminder="LLM recap.")

        def on_head(stop_event):
            seen["highlights"] = len(self.highlights)
            seen["generated"] = generated.wait(self.LONG_WAIT_S)
            self.rx._enqueue("stop")
            stop_event.wait(self.LONG_WAIT_S)

        self.on_head = on_head

        self.run_staged(self.cfg())

        self.assertEqual(seen, {"highlights": 0, "generated": True})
        self.assertEqual(self.generations, ["current sentence"])

    def test_no_return_in_stage3_goes_to_stage4_without_highlight_or_recap(self) -> None:
        result = self.run_staged(self.cfg())

        self.assertEqual(result.outcome, "sequence_complete")
        self.assertEqual(self.timeline.count("head"), 1)
        self.assertEqual(self.timeline.count("arm"), 1)
        self.assertLess(self.timeline.index("head"), self.timeline.index("arm"))
        self.assertEqual(self.highlights, [])
        self.assertEqual(self.event()["post_return_recap"]["status"], "not_applicable")
        self.assertEqual(self.log.total_voice_prompts, 0)
        self.assertEqual(len(self.reading_mode_offers), 1, "stage 4 is reached")

    def test_stage3_return_during_head_motion(self) -> None:
        seen = {}

        def on_head(stop_event):
            self.rx._enqueue("stop")
            seen["interrupted"] = stop_event.wait(self.LONG_WAIT_S)

        self.on_head = on_head

        result = self.run_staged(self.cfg())

        self.assertTrue(seen["interrupted"], "the motion must be interrupted")
        self.assertEqual(result.outcome, "stop")
        self.assertEqual(self.event()["recovered_at_stage"], "head_redirect_motion")
        self.assertHighlightedOnce("stage3_motion_return")
        self.assertNoRecap()
        self.assertEqual(self.arm_runs, [], "the arm cue must not start after the interruption")

    def test_stage3_return_during_arm_cue_lowers_the_arm(self) -> None:
        def on_arm(stop_event):
            self.rx._enqueue("stop")
            stop_event.wait(self.LONG_WAIT_S)

        self.on_arm = on_arm

        self.run_staged(self.cfg())

        self.assertEqual(self.event()["recovered_at_stage"], "head_redirect_motion")
        self.assertEqual(self.arm_resets, [True])
        self.assertHighlightedOnce("stage3_motion_return")
        self.assertNoRecap()

    def test_stage3_return_during_wait_highlights_then_speaks_after_the_delay(self) -> None:
        spoken_at = {}
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_speak = lambda: spoken_at.setdefault("time", local_iso())

        result = self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_delay_after_return_s=0.2,
        ))

        self.assertEqual(result.outcome, "stop")
        event = self.event()
        self.assertEqual(event["recovered_at_stage"], "head_redirect_wait")
        self.assertHighlightedOnce("stage3_return")
        self.assertEqual(self.spoken, ["LLM recap."])
        self.assertLess(self.timeline.index("highlight"), self.timeline.index("speak"))
        self.assertEqual(self.timeline[self.timeline.index("speak") - 1], distraction.SPEAKING_FACE)
        self.assertEqual(self.generations, ["current sentence"], "generated once, not again at speech time")
        self.assertEqual(self.timeline.count("arm"), 1, "no arm cue after the recap")

        recap = event["post_return_recap"]
        self.assertEqual(recap["status"], "played")
        self.assertIs(recap["used_fallback"], False)
        self.assertGreaterEqual(recap["delay_from_return_s"], 0.2)
        self.assertLess(recap["delay_from_return_s"], 1.0)
        self.assertGreaterEqual(
            seconds_between(event["distraction_end_time"], spoken_at["time"]), 0.2,
            "the delay is measured from the stop",
        )

        self.assertTrue(event["voice_prompt_used"])
        self.assertEqual(self.log.total_voice_prompts, 1)
        self.assertIn("no_response_prompt_generated", self.event_names())
        attempt = next(e for e in self.log.events if e["name"] == "speech_attempt")
        self.assertEqual(attempt["payload"]["stage"], "no_response_prompt")
        self.assertFalse(attempt["payload"]["interrupted"], "the latched stop must not cancel the recap")
        self.assertTrue(attempt["payload"]["success"])
        self.assertFalse(event["disengaged_during_recap"])
        self.assertIsNone(event["resumption_support_interrupted"])

    def test_start_during_recap_delay_cancels_the_recap_and_stays_queued(self) -> None:
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_highlight = lambda: self.rx._enqueue("start")

        result = self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_delay_after_return_s=self.LONG_WAIT_S,
        ))

        self.assertEqual(result.outcome, "stop")
        event = self.event()
        self.assertEqual(self.spoken, [])
        self.assertEqual(event["post_return_recap"], {
            "status": "cancelled_new_disengagement",
            "start_time": None,
            "delay_from_return_s": None,
            "used_fallback": False,
        })
        self.assertEqual(event["recovered_at_stage"], "head_redirect_wait")
        self.assertIs(event["resumption_support_interrupted"], True)
        self.assertEqual(event["resumption_support_interrupted_at"], "before_recap")
        self.assertTrue(self.rx.has_pending_start(), "the start belongs to the next event")

    def test_shutdown_during_recap_delay_cancels_the_recap(self) -> None:
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_highlight = lambda: self.rx._enqueue("shutdown")

        result = self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_delay_after_return_s=self.LONG_WAIT_S,
        ))

        self.assertEqual(result.outcome, "stop")
        self.assertEqual(self.spoken, [])
        self.assertEqual(self.event()["post_return_recap"]["status"], "cancelled_shutdown")
        self.assertEqual(self.event()["recovered_at_stage"], "head_redirect_wait")
        self.assertTrue(self.rx.has_shutdown_event(), "the pipeline still has to see the shutdown")

    def test_start_while_waiting_for_generation_cancels_the_recap(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        self.generate = lambda text, cfg: release.wait(self.LONG_WAIT_S)
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_highlight = lambda: threading.Timer(0.1, self.rx._enqueue, args=("start",)).start()

        self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_generation_wait_s=self.LONG_WAIT_S,
        ))

        self.assertEqual(self.spoken, [])
        self.assertEqual(self.event()["resumption_support_interrupted_at"], "before_recap")

    def test_start_during_speech_does_not_interrupt_the_recap(self) -> None:
        def on_speak():
            self.rx._enqueue("start")
            self.rx._enqueue("stop")

        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_speak = on_speak

        result = self.run_staged(self.cfg(head_redirect_followup_wait_s=self.LONG_WAIT_S))

        self.assertEqual(result.outcome, "stop")
        event = self.event()
        self.assertEqual(self.spoken, ["LLM recap."])
        self.assertEqual(event["post_return_recap"]["status"], "played")
        self.assertTrue(event["disengaged_during_recap"])
        self.assertIs(event["resumption_support_interrupted"], True)
        self.assertEqual(event["resumption_support_interrupted_at"], "during_recap")
        self.assertEqual(event["recovered_at_stage"], "head_redirect_wait")
        with self.rx._lock:
            self.assertEqual(list(self.rx._events), ["start", "stop"])

    def test_start_during_speech_is_logged_even_if_shutdown_follows(self) -> None:
        def on_speak():
            self.rx._enqueue("start")
            self.rx._enqueue("shutdown")

        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_speak = on_speak

        self.run_staged(self.cfg(head_redirect_followup_wait_s=self.LONG_WAIT_S))

        self.assertTrue(self.event()["disengaged_during_recap"])
        self.assertEqual(self.event()["resumption_support_interrupted_at"], "during_recap")

    def test_recap_holds_until_the_estimated_end_of_speech(self) -> None:
        started = {}
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_speak = lambda: started.setdefault("at", time.monotonic())

        self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_speech_words_per_s=10.0,
            recap_speech_end_padding_s=0.1,
        ))

        # "LLM recap." is two words: 0.2 s at 10 words/s, plus the padding.
        self.assertGreaterEqual(time.monotonic() - started["at"], 0.3)

    def test_generation_timeout_uses_the_fallback_sentence(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        self.generate = lambda text, cfg: release.wait(self.LONG_WAIT_S)
        self.on_arm = lambda stop_event: self.stop_soon()

        self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_generation_wait_s=0.1,
        ))

        event = self.event()
        self.assertEqual(self.spoken, [fallback_focus_prompt("x").reminder])
        self.assertIs(event["post_return_recap"]["used_fallback"], True)
        self.assertEqual(event["post_return_recap"]["status"], "played")
        self.assertGreaterEqual(event["post_return_recap"]["delay_from_return_s"], 0.1)
        prompt = next(e for e in self.log.events if e["name"] == "no_response_prompt_generated")
        self.assertTrue(prompt["payload"]["used_fallback"])
        self.assertEqual(prompt["payload"]["fallback_reason"], "generation_timeout")

    def test_generation_finishing_within_the_extra_wait_is_used(self) -> None:
        def slow_generate(text, cfg):
            time.sleep(0.2)
            return FocusPromptResult(summary="", reminder="Late but fine.")

        self.generate = slow_generate
        self.on_arm = lambda stop_event: self.stop_soon()

        self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_generation_wait_s=self.LONG_WAIT_S,
        ))

        self.assertEqual(self.spoken, ["Late but fine."])
        self.assertIs(self.event()["post_return_recap"]["used_fallback"], False)

    def test_generation_error_uses_the_fallback_with_its_reason(self) -> None:
        def broken_generate(text, cfg):
            raise RuntimeError("boom")

        self.generate = broken_generate
        self.on_arm = lambda stop_event: self.stop_soon()

        self.run_staged(self.cfg(head_redirect_followup_wait_s=self.LONG_WAIT_S))

        self.assertIs(self.event()["post_return_recap"]["used_fallback"], True)
        prompt = next(e for e in self.log.events if e["name"] == "no_response_prompt_generated")
        self.assertEqual(prompt["payload"]["fallback_reason"], "RuntimeError: boom")

    def test_failed_speech_is_not_logged_as_played(self) -> None:
        self.speak_succeeds = False
        self.on_arm = lambda stop_event: self.stop_soon()

        self.run_staged(self.cfg(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_speech_end_padding_s=self.LONG_WAIT_S,
        ))

        self.assertEqual(self.event()["post_return_recap"]["status"], "speech_failed")

    def test_end_time_is_the_stop_receipt_not_the_end_of_support(self) -> None:
        stop_time = {}

        def on_arm(stop_event):
            self.rx._enqueue("stop")
            with self.rx._lock:
                stop_time["received"] = self.rx._events[-1].received_at

        self.on_arm = on_arm
        start_time = local_iso()

        self.run_staged(
            self.cfg(
                head_redirect_followup_wait_s=self.LONG_WAIT_S,
                recap_delay_after_return_s=0.2,
                recap_speech_end_padding_s=0.2,
            ),
            start_time=start_time,
        )

        event = self.event()
        self.assertTrue(event["distraction_end_signal_received"])
        self.assertEqual(event["distraction_end_time"], stop_time["received"])
        self.assertEqual(event["distraction_start_time"], start_time)
        self.assertEqual(
            event["distraction_duration_s"],
            round(seconds_between(start_time, stop_time["received"]), 2),
        )
        self.assertLess(event["distraction_duration_s"], 0.2)

    def test_stage4_return_keeps_its_behaviour(self) -> None:
        self.on_highlight = lambda: self.fail("stage 4 sends no highlight")

        def speak_then_stop():
            self.stop_soon()

        self.on_speak = speak_then_stop

        result = self.run_staged(self.cfg(fatigue_offer_wait_s=self.LONG_WAIT_S))

        self.assertEqual(result.outcome, "stop")
        event = self.event()
        self.assertEqual(event["recovered_at_stage"], "fatigue_support")
        self.assertEqual(event["post_return_recap"]["status"], "not_applicable")
        self.assertEqual(self.highlights, [])
        self.assertEqual(len(self.spoken), 1, "only the fatigue offer is spoken")

    def test_failed_highlight_is_logged(self) -> None:
        def broken_urlopen(req, timeout=None):
            raise distraction.error.URLError("server down")

        self.on_sound = lambda: self.rx._enqueue("stop")

        with patch.object(distraction.request, "urlopen", broken_urlopen):
            self.run_staged(self.cfg(confused_sound_followup_wait_s=self.LONG_WAIT_S))

        highlight = self.event()["post_return_highlight"]
        self.assertIs(highlight["sent"], True)
        self.assertIs(highlight["ok"], False)


class PipelineReturnFlowTests(_StagedTestCase):
    """The same flows through pipeline.run: what happens to the next event."""

    def setUp(self) -> None:
        super().setUp()
        test = self
        self.end_of_session_after = 2

        class Receiver(ExternalSignalReceiver):
            def __new__(cls, host, port):
                return test.rx

        class Log(SessionLog):
            def __new__(cls, session_id, cfg):
                return test.log

            def __init__(self, session_id, cfg):
                pass

        original_end = self.log.record_distraction_end

        def record_end(*args, **kwargs):
            original_end(*args, **kwargs)
            if len(self.log.distraction_events) == self.end_of_session_after:
                self.rx._enqueue("shutdown")

        self.log.record_distraction_end = record_end
        self.rx.start = lambda: None

        for attribute, replacement in (
            ("ExternalSignalReceiver", Receiver),
            ("SessionLog", Log),
            ("generate_session_id", lambda participant_id: "session_test"),
            ("run_bootup", lambda misty, cfg, log: SimpleNamespace(yaw=45.0, pitch=0.0)),
            ("run_screen_watch", lambda misty, cfg, log, screen_pos: screen_pos),
            ("run_summary", lambda misty, cfg, log: None),
            ("ensure_audio_ready", lambda misty, cfg: {}),
        ):
            patcher = patch.object(pipeline, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_pipeline(self, misty=None, **overrides) -> None:
        values = {"participant_id": "p", "signal_host": "127.0.0.1", "signal_port": 0}
        values.update(overrides)
        pipeline.run(misty or object(), self.cfg(**values))

    def test_start_before_recap_becomes_a_new_event_from_stage1(self) -> None:
        def on_highlight():
            self.rx._enqueue("start")
            self.rx._enqueue("stop")

        self.on_arm = lambda stop_event: self.stop_soon() if self.arm_runs == [1] else None
        self.on_highlight = on_highlight
        self.rx._enqueue("start")

        self.run_pipeline(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_delay_after_return_s=self.LONG_WAIT_S,
        )

        first, second = self.log.distraction_events
        self.assertEqual(first["post_return_recap"]["status"], "cancelled_new_disengagement")
        self.assertEqual(first["recovered_at_stage"], "head_redirect_wait")
        self.assertEqual(first["next_disengagement_start_time"], second["distraction_start_time"])
        self.assertIsNotNone(first["time_to_next_disengagement_s"])
        self.assertGreaterEqual(first["time_to_next_disengagement_s"], 0)
        self.assertIs(first["resumption_support_interrupted"], True)
        self.assertEqual(first["resumption_support_interrupted_at"], "before_recap")

        self.assertEqual(second["recovered_at_stage"], "self_recovery")
        self.assertEqual(self.event_names().count("self_recovery_grace_started"), 2)
        self.assertEqual(self.spoken, [])
        self.assertEqual(len(self.sounds), 1, "the new event ended in stage 1, no second sound")

    def test_start_during_speech_is_processed_after_it_with_receipt_times(self) -> None:
        received = {}

        def on_speak():
            self.rx._enqueue("start")
            self.rx._enqueue("stop")
            with self.rx._lock:
                received["start"], received["stop"] = [e.received_at for e in self.rx._events]

        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_speak = on_speak
        self.rx._enqueue("start")

        self.run_pipeline(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_speech_end_padding_s=0.3,
        )

        first, second = self.log.distraction_events
        self.assertEqual(self.spoken, ["LLM recap."])
        self.assertTrue(first["disengaged_during_recap"])
        self.assertIs(first["resumption_support_interrupted"], True)
        self.assertEqual(first["resumption_support_interrupted_at"], "during_recap")
        self.assertEqual(first["next_disengagement_start_time"], received["start"])
        self.assertEqual(
            first["time_to_next_disengagement_s"],
            round(seconds_between(first["distraction_end_time"], received["start"]), 2),
        )

        self.assertEqual(second["distraction_start_time"], received["start"])
        self.assertGreaterEqual(second["processing_delayed_by_recap_s"], 0.3)
        self.assertEqual(second["recovered_at_stage"], "self_recovery")
        self.assertEqual(second["distraction_end_time"], received["stop"])
        self.assertLess(second["distraction_duration_s"], 0.1)
        self.assertEqual(self.highlights[0]["source"], "stage3_return")
        self.assertEqual(len(self.highlights), 1, "stage 1 sends no highlight")

    def test_start_during_highlight_only_support_is_backfilled(self) -> None:
        self.on_sound = lambda: self.rx._enqueue("stop")
        self.on_highlight = lambda: (self.rx._enqueue("start"), self.rx._enqueue("stop"))
        self.rx._enqueue("start")

        self.run_pipeline(confused_sound_followup_wait_s=self.LONG_WAIT_S)

        first, second = self.log.distraction_events
        self.assertEqual(first["recovered_at_stage"], "sound")
        self.assertIs(first["resumption_support_interrupted"], True)
        self.assertEqual(first["resumption_support_interrupted_at"], "during_highlight")
        self.assertEqual(second["recovered_at_stage"], "self_recovery")

    def test_next_event_after_support_finished_is_not_an_interruption(self) -> None:
        def on_highlight():
            timer = threading.Timer(0.1, lambda: (self.rx._enqueue("start"), self.rx._enqueue("stop")))
            timer.daemon = True
            timer.start()

        self.on_sound = lambda: self.rx._enqueue("stop")
        self.on_highlight = on_highlight
        self.rx._enqueue("start")

        self.run_pipeline(confused_sound_followup_wait_s=self.LONG_WAIT_S, return_highlight_ms=1)

        first = self.log.distraction_events[0]
        self.assertIs(first["resumption_support_interrupted"], False)
        self.assertIsNone(first["resumption_support_interrupted_at"])

    def test_session_end_during_recap_closes_cleanly(self) -> None:
        self.end_of_session_after = None
        self.on_arm = lambda stop_event: self.stop_soon()
        self.on_highlight = lambda: self.rx._enqueue("shutdown")
        self.rx._enqueue("start")

        self.run_pipeline(
            head_redirect_followup_wait_s=self.LONG_WAIT_S,
            recap_delay_after_return_s=self.LONG_WAIT_S,
        )

        self.assertEqual(len(self.log.distraction_events), 1)
        event = self.event()
        self.assertEqual(event["post_return_recap"]["status"], "cancelled_shutdown")
        self.assertIsNone(event["session_close_reason"], "already ended at the stop")
        self.assertIsNone(event["resumption_support_interrupted"])

    def test_head_returns_to_the_screen_exactly_once(self) -> None:
        """Real stage 3 head motion: out to the participant, then one move back."""
        self.end_of_session_after = 1
        head_moves = []

        class FakeMisty:
            def perform_action(misty, action_name, data=None):
                if action_name == "head_move":
                    head_moves.append(data["Yaw"])
                    if len(head_moves) == 1:
                        self.rx._enqueue("stop")

        with patch.object(distraction, "perform_head_redirect_only", ORIGINAL_HEAD_REDIRECT):
            self.rx._enqueue("start")
            self.run_pipeline(misty=FakeMisty())

        self.assertEqual(head_moves, [-45.0, 45.0])
        self.assertEqual(self.event()["recovered_at_stage"], "head_redirect_motion")


if __name__ == "__main__":
    unittest.main()
