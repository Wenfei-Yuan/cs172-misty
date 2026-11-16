"""Participant-controlled reading flow (served by browser_bridge.py on :9877).

Flow
----
1. /baseline            participant types an ID and clicks Start.
                        ARIA condition  -> web_controller starts the robot pipeline.
                        Baseline (CONTROL_MODE server) -> the ID is registered.
                        The page then jumps to the reading page.
2. /read/<A|B|C|D>      participant opens the ARIA extension (ARIA only), then
                        clicks Start Reading -> camera opens. The page shows
                        success once participant_client reports calibration_complete,
                        or failure (camera_error / webcam offline / timeout).
3. End Reading          (after a confirm dialog) camera + recording stop, the robot
                        session ends (web_controller /api/stop, summary unchanged),
                        and the page goes to the quiz (Qualtrics URL) or /done.

The researcher tools (/researcher Start/Stop Camera, web_controller Start/Stop)
stay as a parallel channel; they send the same commands, so either side can act.

Every step is appended to sessions/reading_events.jsonl with the researcher
computer's clock.

This module has no camera / mediapipe imports so it can be unit-tested alone;
browser_bridge.py passes in the functions that talk to the other processes.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from datetime import datetime
from typing import Callable
from urllib.parse import urlencode

PASSAGES = ("A", "B", "C", "D")
CAMERA_START_TIMEOUT_S = float(os.getenv("READING_CAMERA_TIMEOUT_S", "30"))


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class ReadingFlow:
    def __init__(
        self,
        *,
        query_control_status: Callable[[], dict],
        start_pipeline: Callable[[str], tuple[bool, str]],
        stop_pipeline: Callable[[], None],
        camera_command: Callable[[str], bool],
        recording_command: Callable[..., dict],
        control_session: Callable[..., dict],
        register_control_participant: Callable[[str], dict],
        query_camera_status: Callable[[], dict],
        events_path: str,
        materials_dir: str,
        quiz_urls: dict[str, str] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._query_control_status = query_control_status
        self._start_pipeline = start_pipeline
        self._stop_pipeline = stop_pipeline
        self._camera_command = camera_command
        self._recording_command = recording_command
        self._control_session = control_session
        self._register_control_participant = register_control_participant
        self._query_camera_status = query_camera_status
        self._events_path = events_path
        self._materials_dir = materials_dir
        self._quiz_urls = quiz_urls or {}
        self._clock = clock
        self._lock = threading.RLock()
        self._s: dict | None = None  # current session

    # ── helpers ─────────────────────────────────────────────────────────
    def _log(self, event: str, **fields) -> None:
        entry = {"ts": _iso(self._clock()), "event": event}
        if self._s:
            entry.update(
                participant_id=self._s["participant_id"],
                passage=self._s["passage"],
                condition=self._s["condition"],
            )
        entry.update(fields)
        try:
            os.makedirs(os.path.dirname(self._events_path), exist_ok=True)
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[reading] could not write event log: {exc}")
        print(f"[reading] {entry}")

    def _public(self) -> dict:
        s = self._s
        if s is None:
            return {"ok": True, "state": "idle"}
        out = {
            "ok": True,
            "state": s["state"],
            "participant_id": s["participant_id"],
            "passage": s["passage"],
            "condition": s["condition"],
            "camera_result": s.get("camera_result"),
            "camera_detail": s.get("camera_detail"),
            "reading_started_at": s.get("reading_started_at"),
        }
        if s["state"] == "ended":
            out["redirect_url"] = s["redirect_url"]
        return out

    def quiz_url(self, s: dict) -> str:
        base = (self._quiz_urls.get(s["condition"]) or "").strip()
        params = {"pid": s["participant_id"], "cond": s["condition"], "passage": s["passage"]}
        if not base:
            return "/done?" + urlencode(params)
        sep = "&" if "?" in base else "?"
        return base + sep + urlencode(params)

    # ── step 1: /baseline Start ─────────────────────────────────────────
    def session_start(self, payload: dict) -> tuple[int, dict]:
        pid = str(payload.get("participant_id") or payload.get("username") or "").strip()
        passage = str(payload.get("passage") or "").strip().upper()
        if not pid:
            return 400, {"ok": False, "detail": "Please enter your participant ID."}
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", pid):
            return 400, {"ok": False, "detail": "Use letters, numbers, - or _ only (for example P07)."}
        if passage not in PASSAGES:
            return 400, {"ok": False, "detail": "No reading passage selected. Please tell the researcher."}

        with self._lock:
            s = self._s
            if s and s["state"] != "ended":
                if s["participant_id"] == pid and s["passage"] == passage:
                    # page refreshed or Start clicked twice: same session
                    return 200, {**self._public(), "redirect": self._read_url(s)}
                return 409, {"ok": False, "detail": "Another reading session is still active. Please tell the researcher."}

            status = self._query_control_status() or {}
            if "control_mode" not in status:
                return 503, {"ok": False, "detail": "Cannot reach the study server. Please tell the researcher."}
            condition = "baseline" if status.get("control_mode") else "aria"

            if condition == "aria":
                ok, detail = self._start_pipeline(pid)
                if not ok and "already running" not in (detail or "").lower():
                    return 503, {"ok": False, "detail": f"Could not start the robot ({detail}). Please tell the researcher."}
            else:
                reg = self._register_control_participant(pid) or {}
                if not reg.get("ok"):
                    return 503, {"ok": False, "detail": f"Could not register ({reg.get('detail')}). Please tell the researcher."}

            self._s = {
                "participant_id": pid,
                "passage": passage,
                "condition": condition,
                "state": "registered",
                "session_started_at": _iso(self._clock()),
            }
            self._log("session_start", source="participant_page")
            return 200, {**self._public(), "redirect": self._read_url(self._s)}

    @staticmethod
    def _read_url(s: dict) -> str:
        return f"/read/{s['passage']}?" + urlencode({"pid": s["participant_id"]})

    # ── step 2: Start Reading ───────────────────────────────────────────
    def reading_start(self, payload: dict) -> tuple[int, dict]:
        source = str(payload.get("source") or "participant_page")
        with self._lock:
            s = self._s
            if s is None or s["state"] == "ended":
                return 409, {"ok": False, "detail": "No active session. Please go back to the start page or tell the researcher."}
            if s["state"] == "reading" and s.get("camera_result") == "success":
                return 200, self._public()
            if s["state"] == "starting" and s.get("camera_result") == "pending":
                return 200, self._public()

            now = self._clock()
            if s["condition"] == "aria":
                self._recording_command("start", s["participant_id"], _iso(now))
                ok = self._camera_command("start_camera")
                detail = None if ok else "webcam_not_connected"
            else:
                res = self._control_session("start", s["participant_id"]) or {}
                ok = bool(res.get("ok"))
                detail = res.get("detail")
                if not ok and "already active" in str(detail or ""):
                    # retry after a failed calibration: session exists, reopen camera
                    ok = self._camera_command("start_camera")
                    detail = None if ok else "webcam_not_connected"

            s["start_requested_at"] = _iso(now)
            attempt = s.get("start_attempts", 0) + 1
            s["start_attempts"] = attempt
            if ok:
                s["state"] = "starting"
                s["camera_result"] = "pending"
                s["camera_detail"] = None
            else:
                s["camera_result"] = "failed"
                s["camera_detail"] = detail or "camera_start_failed"
            self._log("reading_start_requested", source=source, attempt=attempt,
                      ok=ok, detail=s["camera_detail"])
            return 200, self._public()

    # ── status (polled by the page and the extension bar) ───────────────
    def reading_status(self) -> tuple[int, dict]:
        with self._lock:
            s = self._s
            if s is not None and s["state"] == "starting" and s.get("camera_result") == "pending":
                self._resolve_camera(s)
            return 200, self._public()

    def _resolve_camera(self, s: dict) -> None:
        cam = self._query_camera_status() or {}
        requested = _parse_iso(s.get("start_requested_at"))
        now = self._clock()
        if cam.get("calibrated_at") and cam.get("camera_on"):
            s["state"] = "reading"
            s["camera_result"] = "success"
            s["reading_started_at"] = cam["calibrated_at"]
            self._log("reading_start", calibrated_at=cam["calibrated_at"],
                      requested_at=s.get("start_requested_at"))
            return
        reason = None
        if cam.get("error"):
            reason = cam["error"]
        elif cam and not cam.get("webcam_connected", True):
            reason = "webcam_not_connected"
        elif not cam:
            reason = None  # server unreachable this poll; try again
        if reason is None and requested and (now - requested).total_seconds() > CAMERA_START_TIMEOUT_S:
            reason = "timeout"
        if reason:
            s["state"] = "registered"
            s["camera_result"] = "failed"
            s["camera_detail"] = reason
            self._log("reading_start_failed", reason=reason)

    # ── step 3: End Reading ─────────────────────────────────────────────
    def reading_end(self, payload: dict) -> tuple[int, dict]:
        source = str(payload.get("source") or "participant_page")
        with self._lock:
            s = self._s
            if s is None:
                return 409, {"ok": False, "detail": "No active session."}
            if s["state"] == "ended":
                return 200, self._public()

            now = _iso(self._clock())
            warnings = []
            if s["condition"] == "aria":
                rec = self._recording_command("stop", s["participant_id"], now) or {}
                if not rec.get("ok"):
                    warnings.append(f"recording_stop: {rec.get('detail')}")
                if not self._camera_command("stop_camera"):
                    warnings.append("stop_camera failed")
                # web_controller waits for the robot to finish its summary,
                # which can take a while: do not hold the participant's page.
                threading.Thread(target=self._stop_pipeline, daemon=True).start()
            else:
                res = self._control_session("stop") or {}
                if not res.get("ok"):
                    warnings.append(f"control_session_stop: {res.get('detail')}")

            s["state"] = "ended"
            s["reading_ended_at"] = now
            s["redirect_url"] = self.quiz_url(s)
            self._log("reading_end", source=source, client_ts=payload.get("client_ts"),
                      reading_started_at=s.get("reading_started_at"), warnings=warnings)
            return 200, self._public()

    # ── pages ───────────────────────────────────────────────────────────
    def load_passage(self, passage: str) -> dict | None:
        if passage not in PASSAGES:
            return None
        path = os.path.join(self._materials_dir, f"{passage}.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def reading_page(self, passage: str) -> str | None:
        data = self.load_passage(passage)
        if data is None:
            return None
        title = html.escape(data.get("title") or f"Passage {passage}")
        paras = data.get("paragraphs") or []
        if paras:
            body = "\n".join(f"<p>{html.escape(p)}</p>" for p in paras)
        else:
            body = "<p><em>This passage has no text yet. Please tell the researcher.</em></p>"
        return READING_PAGE.replace("__TITLE__", title).replace("__BODY__", body).replace("__PASSAGE__", passage)

    @staticmethod
    def baseline_page() -> str:
        return START_PAGE

    @staticmethod
    def done_page() -> str:
        return DONE_PAGE


# ═══════════════════════════════════════════════════════════════════════════
#  Shared look for the study control bar (page and extension use the same)
# ═══════════════════════════════════════════════════════════════════════════

_BAR_CSS = r"""
  #study-controls {
    position: fixed; left: 24px; bottom: 24px; z-index: 2147483647;
    width: 230px; background: #ffffff; border: 1px solid #d9d4ca; border-radius: 12px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.15); padding: 14px 16px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #222;
  }
  #study-controls .sc-msg { font-size: 13px; line-height: 1.45; margin-bottom: 10px; min-height: 18px; }
  #study-controls .sc-ok   { color: #1e7b3a; }
  #study-controls .sc-err  { color: #b3261e; }
  #study-controls .sc-info { color: #555; }
  #study-controls button {
    width: 100%; padding: 10px; border: none; border-radius: 8px; font-size: 15px;
    font-weight: 600; cursor: pointer; color: #fff;
  }
  #study-controls button:disabled { opacity: 0.5; cursor: not-allowed; }
  #study-controls .sc-start { background: #2e7d32; }
  #study-controls .sc-end   { background: #b3261e; }
  #sc-modal-back {
    position: fixed; inset: 0; z-index: 2147483647; background: rgba(0,0,0,0.35);
    display: none; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  }
  #sc-modal { background: #fff; border-radius: 12px; padding: 24px; width: 360px; text-align: center; }
  #sc-modal p { font-size: 15px; color: #222; margin: 0 0 18px; line-height: 1.5; }
  #sc-modal button { padding: 9px 18px; border-radius: 8px; font-size: 14px; cursor: pointer; margin: 0 6px; }
  #sc-modal .sc-cancel  { border: 1px solid #ccc; background: #fff; color: #222; }
  #sc-modal .sc-confirm { border: none; background: #b3261e; color: #fff; font-weight: 600; }
