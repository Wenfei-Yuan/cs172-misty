"""Phase 2 A/B switch: --policy graded (current) vs immediate (old study version)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import config
import pipeline
from stages.distraction import DistractionResult, StagedDistractionResult
from utils.triggers import StartSignalResult


class FakeLog:
    instances: list = []

    def __init__(self, session_id, cfg):
        self.events = []
        self.end_calls = 0
        self.closed = []
        FakeLog.instances.append(self)

    def record(self, name, **payload):
        self.events.append((name, payload))

    def record_distraction_end(self, end_time=None, recovered_at_stage=None):
        self.end_calls += 1

    def close_active_distraction(self, reason=None):
        self.closed.append(reason)

    def names(self):
        return [n for n, _ in self.events]


def make_receiver(starts, stop_waits=(), pending=None):
    class Rx:
        def __init__(self, host, port):
            self.wait_results = [StartSignalResult(event=e) for e in starts]
            self.stop_wait_results = list(stop_waits)
            self.stop_wait_calls = []
            self.redirect_stop_event = threading.Event()

        def start(self): pass
        def stop(self): pass
        def has_shutdown_event(self): return False
        def wait_for_start(self): return self.wait_results.pop(0)
        def consume_interrupt(self): return None
        def consume_stop_or_shutdown(self): return pending
        def last_stop_time(self): return None
        def current_text(self): return "text"
        def current_mode(self): return "full"
        def clear_redirect_stop(self): pass

        def wait_for_stop_or_shutdown_only(self, timeout_s):
            self.stop_wait_calls.append(timeout_s)
            return self.stop_wait_results.pop(0) if self.stop_wait_results else None

    return Rx


class ImmediatePolicyPipelineTests(unittest.TestCase):
    def setUp(self):
        FakeLog.instances = []
        self.calls = {"immediate": 0, "staged": 0, "no_response": [], "screen_watch": 0, "highlight": [], "look": 0}
        self.outcomes = []
        names = ["SessionLog", "ExternalSignalReceiver", "generate_session_id", "run_bootup", "run_screen_watch",
                 "run_distraction", "run_staged_distraction", "run_no_response", "run_summary",
                 "ensure_audio_ready", "look_at_screen", "_notify_extension_sentence_highlight"]
        self.originals = {n: getattr(pipeline, n) for n in names}
        c = self.calls
        pipeline.SessionLog = FakeLog
        pipeline.generate_session_id = lambda pid: "s"
        pipeline.run_bootup = lambda misty, cfg, log: SimpleNamespace(yaw=45.0, pitch=0.0)

        def watch(misty, cfg, log, pos):
            c["screen_watch"] += 1
            return pos

        pipeline.run_screen_watch = watch

        def immediate(misty, cfg, log, pos, consume_interrupt=None, trigger_reason=None):
            c["immediate"] += 1
            return DistractionResult(outcome=self.outcomes.pop(0))

        pipeline.run_distraction = immediate

        def staged(*a, **k):
            c["staged"] += 1
            return StagedDistractionResult(outcome="shutdown")

        pipeline.run_staged_distraction = staged
        pipeline.run_no_response = lambda misty, cfg, log, attempt, **k: c["no_response"].append(attempt)
        pipeline.run_summary = lambda misty, cfg, log: None
        pipeline.ensure_audio_ready = lambda misty, cfg: {}
        pipeline.look_at_screen = lambda misty, pos: c.__setitem__("look", c["look"] + 1)
        pipeline._notify_extension_sentence_highlight = lambda log, duration_ms, source: c["highlight"].append((duration_ms, source))

    def tearDown(self):
        for n, v in self.originals.items():
            setattr(pipeline, n, v)

    def cfg(self, policy="immediate", **kw):
        base = dict(participant_id="P07", signal_host="127.0.0.1", signal_port=5050, max_attempts=3,
                    redirect_confirmation_wait_s=60.0, intervention_policy=policy, immediate_highlight_ms=5000)
        base.update(kw)
        return SimpleNamespace(**base)

    def run_pipeline(self, cfg, rx):
        pipeline.ExternalSignalReceiver = rx
        pipeline.run(misty=object(), cfg=cfg)
        return FakeLog.instances[0]

    def test_graded_is_default_and_never_calls_immediate(self):
        cfg = self.cfg()
        del cfg.intervention_policy
        log = self.run_pipeline(cfg, make_receiver(["start"]))
        self.assertEqual(self.calls["staged"], 1)
        self.assertEqual(self.calls["immediate"], 0)
        self.assertIn(("intervention_policy", {"policy": "graded"}), log.events)

    def test_immediate_stop_returns_to_screen_and_rearms(self):
        self.outcomes = ["stop", "shutdown"]
        log = self.run_pipeline(self.cfg(), make_receiver(["start", "start"]))
        self.assertEqual(self.calls["immediate"], 2)
        self.assertEqual(self.calls["staged"], 0)
        self.assertEqual(self.calls["look"], 1)
        self.assertEqual(log.end_calls, 1)
        self.assertEqual(self.calls["screen_watch"], 1)  # skipped once after the stop
        self.assertEqual(self.calls["highlight"], [(5000, "immediate_redirect")] * 2)
        self.assertIn(("intervention_policy", {"policy": "immediate"}), log.events)
        self.assertIn("returned_to_waiting_position", log.names())

    def test_immediate_sequence_complete_then_stop_within_wait(self):
        self.outcomes = ["sequence_complete"]
        rx = make_receiver(["start", "shutdown"], stop_waits=["stop"])
        log = self.run_pipeline(self.cfg(), rx)
        self.assertEqual(self.calls["no_response"], [])
        self.assertEqual(log.end_calls, 1)
        self.assertIn("post_sequence_wait_started", log.names())

    def test_immediate_sequence_complete_no_stop_prompts_then_quiet_waits(self):
        self.outcomes = ["sequence_complete"]
        captured = {}
        rx_cls = make_receiver(["start", "shutdown"], stop_waits=[None, None, "stop"])

        class Rx(rx_cls):
            def __init__(self, h, p):
                super().__init__(h, p)
                captured["rx"] = self

        log = self.run_pipeline(self.cfg(), Rx)
        self.assertEqual(self.calls["no_response"], [1])
        self.assertEqual(captured["rx"].stop_wait_calls, [60.0, 1.0, 1.0])
        self.assertIn("post_sequence_wait_timed_out", log.names())
        self.assertEqual(log.end_calls, 1)

    def test_immediate_timeouts_never_end_session(self):
        self.outcomes = ["timeout"] * 5 + ["stop", "timeout", "shutdown"]
        log = self.run_pipeline(self.cfg(), make_receiver(["start"] * 8))
        # prompt level caps at max_attempts, then resets after the participant returns
        self.assertEqual(self.calls["no_response"], [1, 2, 3, 3, 3, 1])
        self.assertIn("max_attempts_reached", log.names())
        self.assertEqual(log.closed, ["shutdown"])  # only the participant's end closes it
        self.assertEqual(self.calls["immediate"], 8)

    def test_highlight_can_be_turned_off(self):
        self.outcomes = ["shutdown"]
        self.run_pipeline(self.cfg(immediate_highlight_ms=0), make_receiver(["start"]))
        self.assertEqual(self.calls["highlight"], [])


class ConfigPolicyTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(config.normalize_policy(None), "graded")
        self.assertEqual(config.normalize_policy(" Immediate "), "immediate")
        with self.assertRaises(ValueError):
            config.normalize_policy("fast")

    def test_default_config_is_graded(self):
        cfg = config.Config(ip="x", openai_api_key="k", participant_id="P07")
        self.assertEqual(cfg.intervention_policy, "graded")

    def test_main_parser_accepts_policy(self):
        import main
        args = main._build_parser().parse_args(["--username", "P07", "--policy", "immediate"])
        self.assertEqual(args.policy, "immediate")
        with self.assertRaises(SystemExit):
            main._build_parser().parse_args(["--policy", "bad"])


class WebControllerPolicyTests(unittest.TestCase):
    def setUp(self):
        import web_controller as wc
        self.wc = wc
        self.tmp = tempfile.mkdtemp()
        self.orig = (wc.POLICY_FILE, wc._current_policy, wc._pipeline_proc)
        wc.POLICY_FILE = os.path.join(self.tmp, "sessions", "intervention_policy.txt")
        wc._current_policy = "graded"
        wc._pipeline_proc = None

    def tearDown(self):
        self.wc.POLICY_FILE, self.wc._current_policy, self.wc._pipeline_proc = self.orig

    def test_set_policy_persists(self):
        ok, _ = self.wc._set_policy("immediate")
        self.assertTrue(ok)
        self.assertEqual(self.wc._load_policy(), "immediate")
        self.assertEqual(self.wc._status()["policy"], "immediate")
        self.assertFalse(self.wc._set_policy("bad")[0])

    def test_start_passes_policy_to_main(self):
        self.wc._set_policy("immediate")
        fake = mock.MagicMock()
        fake.poll.return_value = None
        fake.pid = 123
        with mock.patch.object(self.wc.subprocess, "Popen", return_value=fake) as popen, \
                mock.patch.object(self.wc, "_notify_bridge_recording"):
            ok, detail = self.wc._start_pipeline("P07")  # participant page sends no policy
            self.assertTrue(ok)
            argv = popen.call_args[0][0]
            self.assertEqual(argv[-4:], ["--username", "P07", "--policy", "immediate"])
            self.assertEqual(self.wc._status()["running_policy"], "immediate")
            self.wc._pipeline_proc = None
            self.wc._start_pipeline("P08", "graded")
            self.assertEqual(popen.call_args[0][0][-1], "graded")


if __name__ == "__main__":
    unittest.main()
