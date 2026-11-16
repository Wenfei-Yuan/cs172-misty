from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webcam"))

import reading_flow  # noqa: E402
from reading_flow import ReadingFlow  # noqa: E402


class FakeWorld:
    def __init__(self, control_mode: bool = False) -> None:
        self.control_mode = control_mode
        self.server_up = True
        self.calls: list[tuple] = []
        self.camera = {"webcam_connected": True, "camera_on": False, "calibrated_at": None, "error": None}
        self.pipeline_running = False
        self.control_active = False
        self.now = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)

    # injected functions
    def query_control_status(self):
        if not self.server_up:
            return {}
        return {"ok": not False, "control_mode": self.control_mode}

    def start_pipeline(self, username):
        self.calls.append(("start_pipeline", username))
        if self.pipeline_running:
            return False, "Pipeline already running"
        self.pipeline_running = True
        return True, "started"

    def stop_pipeline(self):
        self.calls.append(("stop_pipeline",))
        self.pipeline_running = False

    def camera_command(self, cmd):
        self.calls.append(("camera", cmd))
        if not self.camera["webcam_connected"]:
            return False
        if cmd == "start_camera" and not self.camera["camera_on"]:
            self.camera.update(camera_on=True, calibrated_at=None, error=None)
        if cmd == "stop_camera":
            self.camera["camera_on"] = False
        return True

    def recording_command(self, action, username="participant", ts=""):
        self.calls.append(("recording", action, username))
        return {"ok": True}

    def control_session(self, action, pid=""):
        self.calls.append(("control", action, pid))
        if action == "start":
            if self.control_active:
                return {"ok": False, "detail": "session already active"}
            self.control_active = True
            self.camera_command("start_camera")
            return {"ok": True}
        if action == "stop":
            self.control_active = False
            self.camera_command("stop_camera")
            return {"ok": True}
        return {"ok": True}

    def register(self, pid):
        self.calls.append(("register", pid))
        return {"ok": True}

    def query_camera(self):
        return dict(self.camera) if self.server_up else {}

    def calibrate(self):
        self.camera["calibrated_at"] = self.now.isoformat()


def make_flow(world: FakeWorld, quiz_urls=None):
    tmp = tempfile.mkdtemp()
    flow = ReadingFlow(
        query_control_status=world.query_control_status,
        start_pipeline=world.start_pipeline,
        stop_pipeline=world.stop_pipeline,
        camera_command=world.camera_command,
        recording_command=world.recording_command,
        control_session=world.control_session,
        register_control_participant=world.register,
        query_camera_status=world.query_camera,
        events_path=os.path.join(tmp, "sessions", "reading_events.jsonl"),
        materials_dir=os.path.join(os.path.dirname(reading_flow.__file__), "reading_materials"),
        quiz_urls=quiz_urls or {},
        clock=lambda: world.now,
    )
    return flow, os.path.join(tmp, "sessions", "reading_events.jsonl")