"""

_BAR_HTML = r"""
<div id="study-controls">
  <div class="sc-msg sc-info" id="sc-msg">Loading…</div>
  <button class="sc-start" id="sc-start" style="display:none">Start Reading</button>
  <button class="sc-end" id="sc-end" style="display:none">End Reading</button>
</div>
<div id="sc-modal-back">
  <div id="sc-modal">
    <p>Are you sure you have finished reading?<br>You cannot go back to the passage after this.</p>
    <button class="sc-cancel" id="sc-cancel">Keep reading</button>
    <button class="sc-confirm" id="sc-confirm">Yes, end reading</button>
  </div>
</div>
"""

# The same logic runs on the page and inside the extension's shadow root.
# `root` is document or the shadow root; `api` is the bridge origin.
_BAR_JS = r"""
function studyControls(root, api, source) {
  var $ = function (id) { return root.getElementById(id); };
  var msg = $('sc-msg'), btnStart = $('sc-start'), btnEnd = $('sc-end');
  var back = $('sc-modal-back'), bar = $('study-controls');
  var busy = false, redirected = false;
  var REASONS = {
    webcam_not_connected: 'the camera program is not connected',
    camera_open_failed: 'the camera could not be opened',
    calibration_failed: 'calibration failed (please face the screen)',
    timeout: 'no response from the camera',
    camera_start_failed: 'the camera did not start'
  };
  function show(text, kind) { bar.style.display = ''; msg.style.display = ''; msg.textContent = text; msg.className = 'sc-msg sc-' + kind; }
  function hideBar() { bar.style.display = 'none'; }
  function go(url) { if (!redirected) { redirected = true; window.location.href = url; } }
  async function call(method, path, body) {
    var res = await fetch(api + path, {
      method: method, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    });
    return await res.json();
  }
  function render(d) {
    if (!d || !d.ok) return;
    if (d.state === 'ended') { show('Reading finished. Opening the questions…', 'info'); go(d.redirect_url); return; }
    if (d.state === 'idle') {
      btnStart.style.display = 'none'; btnEnd.style.display = 'none';
      show('No active session. Please go back to the start page or tell the researcher.', 'err');
      return;
    }
    if (d.camera_result === 'pending') {
      btnStart.style.display = 'block'; btnStart.disabled = true; btnEnd.style.display = 'none';
      hideBar();  // say nothing while the camera starts / calibrates
    } else if (d.camera_result === 'success') {
      btnStart.style.display = 'none'; btnEnd.style.display = 'block'; btnEnd.disabled = busy;
      bar.style.display = ''; msg.textContent = ''; msg.style.display = 'none';  // only the End Reading button
    } else if (d.camera_result === 'failed') {
      btnStart.style.display = 'block'; btnStart.disabled = busy; btnEnd.style.display = 'none';
      show('✗ Camera failed to start: ' + (REASONS[d.camera_detail] || d.camera_detail) +
           '. Try again, or ring the bell for the researcher.', 'err');
    } else {
      btnStart.style.display = 'block'; btnStart.disabled = busy; btnEnd.style.display = 'none';
      show('When you are ready, click Start Reading.', 'info');
    }
  }
  async function poll() {
    try { render(await call('GET', '/api/reading/status')); }
    catch (e) { show('Cannot reach the study server. Please ring the bell for the researcher.', 'err'); }
  }
  btnStart.addEventListener('click', async function () {
    busy = true; btnStart.disabled = true;
    hideBar();  // say nothing while the camera starts / calibrates
    try { render(await call('POST', '/api/reading/start', {source: source})); }
    catch (e) { show('✗ Could not reach the study server. Please ring the bell for the researcher.', 'err'); btnStart.disabled = false; }
    busy = false;
  });
  btnEnd.addEventListener('click', function () { back.style.display = 'flex'; });
  $('sc-cancel').addEventListener('click', function () { back.style.display = 'none'; });
  $('sc-confirm').addEventListener('click', async function () {
    back.style.display = 'none'; busy = true; btnEnd.disabled = true;
    show('Ending the session…', 'info');
    for (var i = 0; i < 3; i++) {
      try {
        var d = await call('POST', '/api/reading/end', {source: source, client_ts: new Date().toISOString()});
        if (d && d.ok) { render(d); return; }
      } catch (e) {}
      await new Promise(function (r) { setTimeout(r, 1500); });
    }
    busy = false; btnEnd.disabled = false;
    show('✗ Could not end the session. Please ring the bell for the researcher.', 'err');
  });
  poll();
  return setInterval(poll, 1500);
}
"""

READING_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
  body { margin: 0; background: #fbfaf7; color: #1a1a1a; }
  main { max-width: 720px; margin: 0 auto; padding: 48px 24px 160px; }
  article { font-family: Georgia, 'Times New Roman', serif; font-size: 19px; line-height: 1.7; }
  article h1 { font-size: 28px; line-height: 1.3; margin: 0 0 28px; }
  article p { margin: 0 0 1.2em; }
""" + _BAR_CSS + r"""
</style>
</head>
<body>
<main>
  <article id="study-article" data-passage="__PASSAGE__">
    <h1>__TITLE__</h1>
    __BODY__
  </article>
</main>
<div id="study-reading-page" data-passage="__PASSAGE__" hidden></div>
""" + _BAR_HTML + r"""
<script>
""" + _BAR_JS + r"""
studyControls(document, '', 'participant_page');
</script>
</body>
</html>
"""