def wait_for(predicate, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class AriaFlowTests(unittest.TestCase):
    def setUp(self):
        self.w = FakeWorld(control_mode=False)
        self.flow, self.log = make_flow(self.w, {"aria": "https://tufts.qualtrics.com/jfe/form/SV_x"})

    def test_full_happy_path(self):
        code, d = self.flow.session_start({"participant_id": "P07", "passage": "b"})
        self.assertEqual(code, 200)
        self.assertEqual(d["redirect"], "/read/B?pid=P07")
        self.assertEqual(d["condition"], "aria")
        self.assertIn(("start_pipeline", "P07"), self.w.calls)
        self.assertFalse(self.w.camera["camera_on"])  # camera only opens on Start Reading

        code, d = self.flow.reading_start({})
        self.assertEqual(d["camera_result"], "pending")
        self.assertTrue(self.w.camera["camera_on"])
        self.assertIn(("recording", "start", "P07"), self.w.calls)

        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "pending")
        self.w.calibrate()
        _, d = self.flow.reading_status()
        self.assertEqual(d["state"], "reading")
        self.assertEqual(d["camera_result"], "success")

        _, d = self.flow.reading_end({"client_ts": "x"})
        self.assertEqual(d["state"], "ended")
        self.assertTrue(d["redirect_url"].startswith("https://tufts.qualtrics.com/jfe/form/SV_x?pid=P07&cond=aria&passage=B"))
        self.assertIn(("recording", "stop", "P07"), self.w.calls)
        self.assertIn(("camera", "stop_camera"), self.w.calls)
        self.assertTrue(wait_for(lambda: ("stop_pipeline",) in self.w.calls))

        events = [json.loads(l)["event"] for l in open(self.log)]
        self.assertEqual(events, ["session_start", "reading_start_requested", "reading_start", "reading_end"])

    def test_end_twice_only_stops_once(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.flow.reading_start({})
        self.w.calibrate()
        self.flow.reading_status()
        self.flow.reading_end({})
        _, d = self.flow.reading_end({})
        self.assertEqual(d["state"], "ended")
        wait_for(lambda: ("stop_pipeline",) in self.w.calls)
        self.assertEqual(self.w.calls.count(("camera", "stop_camera")), 1)

    def test_camera_error_shows_failed_and_allows_retry(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.flow.reading_start({})
        self.w.camera.update(error="calibration_failed", camera_on=False)
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "failed")
        self.assertEqual(d["camera_detail"], "calibration_failed")
        # retry
        _, d = self.flow.reading_start({})
        self.assertEqual(d["camera_result"], "pending")
        self.w.calibrate()
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "success")

    def test_webcam_offline_fails_immediately(self):
        self.w.camera["webcam_connected"] = False
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        _, d = self.flow.reading_start({})
        self.assertEqual(d["camera_result"], "failed")
        self.assertEqual(d["camera_detail"], "webcam_not_connected")

    def test_timeout_without_calibration(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.flow.reading_start({})
        self.w.now += timedelta(seconds=reading_flow.CAMERA_START_TIMEOUT_S + 1)
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "failed")
        self.assertEqual(d["camera_detail"], "timeout")

    def test_camera_already_on_from_researcher_counts_as_success(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.w.camera_command("start_camera")  # researcher clicked Start Camera first
        self.w.calibrate()
        self.flow.reading_start({})
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "success")

    def test_researcher_already_started_pipeline_is_ok(self):
        self.w.pipeline_running = True
        code, d = self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.assertEqual(code, 200)

    def test_second_session_blocked_until_first_ends(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        code, _ = self.flow.session_start({"participant_id": "P08", "passage": "A"})
        self.assertEqual(code, 409)
        code, _ = self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.assertEqual(code, 200)  # refresh / double click

    def test_validation(self):
        self.assertEqual(self.flow.session_start({"participant_id": "", "passage": "A"})[0], 400)
        self.assertEqual(self.flow.session_start({"participant_id": "Jane Doe", "passage": "A"})[0], 400)
        self.assertEqual(self.flow.session_start({"participant_id": "P07", "passage": "Z"})[0], 400)

    def test_server_unreachable(self):
        self.w.server_up = False
        self.assertEqual(self.flow.session_start({"participant_id": "P07", "passage": "A"})[0], 503)

    def test_no_quiz_url_goes_to_done(self):
        flow, _ = make_flow(self.w)
        flow.session_start({"participant_id": "P07", "passage": "C"})
        _, d = flow.reading_end({})
        self.assertEqual(d["redirect_url"], "/done?pid=P07&cond=aria&passage=C")


class BaselineFlowTests(unittest.TestCase):
    def setUp(self):
        self.w = FakeWorld(control_mode=True)
        self.flow, _ = make_flow(self.w)

    def test_baseline_uses_control_session(self):
        _, d = self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.assertEqual(d["condition"], "baseline")
        self.assertIn(("register", "P07"), self.w.calls)
        self.assertNotIn(("start_pipeline", "P07"), self.w.calls)
        self.flow.reading_start({})
        self.assertIn(("control", "start", "P07"), self.w.calls)
        self.w.calibrate()
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "success")
        _, d = self.flow.reading_end({})
        self.assertIn(("control", "stop", ""), self.w.calls)
        self.assertEqual(d["state"], "ended")

    def test_baseline_retry_after_failed_calibration(self):
        self.flow.session_start({"participant_id": "P07", "passage": "A"})
        self.flow.reading_start({})
        self.w.camera.update(error="calibration_failed", camera_on=False)
        self.flow.reading_status()
        _, d = self.flow.reading_start({})
        self.assertEqual(d["camera_result"], "pending")
        self.w.calibrate()
        _, d = self.flow.reading_status()
        self.assertEqual(d["camera_result"], "success")


class PageTests(unittest.TestCase):
    def test_pages_render(self):
        flow, _ = make_flow(FakeWorld())
        for p in "ABC":
            page = flow.reading_page(p)
            self.assertIn("study-reading-page", page)
            self.assertIn("<article", page)
            self.assertGreater(page.count("<p>"), 10)
        self.assertIn("no text yet", flow.reading_page("D"))
        self.assertIsNone(flow.reading_page("E"))
        self.assertIn("Participant ID", flow.baseline_page())


if __name__ == "__main__":
    unittest.main()