START_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reading Study</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #eef1f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  .card { background: #fff; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.12);
          padding: 40px 36px; width: 420px; max-width: calc(100% - 32px); }
  h1 { font-size: 24px; margin: 0 0 8px; color: #222; }
  .sub { font-size: 14px; color: #777; margin: 0 0 28px; }
  label { display: block; font-weight: 600; font-size: 14px; color: #444; margin-bottom: 6px; }
  input, select { width: 100%; padding: 12px 14px; border: 2px solid #ddd; border-radius: 10px;
                  font-size: 16px; margin-bottom: 20px; outline: none; }
  input:focus, select:focus { border-color: #4a6cf7; }
  button { width: 100%; padding: 13px; border: none; border-radius: 10px; font-size: 17px;
           font-weight: 600; color: #fff; background: #2e7d32; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #msg { margin-top: 18px; font-size: 14px; min-height: 20px; }
  .err { color: #b3261e; } .info { color: #555; }
  #passageField { display: none; }
</style>
</head>
<body>
<div class="card">
  <h1>Reading Study</h1>
  <p class="sub">Enter your participant ID and click Start.</p>
  <label for="pid">Participant ID</label>
  <input id="pid" type="text" placeholder="e.g. P07" autocomplete="off">
  <div id="passageField">
    <label for="passage">Passage (set by the researcher)</label>
    <select id="passage">
      <option value="">— choose —</option>
      <option>A</option><option>B</option><option>C</option><option>D</option>
    </select>
  </div>
  <button id="start">Start</button>
  <div id="msg"></div>
</div>
<script>
var params = new URLSearchParams(location.search);
var fixedPassage = (params.get('passage') || '').toUpperCase();
if (!fixedPassage) document.getElementById('passageField').style.display = 'block';
function show(t, k) { var m = document.getElementById('msg'); m.textContent = t; m.className = k; }
document.getElementById('start').addEventListener('click', async function () {
  var pid = document.getElementById('pid').value.trim();
  var passage = fixedPassage || document.getElementById('passage').value;
  if (!pid) { show('Please enter your participant ID.', 'err'); return; }
  if (!passage) { show('No passage selected. Please tell the researcher.', 'err'); return; }
  this.disabled = true; show('Starting…', 'info');
  try {
    var res = await fetch('/api/session/start', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({participant_id: pid, passage: passage})
    });
    var d = await res.json();
    if (d.ok && d.redirect) { location.href = d.redirect; return; }
    show(d.detail || 'Could not start. Please tell the researcher.', 'err');
  } catch (e) { show('Cannot reach the study server. Please tell the researcher.', 'err'); }
  this.disabled = false;
});
</script>
</body>
</html>
"""

DONE_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reading finished</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #eef1f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  .card { background: #fff; border-radius: 16px; padding: 40px 36px; width: 420px; text-align: center;
          box-shadow: 0 10px 40px rgba(0,0,0,0.12); }
  h1 { font-size: 22px; margin: 0 0 12px; } p { color: #555; font-size: 15px; line-height: 1.5; }
</style></head>
<body><div class="card"><h1>Reading finished</h1>
<p>Thank you. Please let the researcher know you are done; they will give you the questions.</p></div></body>
</html>
"""

# Exported for the extension file (kept in sync by hand; see study_controls.js).
BAR_CSS = _BAR_CSS
BAR_HTML = _BAR_HTML
BAR_JS = _BAR_JS
