#!/usr/bin/env python3
"""
Browser Bridge: receives webcam frames from the Chrome extension via
a local WebSocket server, runs the IDENTICAL MediaPipe Face Mesh +
head pose + eye gaze pipeline from participant_client.py, and forwards
posture events to the main bridge server (server.py).

Usage:
    cd webcam
    python browser_bridge.py

The Chrome extension connects to ws://127.0.0.1:9876 and sends raw
JPEG frames as binary WebSocket messages.  This script decodes each
frame, feeds it through the same analyze_frame() / posture_logic used
by participant_client.py, and forwards state-change messages to the
bridge server on SERVER_IP:SERVER_PORT.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import websockets
from websockets.protocol import State as _WsState

# ---------------------------------------------------------------------------
# Import detection + posture logic from the existing webcam package
# ---------------------------------------------------------------------------
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from participant_client import (
    analyze_frame,
    smooth_angle,
    SERVER_IP,
    SERVER_PORT,
    DISENGAGE_THRESHOLD,
    REENGAGE_THRESHOLD,
    GAZE_REENGAGE_THRESHOLD,
    EVENT_CALIBRATION_COMPLETE,
    EVENT_POSTURE,
    CALIBRATION_DURATION,
    YAW_DEVIATION_THRESHOLD,
    PITCH_DEVIATION_THRESHOLD,
    REENGAGE_YAW_DEVIATION_THRESHOLD,
    REENGAGE_PITCH_DEVIATION_THRESHOLD,
    YAW_HYSTERESIS_MARGIN,
    PITCH_HYSTERESIS_MARGIN,
    POSE_SMOOTHING_ALPHA,
    REENGAGE_BREAK_TOLERANCE,
    AWAY_BREAK_TOLERANCE,
    MIN_VALID_FACE_FRAMES,
    FACE_MISSING_GRACE_PERIOD,
    POSE_INVALID_GRACE_PERIOD,
    ENABLE_EYE_GAZE,
    GAZE_SMOOTHING_ALPHA,
    GAZE_YAW_DEVIATION_THRESHOLD,
    GAZE_PITCH_DEVIATION_THRESHOLD,
    GAZE_YAW_HYSTERESIS,
    GAZE_PITCH_HYSTERESIS,
    GAZE_MIND_WANDERING_DURATION,
    GAZE_MW_BREAK_TOLERANCE,
    GAZE_HEAD_YAW_LIMIT,
)

from posture_logic import (
    compute_full_recovery,
    compute_reengage_threshold,
    compute_screen_facing,
)

# ---------------------------------------------------------------------------
# Local server config
# ---------------------------------------------------------------------------
LOCAL_HOST = "0.0.0.0"          # accept remote connections
LOCAL_PORT = int(os.getenv("BRIDGE_LOCAL_PORT", "9876"))
HTTP_PORT  = int(os.getenv("BRIDGE_HTTP_PORT", "9877"))     # participant page + control API
PREVIEW_PORT = int(os.getenv("BRIDGE_PREVIEW_PORT", "9878"))  # researcher live preview WS

# When PARTICIPANT_CLIENT_MODE=1, disable the port-9876 participant browser WS.
# Participant detection runs locally via participant_client.py instead.
PARTICIPANT_CLIENT_MODE = os.getenv("PARTICIPANT_CLIENT_MODE", "0").strip().lower() in ("1", "true", "yes")

_BRIDGE_CALIBRATION_DURATION = 12.0
_BRIDGE_MIN_CALIBRATION_SAMPLES = 5

BRIDGE_WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}"

# ---------------------------------------------------------------------------
# Baseline session state (no-robot condition)
# ---------------------------------------------------------------------------
import uuid as _uuid

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions")
_baseline_lock = threading.Lock()
_baseline_session: dict | None = None


def _gen_baseline_id(participant_id: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    return f"baseline_{date_str}_{participant_id}_{_uuid.uuid4().hex[:6]}"


def _process_baseline_events(raw_events: list, session_end_time: str) -> list:
    """Convert raw start/stop events into distraction_events list."""
    distraction_events = []
    current_start = None
    current_reason = None
    idx = 0
    for e in raw_events:
        event_type = e.get("event")
        if event_type == "start":
            current_start = e.get("ts")
            current_reason = e.get("reason")
        elif event_type == "stop" and current_start:
            idx += 1
            start_dt = datetime.fromisoformat(current_start)
            end_dt = datetime.fromisoformat(e["ts"])
            duration_s = round((end_dt - start_dt).total_seconds(), 2)
            distraction_events.append({
                "event_index": idx,
                "distraction_start_time": current_start,
                "distraction_end_time": e["ts"],
                "distraction_duration_s": duration_s,
                "distraction_end_signal_received": True,
                "exit_reason": "self_recovered",
                "trigger_source": current_reason or "unknown",
                "voice_prompt_used": False,
                "voice_prompt_count": 0,
                "gaze_detected": True,
                "gaze_latency_s": duration_s,
            })
            current_start = None
            current_reason = None
    if current_start:
        idx += 1
        start_dt = datetime.fromisoformat(current_start)
        end_dt = datetime.fromisoformat(session_end_time)
        duration_s = round((end_dt - start_dt).total_seconds(), 2)
        distraction_events.append({
            "event_index": idx,
            "distraction_start_time": current_start,
            "distraction_end_time": session_end_time,
            "distraction_duration_s": duration_s,
            "distraction_end_signal_received": False,
            "exit_reason": "session_ended",
            "trigger_source": current_reason or "unknown",
            "voice_prompt_used": False,
            "voice_prompt_count": 0,
            "gaze_detected": False,
            "gaze_latency_s": None,
        })
    return distraction_events


def _handle_baseline_start(payload: dict) -> tuple[int, dict]:
    global _baseline_session
    username = payload.get("username", "").strip()
    if not username:
        return 400, {"ok": False, "detail": "username required"}
    with _baseline_lock:
        if _baseline_session:
            return 409, {"ok": False, "detail": "Session already active"}
        sid = _gen_baseline_id(username)
        _baseline_session = {
            "session_id": sid,
            "participant_id": username,
            "start_time": datetime.now().astimezone().isoformat(),
        }
    print(f"[baseline] Session started: {sid} (participant={username})")
    _request_recording_start(username)
    return 200, {"ok": True, "session_id": sid}


def _handle_baseline_stop(payload: dict) -> tuple[int, dict]:
    global _baseline_session
    raw_events = payload.get("events", [])
    with _baseline_lock:
        if not _baseline_session:
            return 404, {"ok": False, "detail": "No active session"}
        session = dict(_baseline_session)
        _baseline_session = None
    end_time = datetime.now().astimezone().isoformat()
    events = []
    for e in raw_events:
        ev_name = "disengagement_start" if e.get("event") == "start" else "disengagement_end"
        entry = {"timestamp": e.get("ts", ""), "name": ev_name, "payload": {}}
        if e.get("reason"):
            entry["payload"]["reason"] = e["reason"]
        events.append(entry)
    distraction_events = _process_baseline_events(raw_events, end_time)
    session_data = {
        "session_id": session["session_id"],
        "participant_id": session["participant_id"],
        "condition": "no_system",
        "start_time": session["start_time"],
        "end_time": end_time,
        "events": events,
        "distraction_events": distraction_events,
        "total_distraction_count": len(distraction_events),
        "total_voice_prompts": 0,
    }
    os.makedirs(_SESSIONS_DIR, exist_ok=True)
    fpath = os.path.join(_SESSIONS_DIR, f"{session['session_id']}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False)
    print(f"[baseline] Session saved: {fpath}  ({len(distraction_events)} distractions)")
    _request_recording_stop()
    return 200, {"ok": True, "session_id": session["session_id"], "num_distractions": len(distraction_events)}


def decode_jpeg_frame(data: bytes) -> np.ndarray | None:
    buf = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return frame


def _json_default(obj):
    """json.dumps fallback for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ═══════════════════════════════════════════════════════════════════════════
#  Shared state — accessed by asyncio handlers + HTTP thread
# ═══════════════════════════════════════════════════════════════════════════
_event_loop: asyncio.AbstractEventLoop | None = None
_participant_ws: websockets.WebSocketServerProtocol | None = None
_participant_lock = threading.Lock()
_camera_status = "disconnected"   # disconnected | connected | streaming
_preview_clients: set[websockets.WebSocketServerProtocol] = set()


# ═══════════════════════════════════════════════════════════════════════════
#  Video recording — saves session video for offline review
# ═══════════════════════════════════════════════════════════════════════════
_RECORDINGS_DIR = os.path.normpath(os.path.join(_dir, "..", "recordings"))
_REC_NOMINAL_FPS = 15

_rec_lock = threading.Lock()
_rec_pending_start: dict | None = None
_rec_pending_stop: bool = False
_rec_writer: cv2.VideoWriter | None = None
_rec_active: bool = False
_rec_start_ts: str = ""
_rec_frame_count: int = 0
_rec_filename: str = ""
_rec_username: str = ""
_rec_resolution: tuple[int, int] = (0, 0)


def _request_recording_start(username: str) -> dict:
    global _rec_pending_start
    with _rec_lock:
        if _rec_active:
            return {"ok": False, "detail": "Already recording"}
        _rec_pending_start = {"username": username}
    return {"ok": True, "detail": "Recording start requested"}


def _request_recording_stop() -> dict:
    global _rec_pending_stop
    with _rec_lock:
        if not _rec_active:
            return {"ok": False, "detail": "Not recording"}
        _rec_pending_stop = True
    return {"ok": True, "detail": "Recording stop requested"}


def _do_start_recording(username: str, frame: np.ndarray) -> None:
    """Create VideoWriter on first frame after start is requested."""
    global _rec_writer, _rec_active, _rec_start_ts, _rec_frame_count
    global _rec_filename, _rec_username, _rec_resolution

    os.makedirs(_RECORDINGS_DIR, exist_ok=True)

    now = datetime.now(timezone.utc).astimezone()
    _rec_start_ts = now.isoformat()
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    _rec_filename = f"recording_{ts_str}_{username}.avi"
    _rec_username = username
    _rec_frame_count = 0

    h, w = frame.shape[:2]
    _rec_resolution = (w, h)

    path = os.path.join(_RECORDINGS_DIR, _rec_filename)
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    _rec_writer = cv2.VideoWriter(path, fourcc, _REC_NOMINAL_FPS, (w, h))

    if _rec_writer.isOpened():
        _rec_active = True
        print(f"[Recording] Started: {_rec_filename} ({w}x{h} @ {_REC_NOMINAL_FPS}fps nominal)")
    else:
        print(f"[Recording] ERROR: failed to open VideoWriter for {path}")
        _rec_writer = None


def _do_stop_recording() -> None:
    """Release VideoWriter and save metadata JSON alongside the video."""
    global _rec_writer, _rec_active

    if _rec_writer is None:
        _rec_active = False
        return

    _rec_writer.release()
    _rec_writer = None
    _rec_active = False

    end_ts = datetime.now(timezone.utc).astimezone().isoformat()

    meta = {
        "video_file": _rec_filename,
        "username": _rec_username,
        "video_start_ts": _rec_start_ts,
        "video_end_ts": end_ts,
        "total_frames": _rec_frame_count,
        "nominal_fps": _REC_NOMINAL_FPS,
        "resolution": list(_rec_resolution),
    }

    meta_path = os.path.join(_RECORDINGS_DIR, _rec_filename.replace(".avi", ".json"))
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[Recording] Stopped: {_rec_filename} ({_rec_frame_count} frames)")
    print(f"[Recording] Metadata saved: {meta_path}")


def _check_and_handle_recording_bytes(data: bytes) -> None:
    """Decode raw JPEG and write to video. Called for EVERY received frame."""
    global _rec_pending_start, _rec_pending_stop, _rec_frame_count

    with _rec_lock:
        start_cmd = _rec_pending_start
        stop_cmd = _rec_pending_stop
        _rec_pending_start = None
        _rec_pending_stop = False

    frame = None

    if stop_cmd and _rec_active:
        _do_stop_recording()

    if start_cmd is not None and not _rec_active:
        frame = decode_jpeg_frame(data)
        if frame is not None:
            _do_start_recording(start_cmd["username"], frame)

    if _rec_active and _rec_writer is not None:
        if frame is None:
            frame = decode_jpeg_frame(data)
        if frame is not None:
            _rec_writer.write(frame)
            _rec_frame_count += 1


def _send_to_participant(cmd: dict) -> bool:
    """Thread-safe: send JSON command to the connected participant page."""
    with _participant_lock:
        loop = _event_loop
        ws = _participant_ws
    if loop is None or ws is None:
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(ws.send(json.dumps(cmd)), loop)
        fut.result(timeout=5)
        return True
    except Exception:
        return False


def _send_camera_via_server(cmd_type: str) -> bool:
    """Relay camera command through server.py when no local participant WS."""
    try:
        from websockets.sync.client import connect as ws_sync_connect
        with ws_sync_connect(BRIDGE_WS_URL, open_timeout=3) as ws:
            ws.send(json.dumps({"type": cmd_type}))
            resp = ws.recv(timeout=3)
            result = json.loads(resp)
            return result.get("ok", False)
    except Exception as e:
        print(f"[CameraHTTP] Failed to relay {cmd_type} via server: {e}")
        return False


def _send_recording_via_server(action: str, username: str = "participant") -> dict:
    """Relay recording start/stop to participant_client via server.py WebSocket."""
    try:
        from websockets.sync.client import connect as ws_sync_connect
        if action == "start":
            msg = json.dumps({"type": "recording_start", "username": username})
        else:
            msg = json.dumps({"type": "recording_stop"})
        with ws_sync_connect(BRIDGE_WS_URL, open_timeout=3) as ws:
            ws.send(msg)
            resp = ws.recv(timeout=3)
            result = json.loads(resp)
            ok = result.get("ok", False)
            return {"ok": ok, "detail": f"Recording {action} relayed via server" if ok else result.get("reason", "relay failed")}
    except Exception as e:
        print(f"[RecordingHTTP] Failed to relay recording_{action} via server: {e}")
        return {"ok": False, "detail": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
#  Participant HTML page — camera is HIDDEN, controlled by researcher
# ═══════════════════════════════════════════════════════════════════════════
PARTICIPANT_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Study Session</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f5f5; color: #333;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .card {
    background: #fff; border-radius: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 48px 40px; width: 380px; text-align: center;
  }
  .icon { font-size: 48px; margin-bottom: 12px; }
  h1 { font-size: 22px; margin-bottom: 8px; }
  #status {
    font-size: 15px; color: #888; margin-top: 16px;
    padding: 10px; border-radius: 8px; background: #f0f0f0;
  }
  .dot {
    display: inline-block; width: 10px; height: 10px;
    border-radius: 50%; margin-right: 6px; vertical-align: middle;
  }
  .dot-green  { background: #2ecc71; }
  .dot-yellow { background: #f39c12; }
  .dot-red    { background: #e74c3c; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#x1F4D6;</div>
  <h1>Study Session Active</h1>
  <p style="color:#888;font-size:13px;margin-bottom:12px;">Please keep this tab open during the study.</p>
  <div id="status"><span class="dot dot-yellow"></span>Connecting...</div>
</div>
<!-- Hidden camera elements -->
<video id="v" style="display:none" autoplay muted playsinline></video>
<canvas id="c" style="display:none"></canvas>
<script>
var WS_PORT = __WS_PORT__;
var WS_URL = 'ws://' + location.hostname + ':' + WS_PORT;
var ws, stream, timer, streaming = false;
var video = document.getElementById('v');
var canvas = document.getElementById('c');
var ctx = canvas.getContext('2d');

function setStatus(dot, text) {
  document.getElementById('status').innerHTML =
    '<span class="dot dot-' + dot + '"></span>' + text;
}

function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';
  ws.onopen = function() {
    setStatus('green', 'Connected &mdash; ready');
    ws.send(JSON.stringify({type: 'hello', role: 'participant'}));
  };
  ws.onmessage = function(e) {
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'start_camera') startCamera();
      else if (msg.type === 'stop_camera') stopCamera();
    } catch(_) {}
  };
  ws.onclose = function() {
    setStatus('red', 'Disconnected &mdash; reconnecting...');
    stopCamera();
    setTimeout(connect, 3000);
  };
  ws.onerror = function() {};
}

async function startCamera() {
  if (streaming) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: {ideal: 640}, height: {ideal: 480} },
      audio: false
    });
    video.srcObject = stream;
    await video.play();
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    streaming = true;
    setStatus('green', 'Session in progress...');
    sendFrames();
  } catch(err) {
    setStatus('red', 'Camera error: ' + err.message);
  }
}

function stopCamera() {
  streaming = false;
  if (timer) { clearTimeout(timer); timer = null; }
  if (stream) {
    stream.getTracks().forEach(function(t) { t.stop(); });
    stream = null;
  }
  video.srcObject = null;
  if (ws && ws.readyState === WebSocket.OPEN)
    setStatus('green', 'Connected &mdash; ready');
}

function sendFrames() {
  if (!streaming) return;
  ctx.drawImage(video, 0, 0);
  canvas.toBlob(function(blob) {
    if (blob && ws && ws.readyState === WebSocket.OPEN)
      blob.arrayBuffer().then(function(buf) { ws.send(new Uint8Array(buf)); });
    timer = setTimeout(sendFrames, 33);
  }, 'image/jpeg', 0.85);
}

connect();
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Baseline page — camera + session management combined (served at /baseline)
# ═══════════════════════════════════════════════════════════════════════════

BASELINE_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reading Study — Baseline</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #74b9ff 0%, #a29bfe 100%);
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }
  .card {
    background: #fff; border-radius: 20px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    padding: 48px 40px; width: 440px; text-align: center;
  }
  .card h1 { font-size: 26px; color: #333; margin-bottom: 8px; }
  .card .subtitle { font-size: 14px; color: #888; margin-bottom: 32px; }
  .icon { font-size: 56px; margin-bottom: 12px; }
  label { display: block; text-align: left; font-weight: 600; color: #555; margin-bottom: 6px; font-size: 14px; }
  input[type="text"] {
    width: 100%; padding: 12px 16px; border: 2px solid #ddd;
    border-radius: 10px; font-size: 16px; outline: none;
    transition: border-color 0.2s; margin-bottom: 24px;
  }
  input[type="text"]:focus { border-color: #74b9ff; }
  .btn {
    width: 100%; padding: 14px; border: none; border-radius: 10px;
    font-size: 17px; font-weight: 600; cursor: pointer;
    transition: transform 0.1s, box-shadow 0.2s; margin-bottom: 12px;
  }
  .btn:active { transform: scale(0.98); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-start { background: linear-gradient(135deg, #43e97b, #38f9d7); color: #fff; box-shadow: 0 4px 15px rgba(67,233,123,0.4); }
  .btn-stop  { background: linear-gradient(135deg, #f093fb, #f5576c); color: #fff; box-shadow: 0 4px 15px rgba(245,87,108,0.4); }
  #status { margin-top: 20px; padding: 12px; border-radius: 10px; font-size: 14px; display: none; }
  .status-ok   { background: #e6ffed; color: #27ae60; display: block !important; }
  .status-err  { background: #ffeaea; color: #e74c3c; display: block !important; }
  .status-info { background: #eef2ff; color: #667eea; display: block !important; }
  .divider { border: none; border-top: 2px solid #eee; margin: 28px 0 20px; }
  #camStatus { padding: 10px; border-radius: 10px; font-size: 13px; display: block; background: #f0f0f0; color: #888; }
  .cam-ok   { background: #e6ffed !important; color: #27ae60 !important; }
  .cam-info { background: #eef2ff !important; color: #667eea !important; }
  .cam-err  { background: #ffeaea !important; color: #e74c3c !important; }
  #serverStatus { margin-top: 10px; padding: 10px; border-radius: 10px; font-size: 13px; background: #f0f0f0; color: #888; }
  .srv-ok  { background: #e6ffed !important; color: #27ae60 !important; }
  .srv-err { background: #ffeaea !important; color: #e74c3c !important; }
  #eventLog { margin-top: 10px; padding: 10px; border-radius: 10px; font-size: 13px; background: #f9f9f9; color: #555; }
  .timer { font-size: 22px; font-weight: 700; color: #333; margin: 16px 0; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">📖</div>
  <h1>Reading Study</h1>
  <p class="subtitle">Baseline — click Start to begin reading</p>

  <label for="username">Your Name</label>
  <input type="text" id="username" placeholder="e.g. participant1" autocomplete="off">

  <button class="btn btn-start" id="btnStart" onclick="startSession()">▶ Start Reading</button>
  <button class="btn btn-stop" id="btnStop" onclick="stopSession()" disabled>■ End Reading</button>

  <div id="timer" class="timer" style="display:none">00:00</div>
  <div id="status"></div>
  <hr class="divider">
  <div id="camStatus">📷 Initializing…</div>
  <div id="serverStatus">🔗 Connecting to detection server…</div>
  <div id="eventLog"></div>
</div>

<video id="camVideo" style="display:none" autoplay muted playsinline></video>
<canvas id="camCanvas" style="display:none"></canvas>

<script>
var CAM_WS_PORT    = __WS_PORT__;
var SERVER_WS_PORT = 8765;

var sessionId = null, sessionEvents = [], timerInterval = null, sessionStart = null;
var camWs = null, camStream = null, camTimer = null, camStreaming = false;
var camVideo  = document.getElementById('camVideo');
var camCanvas = document.getElementById('camCanvas');
var camCtx    = camCanvas.getContext('2d');
var serverWs = null;

function setStatus(msg, type) {
  var el = document.getElementById('status');
  el.textContent = msg; el.className = 'status-' + type;
}
function setCamStatus(msg, type) {
  var el = document.getElementById('camStatus');
  el.textContent = msg; el.className = type ? ('cam-' + type) : '';
}
function setServerStatus(msg, type) {
  var el = document.getElementById('serverStatus');
  el.textContent = msg; el.className = type ? ('srv-' + type) : '';
}
function updateEventLog() {
  var el = document.getElementById('eventLog');
  if (!sessionId) { el.textContent = ''; return; }
  var starts = sessionEvents.filter(function(e){ return e.event === 'start'; }).length;
  var stops  = sessionEvents.filter(function(e){ return e.event === 'stop'; }).length;
  el.textContent = 'Distractions detected: ' + starts + '  |  Re-engaged: ' + stops;
}
function updateTimer() {
  if (!sessionStart) return;
  var elapsed = Math.floor((Date.now() - sessionStart) / 1000);
  var m = String(Math.floor(elapsed / 60)).padStart(2, '0');
  var s = String(elapsed % 60).padStart(2, '0');
  document.getElementById('timer').textContent = m + ':' + s;
}

/* ── Camera ──────────────────────────────────────────── */
function connectBridge() {
  var wsUrl = 'ws://' + location.hostname + ':' + CAM_WS_PORT;
  setCamStatus('📷 Connecting to camera bridge…', 'info');
  camWs = new WebSocket(wsUrl);
  camWs.binaryType = 'arraybuffer';
  camWs.onopen = function() {
    camWs.send(JSON.stringify({type: 'hello', role: 'participant'}));
    setCamStatus('📷 Bridge connected — camera ready', 'ok');
  };
  camWs.onmessage = function(e) {
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'start_camera') startCamera();
      else if (msg.type === 'stop_camera') stopCamera();
    } catch(_) {}
  };
  camWs.onclose = function(ev) {
    stopCamera();
    setCamStatus('📷 Bridge disconnected — reconnecting…', 'err');
    setTimeout(connectBridge, 3000);
  };
  camWs.onerror = function() { setCamStatus('📷 Bridge connection error', 'err'); };
}

async function startCamera() {
  if (camStreaming) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setCamStatus('📷 Camera blocked — open page via HTTPS or localhost', 'err');
    return;
  }
  setCamStatus('📷 Opening camera…', 'info');
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: {ideal: 640}, height: {ideal: 480} },
      audio: false
    });
    camVideo.srcObject = camStream;
    await camVideo.play();
    camCanvas.width  = camVideo.videoWidth;
    camCanvas.height = camVideo.videoHeight;
    camStreaming = true;
    setCamStatus('📷 Camera active — streaming (' + camVideo.videoWidth + 'x' + camVideo.videoHeight + ')', 'ok');
    sendCamFrames();
  } catch(err) {
    setCamStatus('📷 Camera error: ' + err.name + ' — ' + err.message, 'err');
  }
}

function stopCamera() {
  camStreaming = false;
  if (camTimer) { clearTimeout(camTimer); camTimer = null; }
  if (camStream) { camStream.getTracks().forEach(function(t){ t.stop(); }); camStream = null; }
  camVideo.srcObject = null;
}

function sendCamFrames() {
  if (!camStreaming) return;
  camCtx.drawImage(camVideo, 0, 0);
  camCanvas.toBlob(function(blob) {
    if (blob && camWs && camWs.readyState === WebSocket.OPEN)
      blob.arrayBuffer().then(function(buf){ camWs.send(new Uint8Array(buf)); });
    camTimer = setTimeout(sendCamFrames, 33);
  }, 'image/jpeg', 0.85);
}

/* ── Detection server WebSocket ──────────────────────── */
function connectServer() {
  var wsUrl = 'ws://' + location.hostname + ':' + SERVER_WS_PORT;
  setServerStatus('🔗 Connecting…', '');
  serverWs = new WebSocket(wsUrl);
  serverWs.onopen = function() {
    serverWs.send(JSON.stringify({client: 'baseline'}));
    setServerStatus('🔗 Detection server connected', 'ok');
  };
  serverWs.onmessage = function(e) {
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'baseline_event' && sessionId) {
        sessionEvents.push(msg);
        updateEventLog();
      }
    } catch(_) {}
  };
  serverWs.onclose = function() {
    setServerStatus('🔗 Disconnected — reconnecting…', 'err');
    setTimeout(connectServer, 3000);
  };
  serverWs.onerror = function() { setServerStatus('🔗 Connection error', 'err'); };
}

/* ── Session control ─────────────────────────────────── */
async function startSession() {
  var username = document.getElementById('username').value.trim();
  if (!username) { setStatus('Please enter your name', 'err'); return; }

  // Open camera FIRST (needs user-gesture context for permission prompt)
  if (!camStreaming) {
    await startCamera();
  }

  setStatus('Starting session…', 'info');
  document.getElementById('btnStart').disabled = true;

  try {
    var res = await fetch('/api/baseline/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username})
    });
    var data = await res.json();
    if (data.ok) {
      sessionId = data.session_id;
      sessionEvents = [];
      sessionStart = Date.now();
      setStatus('Session active — focus on your reading!', 'ok');
      document.getElementById('btnStop').disabled = false;
      document.getElementById('username').disabled = true;
      document.getElementById('timer').style.display = 'block';
      timerInterval = setInterval(updateTimer, 1000);
      updateEventLog();
    } else {
      setStatus('Error: ' + data.detail, 'err');
      document.getElementById('btnStart').disabled = false;
    }
  } catch(e) {
    setStatus('Request failed: ' + e.message, 'err');
    document.getElementById('btnStart').disabled = false;
  }
}

async function stopSession() {
  setStatus('Ending session…', 'info');
  document.getElementById('btnStop').disabled = true;
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }

  stopCamera();

  try {
    var res = await fetch('/api/baseline/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, events: sessionEvents})
    });
    var data = await res.json();
    if (data.ok) {
      setStatus('Session ended! Distractions: ' + data.num_distractions, 'ok');
    } else {
      setStatus('Error: ' + data.detail, 'err');
      document.getElementById('btnStop').disabled = false;
    }
  } catch(e) {
    setStatus('Request failed: ' + e.message, 'err');
    document.getElementById('btnStop').disabled = false;
  }

  sessionId = null; sessionEvents = []; sessionStart = null;
  setTimeout(function() {
    document.getElementById('btnStart').disabled = false;
    document.getElementById('username').disabled = false;
    document.getElementById('timer').style.display = 'none';
    document.getElementById('eventLog').textContent = '';
  }, 2000);
}

/* ── Boot ─────────────────────────────────────────────── */
connectBridge();
connectServer();
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP server — participant page + camera control API
# ═══════════════════════════════════════════════════════════════════════════
from http.server import BaseHTTPRequestHandler, HTTPServer


# ═══════════════════════════════════════════════════════════════════════════
#  Researcher control page — camera start/stop + live preview
# ═══════════════════════════════════════════════════════════════════════════
RESEARCHER_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Researcher — Camera Control</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    color: #e0e0e0;
  }
  .card {
    background: rgba(255,255,255,0.07); backdrop-filter: blur(10px);
    border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    padding: 36px 32px; width: 500px; text-align: center;
    border: 1px solid rgba(255,255,255,0.1);
  }
  h1 { font-size: 24px; margin-bottom: 6px; }
  .sub { font-size: 13px; color: #aaa; margin-bottom: 24px; }
  .btn {
    width: 48%; padding: 14px; border: none; border-radius: 10px;
    font-size: 16px; font-weight: 600; cursor: pointer;
    transition: transform 0.1s, opacity 0.2s; margin: 0 1%;
  }
  .btn:active { transform: scale(0.97); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
  .btn-start {
    background: linear-gradient(135deg, #43e97b, #38f9d7); color: #fff;
  }
  .btn-stop {
    background: linear-gradient(135deg, #f093fb, #f5576c); color: #fff;
  }
  #status {
    margin-top: 16px; padding: 10px; border-radius: 8px;
    font-size: 14px; background: rgba(255,255,255,0.05);
  }
  .dot {
    display: inline-block; width: 10px; height: 10px;
    border-radius: 50%; margin-right: 6px; vertical-align: middle;
  }
  .dot-green  { background: #2ecc71; }
  .dot-yellow { background: #f39c12; }
  .dot-red    { background: #e74c3c; }
  .dot-gray   { background: #888; }
  #previewCanvas {
    display: none; margin-top: 16px; width: 100%;
    border-radius: 12px; border: 2px solid rgba(255,255,255,0.15);
    background: #000;
  }
  .no-preview {
    margin-top: 16px; padding: 40px; border-radius: 12px;
    background: rgba(0,0,0,0.3); color: #666; font-size: 14px;
  }
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F3A5; Camera Control</h1>
  <p class="sub">Control the participant's webcam remotely</p>

  <button class="btn btn-start" id="btnStart" onclick="camStart()">&#x25B6; Start Camera</button>
  <button class="btn btn-stop" id="btnStop" onclick="camStop()" disabled>&#x23F9; Stop Camera</button>

  <div id="status"><span class="dot dot-gray"></span>Checking connection...</div>
  <div id="previewContainer" style="position:relative; display:none; margin-top:16px;">
    <canvas id="previewCanvas" width="640" height="480" style="width:100%; border-radius:12px; border:2px solid rgba(255,255,255,0.15); background:#000; display:block;"></canvas>
    <div id="overlay" style="position:absolute; top:0; left:0; right:0; padding:8px 10px; font-family:monospace; font-size:11px; color:#0f0; background:rgba(0,0,0,0.45); border-radius:12px 12px 0 0; pointer-events:none; white-space:pre-line; line-height:1.5;"></div>
  </div>
  <div class="no-preview" id="noPreview">No preview &mdash; camera not started</div>
</div>

<script>
var CAM_API = location.origin;
var PREVIEW_WS = 'ws://' + location.hostname + ':' + __PREVIEW_PORT__;
var previewWs = null;
var statusTimer = null;

function setStatus(dot, text) {
  document.getElementById('status').innerHTML =
    '<span class="dot dot-' + dot + '"></span>' + text;
}

async function pollStatus() {
  try {
    var res = await fetch(CAM_API + '/api/camera/status');
    var d = await res.json();
    if (!d.connected) {
      setStatus('red', 'Participant not connected');
    } else if (d.status === 'streaming') {
      setStatus('green', 'Camera streaming');
    } else {
      setStatus('yellow', 'Participant connected &mdash; camera idle');
    }
  } catch(e) {
    setStatus('red', 'Cannot reach bridge server');
  }
}

async function camStart() {
  setStatus('yellow', 'Starting camera...');
  try {
    var res = await fetch(CAM_API + '/api/camera/start', {method:'POST'});
    var d = await res.json();
    if (d.ok) {
      setStatus('green', 'Camera streaming');
      document.getElementById('btnStart').disabled = true;
      document.getElementById('btnStop').disabled = false;
      startPreview();
    } else {
      setStatus('red', 'Failed &mdash; is participant page open?');
    }
  } catch(e) {
    setStatus('red', 'Error: ' + e.message);
  }
}

async function camStop() {
  try { await fetch(CAM_API + '/api/camera/stop', {method:'POST'}); } catch(e) {}
  setStatus('yellow', 'Camera stopped');
  document.getElementById('btnStart').disabled = false;
  document.getElementById('btnStop').disabled = true;
  stopPreview();
}

function startPreview() {
  stopPreview();
  document.getElementById('noPreview').style.display = 'none';
  var container = document.getElementById('previewContainer');
  var canvas = document.getElementById('previewCanvas');
  var ctx = canvas.getContext('2d');
  var overlayDiv = document.getElementById('overlay');
  container.style.display = 'block';
  previewWs = new WebSocket(PREVIEW_WS);
  previewWs.binaryType = 'arraybuffer';
  previewWs.onmessage = function(e) {
    if (e.data instanceof ArrayBuffer) {
      var blob = new Blob([e.data], {type:'image/jpeg'});
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function() {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
      };
      img.onerror = function() { URL.revokeObjectURL(url); };
      img.src = url;
    } else {
      try {
        var d = JSON.parse(e.data);
        if (d.type === 'overlay') {
          var lines = [];
          lines.push('phase: ' + (d.phase || ''));
          if (d.face_present !== undefined) lines.push('face: ' + d.face_present);
          if (d.yaw !== undefined && d.yaw !== null) lines.push('yaw: ' + (typeof d.yaw === 'number' ? d.yaw.toFixed(1) : d.yaw) + '  pitch: ' + (typeof d.pitch === 'number' ? d.pitch.toFixed(1) : d.pitch));
          if (d.yaw_deviation !== undefined && d.yaw_deviation !== null) lines.push('yaw_dev: ' + d.yaw_deviation.toFixed(1) + '  pitch_dev: ' + (d.pitch_deviation !== null ? d.pitch_deviation.toFixed(1) : 'N/A'));
          if (d.yaw_threshold !== undefined) lines.push('thresh: yaw=' + d.yaw_threshold.toFixed(1) + '  pitch=' + d.pitch_threshold.toFixed(1));
          if (d.disengaged !== undefined) {
            lines.push('disengaged: ' + d.disengaged + '  reason: ' + (d.reason || ''));
          }
          if (d.away_duration !== undefined) lines.push('away: ' + d.away_duration.toFixed(1) + 's  reengage: ' + d.reengage_duration.toFixed(1) + 's  (thresh: ' + (d.reengage_threshold_s || 0).toFixed(1) + 's)');
          if (d.smoothed_gaze_yaw !== undefined && d.smoothed_gaze_yaw !== null) {
            var gLine = 'gaze: (' + d.smoothed_gaze_yaw.toFixed(2) + ', ' + d.smoothed_gaze_pitch.toFixed(2) + ')';
            if (d.gaze_yaw_dev !== null) gLine += '  dev=(' + d.gaze_yaw_dev.toFixed(2) + ', ' + d.gaze_pitch_dev.toFixed(2) + ')';
            if (d.gaze_mind_wandering) gLine += '  MW!';
            lines.push(gLine);
          }
          if (d.fps !== undefined) lines.push('fps: ' + d.fps.toFixed(1));
          overlayDiv.innerHTML = lines.join('\n');
          if (d.disengaged) {
            overlayDiv.style.color = '#f55';
          } else {
            overlayDiv.style.color = '#0f0';
          }
        }
      } catch(_) {}
    }
  };
  previewWs.onclose = function() {
    container.style.display = 'none';
    document.getElementById('noPreview').style.display = 'block';
  };
}

function stopPreview() {
  if (previewWs) { try { previewWs.close(); } catch(e) {} previewWs = null; }
  document.getElementById('previewContainer').style.display = 'none';
  document.getElementById('noPreview').style.display = 'block';
}

pollStatus();
statusTimer = setInterval(pollStatus, 5000);
</script>
</body>
</html>
"""


class _BridgeHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = PARTICIPANT_PAGE.replace("__WS_PORT__", str(LOCAL_PORT))
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/researcher":
            page = RESEARCHER_PAGE.replace("__PREVIEW_PORT__", str(PREVIEW_PORT))
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/baseline":
            page = BASELINE_PAGE.replace("__WS_PORT__", str(LOCAL_PORT))
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/camera/status":
            with _participant_lock:
                data = {"status": _camera_status, "connected": _participant_ws is not None}
            self._json(200, data)
        elif self.path == "/api/recording/status":
            with _rec_lock:
                data = {"active": _rec_active, "filename": _rec_filename,
                        "frames": _rec_frame_count}
            self._json(200, data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _camera_status
        if self.path == "/api/camera/start":
            ok = _send_to_participant({"type": "start_camera"})
            if not ok:
                ok = _send_camera_via_server("start_camera")
            if ok:
                _camera_status = "streaming"
            self._json(200 if ok else 503, {"ok": ok})
        elif self.path == "/api/camera/stop":
            ok = _send_to_participant({"type": "stop_camera"})
            if not ok:
                ok = _send_camera_via_server("stop_camera")
            if ok:
                _camera_status = "connected"
            self._json(200 if ok else 503, {"ok": ok})
        elif self.path == "/api/recording/start":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            username = payload.get("username", "participant")
            if PARTICIPANT_CLIENT_MODE:
                result = _send_recording_via_server("start", username)
            else:
                result = _request_recording_start(username)
            self._json(200, result)
        elif self.path == "/api/recording/stop":
            if PARTICIPANT_CLIENT_MODE:
                result = _send_recording_via_server("stop")
            else:
                result = _request_recording_stop()
            self._json(200, result)
        elif self.path == "/api/baseline/start":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            code, data = _handle_baseline_start(payload)
            self._json(code, data)
        elif self.path == "/api/baseline/stop":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            code, data = _handle_baseline_stop(payload)
            self._json(code, data)
        else:
            self._json(404, {"ok": False})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _json(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[CameraHTTP] {args[0]}")


def _run_http_server():
    srv = HTTPServer(("0.0.0.0", HTTP_PORT), _BridgeHTTPHandler)
    print(f"[CameraHTTP] Participant page at http://0.0.0.0:{HTTP_PORT}")
    srv.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════
#  Researcher preview — WS on PREVIEW_PORT pushes live JPEG frames
# ═══════════════════════════════════════════════════════════════════════════

async def _handle_preview_client(ws: websockets.WebSocketServerProtocol) -> None:
    _preview_clients.add(ws)
    remote = ws.remote_address or ("?", 0)
    print(f"[Preview] Researcher connected from {remote[0]}:{remote[1]}")
    try:
        async for _ in ws:
            pass
    except websockets.ConnectionClosed:
        pass
    finally:
        _preview_clients.discard(ws)
        print("[Preview] Researcher disconnected")


async def _broadcast_frame(data: bytes) -> None:
    if not _preview_clients:
        return
    dead: list[websockets.WebSocketServerProtocol] = []

    async def _send_one(ws: websockets.WebSocketServerProtocol) -> None:
        try:
            await asyncio.wait_for(ws.send(data), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            dead.append(ws)

    await asyncio.gather(*(_send_one(ws) for ws in list(_preview_clients)),
                         return_exceptions=True)
    for ws in dead:
        _preview_clients.discard(ws)


# ═══════════════════════════════════════════════════════════════════════════
#  PostureEngine — identical state machine from participant_client.py
# ═══════════════════════════════════════════════════════════════════════════

class PostureEngine:
    """Encapsulates the full posture detection state machine.

    Every threshold, timer, grace-period, hysteresis branch and gaze
    mind-wandering path is a line-for-line port of run_client() in
    participant_client.py.  The only difference: frames arrive via
    argument instead of cv2.VideoCapture, and there is no cv2 display.
    """

    def __init__(self) -> None:
        self.reset()

    # ── lifecycle ──────────────────────────────────────────────────────

    def reset(self) -> None:
        # calibration
        self.calibration_start: float | None = None
        self.yaw_samples: list[float] = []
        self.pitch_samples: list[float] = []
        self.gaze_yaw_samples: list[float] = []
        self.gaze_pitch_samples: list[float] = []
        self.calibrated = False

        # reference values (set after calibration)
        self.reference_yaw: float | None = None
        self.reference_pitch: float | None = None
        self.reference_gaze_yaw: float = 0.5
        self.reference_gaze_pitch: float = 0.5

        # detection state
        self.disengaged = False
        self.away_start_time: float | None = None
        self.away_break_start_time: float | None = None
        self.reengage_start_time: float | None = None
        self.reengage_break_start_time: float | None = None
        self.detection_armed = False
        self.valid_face_streak = 0
        self.disengage_reason_latched: str | None = None

        # smoothing
        self.smoothed_yaw: float | None = None
        self.smoothed_pitch: float | None = None
        self.smoothed_gaze_yaw: float | None = None
        self.smoothed_gaze_pitch: float | None = None

        # grace periods
        self.face_missing_start_time: float | None = None
        self.invalid_pose_start_time: float | None = None
        self.last_stable_screen_facing: bool | None = None

        # gaze mind wandering
        self.gaze_mw_start_time: float | None = None
        self.gaze_mw_break_start_time: float | None = None
        self.gaze_mind_wandering = False

        # persisted between frames
        self.away_duration = 0.0
        self.reengage_duration = 0.0

        # stats
        self._frame_count = 0
        self._stats_time = time.time()

        # rolling FPS
        self._fps = 0.0
        self._fps_count = 0
        self._fps_time = time.time()

        # snapshot for overlay annotation (populated each frame)
        self.last_frame_info: dict = {}

    # ── public API ─────────────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> tuple[list[dict], dict]:
        """Process one video frame.

        Returns
        -------
        (bridge_messages, status)
            bridge_messages — list of dicts to send to the bridge server
            status          — dict describing current phase for the extension UI
        """
        self._frame_count += 1
        now = time.time()

        # rolling FPS (update every 0.5s)
        self._fps_count += 1
        fps_elapsed = now - self._fps_time
        if fps_elapsed >= 0.5:
            self._fps = self._fps_count / fps_elapsed
            self._fps_count = 0
            self._fps_time = now

        result = analyze_frame(frame)

        if not self.calibrated:
            msgs, status = self._calibrate(result, now)
        else:
            msgs, status = self._detect(result, now)

        self.last_frame_info["fps"] = self._fps
        return msgs, status

    def get_stats_line(self) -> str | None:
        now = time.time()
        elapsed = now - self._stats_time
        if elapsed < 5.0:
            return None
        fps = self._frame_count / elapsed
        self._frame_count = 0
        self._stats_time = now
        return (
            f"fps={fps:.1f} | armed={self.detection_armed} "
            f"| disengaged={self.disengaged}"
        )

    # ── calibration ────────────────────────────────────────────────────

    def _calibrate(self, result: dict, now: float) -> tuple[list[dict], dict]:
        if self.calibration_start is None:
            self.calibration_start = now

        elapsed = now - self.calibration_start

        if result["face_present"] and result["yaw"] is not None and result["pitch"] is not None:
            self.yaw_samples.append(result["yaw"])
            self.pitch_samples.append(result["pitch"])

        if ENABLE_EYE_GAZE and result["gaze"] is not None:
            self.gaze_yaw_samples.append(result["gaze"]["gaze_yaw_ratio"])
            self.gaze_pitch_samples.append(result["gaze"]["gaze_pitch_ratio"])

        cal_dur = _BRIDGE_CALIBRATION_DURATION
        min_samples = _BRIDGE_MIN_CALIBRATION_SAMPLES
        remaining = max(0.0, cal_dur - elapsed)

        if elapsed < cal_dur:
            self.last_frame_info = {
                "phase": "calibrating",
                "remaining": remaining,
                "samples": len(self.yaw_samples),
            }
            return [], {
                "phase": "calibrating",
                "remaining": round(remaining, 1),
                "samples": len(self.yaw_samples),
            }

        # ── calibration period done ──
        if len(self.yaw_samples) < min_samples or len(self.pitch_samples) < min_samples:
            print("[BrowserBridge] Calibration failed — not enough samples, retrying")
            self.calibration_start = None
            self.yaw_samples.clear()
            self.pitch_samples.clear()
            self.gaze_yaw_samples.clear()
            self.gaze_pitch_samples.clear()
            self.last_frame_info = {"phase": "calibration_retry"}
            return [], {"phase": "calibration_retry"}

        self.reference_yaw = float(np.mean(self.yaw_samples))
        self.reference_pitch = float(np.mean(self.pitch_samples))

        if ENABLE_EYE_GAZE and len(self.gaze_yaw_samples) >= 10:
            self.reference_gaze_yaw = float(np.median(self.gaze_yaw_samples))
            self.reference_gaze_pitch = float(np.median(self.gaze_pitch_samples))
        else:
            self.reference_gaze_yaw = 0.5
            self.reference_gaze_pitch = 0.5

        self.calibrated = True
        self.detection_armed = False
        self.valid_face_streak = 0

        print(
            f"[BrowserBridge] Calibration complete: "
            f"ref_yaw={self.reference_yaw:.2f}, ref_pitch={self.reference_pitch:.2f}"
        )
        if ENABLE_EYE_GAZE:
            print(
                f"[BrowserBridge]   ref_gaze_yaw={self.reference_gaze_yaw:.4f}, "
                f"ref_gaze_pitch={self.reference_gaze_pitch:.4f}"
            )

        msg = {
            "source": "webcam",
            "type": EVENT_CALIBRATION_COMPLETE,
            "reference_yaw": round(self.reference_yaw, 2),
            "reference_pitch": round(self.reference_pitch, 2),
            "enable_eye_gaze": ENABLE_EYE_GAZE,
            "reference_gaze_yaw": round(self.reference_gaze_yaw, 4) if ENABLE_EYE_GAZE else None,
            "reference_gaze_pitch": round(self.reference_gaze_pitch, 4) if ENABLE_EYE_GAZE else None,
            "timestamp": now,
        }
        self.last_frame_info = {"phase": "calibrated"}
        return [msg], {"phase": "calibrated"}

    # ── detection (main state machine) ─────────────────────────────────

    def _detect(self, result: dict, now: float) -> tuple[list[dict], dict]:  # noqa: C901 — mirrors original complexity
        raw_face_present = result["face_present"]
        yaw = result["yaw"]
        pitch = result["pitch"]
        gaze = result["gaze"]
        has_valid_pose = raw_face_present and yaw is not None and pitch is not None

        # ── face missing / invalid pose grace ──
        if raw_face_present:
            self.face_missing_start_time = None
        elif self.face_missing_start_time is None:
            self.face_missing_start_time = now

        if raw_face_present and yaw is None:
            if self.invalid_pose_start_time is None:
                self.invalid_pose_start_time = now
        else:
            self.invalid_pose_start_time = None

        face_missing_duration = 0.0 if self.face_missing_start_time is None else (now - self.face_missing_start_time)
        invalid_pose_duration = 0.0 if self.invalid_pose_start_time is None else (now - self.invalid_pose_start_time)

        # ── smoothing ──
        if has_valid_pose:
            self.smoothed_yaw = smooth_angle(self.smoothed_yaw, yaw, POSE_SMOOTHING_ALPHA)
            self.smoothed_pitch = smooth_angle(self.smoothed_pitch, pitch, POSE_SMOOTHING_ALPHA)
        else:
            self.smoothed_yaw = None
            self.smoothed_pitch = None

        effective_yaw = self.smoothed_yaw if self.smoothed_yaw is not None else yaw
        effective_pitch = self.smoothed_pitch if self.smoothed_pitch is not None else pitch

        # ── eye gaze smoothing ──
        if ENABLE_EYE_GAZE and gaze is not None:
            self.smoothed_gaze_yaw = smooth_angle(self.smoothed_gaze_yaw, gaze["gaze_yaw_ratio"], GAZE_SMOOTHING_ALPHA)
            self.smoothed_gaze_pitch = smooth_angle(self.smoothed_gaze_pitch, gaze["gaze_pitch_ratio"], GAZE_SMOOTHING_ALPHA)
        else:
            self.smoothed_gaze_yaw = None
            self.smoothed_gaze_pitch = None

        # ── screen facing determination ──
        if not raw_face_present:
            screen_facing = False
            yaw_deviation = None
            pitch_deviation = None
            yaw_threshold = YAW_DEVIATION_THRESHOLD
            pitch_threshold = PITCH_DEVIATION_THRESHOLD
            if self.detection_armed and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
                reason = "face_missing_pending"
            else:
                reason = "face_missing"
        elif yaw is None or pitch is None:
            screen_facing = False
            yaw_deviation = None
            pitch_deviation = None
            yaw_threshold = YAW_DEVIATION_THRESHOLD
            pitch_threshold = PITCH_DEVIATION_THRESHOLD
            if self.detection_armed and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
                reason = "pose_estimation_pending"
            else:
                reason = "pose_estimation_failed"
        else:
            yaw_deviation = abs(effective_yaw - self.reference_yaw)
            pitch_deviation = abs(effective_pitch - self.reference_pitch)

            screen_facing, yaw_threshold, pitch_threshold = compute_screen_facing(
                yaw_deviation=yaw_deviation,
                pitch_deviation=pitch_deviation,
                disengaged=self.disengaged,
                yaw_threshold=YAW_DEVIATION_THRESHOLD,
                pitch_threshold=PITCH_DEVIATION_THRESHOLD,
                reengage_yaw_threshold=REENGAGE_YAW_DEVIATION_THRESHOLD,
                reengage_pitch_threshold=REENGAGE_PITCH_DEVIATION_THRESHOLD,
                yaw_hysteresis_margin=YAW_HYSTERESIS_MARGIN,
                pitch_hysteresis_margin=PITCH_HYSTERESIS_MARGIN,
            )

            reason = "screen_facing" if screen_facing else "looking_away"
            self.last_stable_screen_facing = screen_facing

        # ── grace period overrides ──
        face_present = raw_face_present
        reported_screen_facing = screen_facing

        if self.detection_armed and (not raw_face_present) and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
            face_present = True
            if self.last_stable_screen_facing is not None:
                reported_screen_facing = self.last_stable_screen_facing
        elif self.detection_armed and raw_face_present and (yaw is None or pitch is None) and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
            if self.last_stable_screen_facing is not None:
                reported_screen_facing = self.last_stable_screen_facing

        face_missing_confirmed = (
            self.detection_armed
            and (not raw_face_present)
            and face_missing_duration >= FACE_MISSING_GRACE_PERIOD
        )
        invalid_pose_confirmed = (
            self.detection_armed
            and raw_face_present
            and (yaw is None or pitch is None)
            and invalid_pose_duration >= POSE_INVALID_GRACE_PERIOD
        )
        looking_away_confirmed = raw_face_present and yaw is not None and pitch is not None and (not screen_facing)
        state_changed = False
        active_error_reason = None
        reengage_threshold_s = REENGAGE_THRESHOLD

        # ── gaze deviation (computed before recovery logic) ──
        gaze_yaw_dev = None
        gaze_pitch_dev = None
        gaze_looking_away = False
        gaze_error_confirmed = False

        if ENABLE_EYE_GAZE and self.smoothed_gaze_yaw is not None:
            gaze_yaw_dev = abs(self.smoothed_gaze_yaw - self.reference_gaze_yaw)
            gaze_pitch_dev = abs(self.smoothed_gaze_pitch - self.reference_gaze_pitch)

            if yaw_deviation is not None and yaw_deviation < GAZE_HEAD_YAW_LIMIT:
                if self.gaze_mind_wandering:
                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD - GAZE_YAW_HYSTERESIS
                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD - GAZE_PITCH_HYSTERESIS
                else:
                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD + GAZE_YAW_HYSTERESIS
                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD + GAZE_PITCH_HYSTERESIS
                gaze_looking_away = gaze_yaw_dev > gy_thresh or gaze_pitch_dev > gp_thresh

        # ── arming ──
        if not self.detection_armed:
            if has_valid_pose:
                self.valid_face_streak += 1
            else:
                self.valid_face_streak = 0

            self.away_start_time = None
            self.away_break_start_time = None
            self.reengage_start_time = None
            self.reengage_break_start_time = None
            self.away_duration = 0.0
            self.reengage_duration = 0.0

            if self.valid_face_streak >= MIN_VALID_FACE_FRAMES:
                self.detection_armed = True
                print("[BrowserBridge] Detection armed")

            status = {
                "phase": "arming",
                "streak": self.valid_face_streak,
                "needed": MIN_VALID_FACE_FRAMES,
            }
            self.last_frame_info = {
                "phase": "arming",
                "face_present": raw_face_present,
                "yaw": yaw,
                "pitch": pitch,
                "valid_face_streak": self.valid_face_streak,
            }
            return [], status

        # ── gaze mind-wandering timer ──
        if ENABLE_EYE_GAZE:
            if gaze_looking_away:
                if self.gaze_mw_start_time is None:
                    self.gaze_mw_start_time = now
                self.gaze_mw_break_start_time = None
            elif self.gaze_mw_start_time is not None:
                if self.gaze_mw_break_start_time is None:
                    self.gaze_mw_break_start_time = now
                elif now - self.gaze_mw_break_start_time >= GAZE_MW_BREAK_TOLERANCE:
                    self.gaze_mw_start_time = None
                    self.gaze_mw_break_start_time = None

            gaze_error_confirmed = (
                self.gaze_mw_start_time is not None
                and (now - self.gaze_mw_start_time) >= GAZE_MIND_WANDERING_DURATION
            )
        else:
            self.gaze_mw_start_time = None
            self.gaze_mw_break_start_time = None

        self.gaze_mind_wandering = gaze_error_confirmed

        # ── active error reason ──
        active_error_reason = None
        if face_missing_confirmed:
            active_error_reason = "face_missing"
        elif invalid_pose_confirmed:
            active_error_reason = "pose_estimation_failed"
        elif looking_away_confirmed:
            active_error_reason = "looking_away"
        elif gaze_error_confirmed:
            active_error_reason = "gaze_mind_wandering"

        # ── full recovery check ──
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=ENABLE_EYE_GAZE,
            disengage_reason=self.disengage_reason_latched,
            raw_face_present=raw_face_present,
            yaw=yaw,
            pitch=pitch,
            screen_facing=screen_facing,
            smoothed_gaze_yaw=self.smoothed_gaze_yaw,
            smoothed_gaze_pitch=self.smoothed_gaze_pitch,
            gaze_looking_away=gaze_looking_away,
            gaze_error_confirmed=gaze_error_confirmed,
        )

        reengage_threshold_s = compute_reengage_threshold(
            disengage_reason=self.disengage_reason_latched,
            default_threshold_s=REENGAGE_THRESHOLD,
            gaze_threshold_s=GAZE_REENGAGE_THRESHOLD,
        )

        # ── state transitions ──
        if active_error_reason is not None:
            reason = self.disengage_reason_latched or active_error_reason

            if self.away_start_time is None:
                self.away_start_time = now
            self.away_break_start_time = None
            self.away_duration = now - self.away_start_time

            if self.reengage_start_time is not None:
                if self.reengage_break_start_time is None:
                    self.reengage_break_start_time = now
                elif now - self.reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                    self.reengage_start_time = None
                    self.reengage_duration = 0.0
            else:
                self.reengage_duration = 0.0

            if not self.disengaged:
                if active_error_reason == "gaze_mind_wandering" or self.away_duration >= DISENGAGE_THRESHOLD:
                    self.disengaged = True
                    self.disengage_reason_latched = active_error_reason
                    reason = self.disengage_reason_latched
                    state_changed = True

        elif fully_recovered:
            reason = "gaze_recovered" if ENABLE_EYE_GAZE else "screen_facing"
            self.reengage_break_start_time = None

            if self.away_start_time is not None:
                if self.away_break_start_time is None:
                    self.away_break_start_time = now
                elif now - self.away_break_start_time >= AWAY_BREAK_TOLERANCE:
                    self.away_start_time = None
                    self.away_duration = 0.0
            else:
                self.away_duration = 0.0

            if self.reengage_start_time is None:
                self.reengage_start_time = now
            self.reengage_duration = now - self.reengage_start_time

            if self.disengaged and self.reengage_duration >= reengage_threshold_s:
                self.disengaged = False
                self.disengage_reason_latched = None
                state_changed = True

        else:
            reason = self.disengage_reason_latched or "waiting_full_recovery"
            self.away_break_start_time = None
            self.away_duration = 0.0 if self.away_start_time is None else (now - self.away_start_time)

            if self.disengaged and self.reengage_start_time is not None:
                if self.reengage_break_start_time is None:
                    self.reengage_break_start_time = now
                elif now - self.reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                    self.reengage_start_time = None
                    self.reengage_duration = 0.0
            else:
                self.reengage_duration = 0.0

        # ── build message if state changed ──
        messages: list[dict] = []
        if state_changed:
            msg = {
                "client": "webcam",
                "source": "webcam",
                "type": EVENT_POSTURE,
                "disengage": self.disengaged,
                "face_present": face_present,
                "screen_facing": reported_screen_facing,
                "reason": reason,
                "yaw": None if yaw is None else round(yaw, 2),
                "pitch": None if pitch is None else round(pitch, 2),
                "reference_yaw": round(self.reference_yaw, 2),
                "reference_pitch": round(self.reference_pitch, 2),
                "yaw_deviation": None if yaw_deviation is None else round(yaw_deviation, 2),
                "pitch_deviation": None if pitch_deviation is None else round(pitch_deviation, 2),
                "away_duration": round(self.away_duration, 2),
                "reengage_duration": round(self.reengage_duration, 2),
                "gaze_yaw_ratio": round(self.smoothed_gaze_yaw, 4) if self.smoothed_gaze_yaw is not None else None,
                "gaze_pitch_ratio": round(self.smoothed_gaze_pitch, 4) if self.smoothed_gaze_pitch is not None else None,
                "gaze_yaw_deviation": round(gaze_yaw_dev, 4) if gaze_yaw_dev is not None else None,
                "gaze_pitch_deviation": round(gaze_pitch_dev, 4) if gaze_pitch_dev is not None else None,
                "gaze_mind_wandering": self.gaze_mind_wandering if ENABLE_EYE_GAZE else None,
                "timestamp": now,
            }
            messages.append(msg)
            label = "DISENGAGED" if self.disengaged else "RE-ENGAGED"
            print(f"[BrowserBridge] *** {label} *** reason={reason}")

        status = {
            "phase": "active",
            "disengaged": self.disengaged,
            "armed": self.detection_armed,
            "reason": reason,
        }

        gaze_mw_duration = 0.0 if self.gaze_mw_start_time is None else (now - self.gaze_mw_start_time)

        self.last_frame_info = {
            "phase": "active",
            "face_present": face_present,
            "raw_face_present": raw_face_present,
            "yaw": yaw,
            "pitch": pitch,
            "effective_yaw": effective_yaw,
            "effective_pitch": effective_pitch,
            "reference_yaw": self.reference_yaw,
            "reference_pitch": self.reference_pitch,
            "yaw_deviation": yaw_deviation,
            "pitch_deviation": pitch_deviation,
            "yaw_threshold": yaw_threshold,
            "pitch_threshold": pitch_threshold,
            "reported_screen_facing": reported_screen_facing,
            "reason": reason,
            "disengaged": self.disengaged,
            "detection_armed": self.detection_armed,
            "valid_face_streak": self.valid_face_streak,
            "away_duration": self.away_duration,
            "reengage_duration": self.reengage_duration,
            "reengage_threshold_s": reengage_threshold_s,
            "active_error_reason": active_error_reason,
            "smoothed_gaze_yaw": self.smoothed_gaze_yaw,
            "smoothed_gaze_pitch": self.smoothed_gaze_pitch,
            "gaze_yaw_dev": gaze_yaw_dev,
            "gaze_pitch_dev": gaze_pitch_dev,
            "gaze_looking_away": gaze_looking_away,
            "gaze_mind_wandering": self.gaze_mind_wandering,
            "gaze_mw_duration": gaze_mw_duration,
        }

        return messages, status


# ═══════════════════════════════════════════════════════════════════════════
#  Annotated frame overlay (mirrors participant_client.py cv2 display)
# ═══════════════════════════════════════════════════════════════════════════

def _annotate_frame(frame: np.ndarray, info: dict) -> np.ndarray:
    """Draw detection overlay onto *frame* (in-place) and return it."""
    phase = info.get("phase", "")
    font = cv2.FONT_HERSHEY_SIMPLEX

    # FPS counter — top-right corner
    fps_val = info.get("fps", 0.0)
    fps_text = f"{fps_val:.1f} fps"
    (tw, th), _ = cv2.getTextSize(fps_text, font, 0.55, 2)
    h, w = frame.shape[:2]
    cv2.putText(frame, fps_text, (w - tw - 10, 25), font, 0.55, (0, 255, 0), 2)

    if phase == "calibrating":
        remaining = info.get("remaining", 0)
        samples = info.get("samples", 0)
        cv2.putText(frame, f"CALIBRATING  {remaining:.1f}s left  ({samples} samples)",
                     (20, 30), font, 0.55, (0, 255, 255), 2)
        return frame

    if phase == "calibration_retry":
        cv2.putText(frame, "CALIBRATION RETRY — not enough samples",
                     (20, 30), font, 0.55, (0, 0, 255), 2)
        return frame

    if phase == "calibrated":
        cv2.putText(frame, "CALIBRATION COMPLETE — arming...",
                     (20, 30), font, 0.55, (0, 255, 0), 2)
        return frame

    if phase == "arming":
        streak = info.get("valid_face_streak", 0)
        cv2.putText(frame, f"ARMING  {streak}/{MIN_VALID_FACE_FRAMES}",
                     (20, 30), font, 0.55, (0, 255, 255), 2)
        return frame

    # ── active detection phase ──
    disengaged = info.get("disengaged", False)
    color = (0, 0, 255) if disengaged else (0, 255, 0)  # BGR

    face_present = info.get("face_present", False)
    yaw = info.get("yaw")
    pitch = info.get("pitch")
    effective_yaw = info.get("effective_yaw")
    effective_pitch = info.get("effective_pitch")

    # line 1: face + yaw + pitch + smooth
    line1 = f"face={face_present}"
    line1 += f" | yaw={yaw:.1f}" if yaw is not None else " | yaw=None"
    line1 += f" | pitch={pitch:.1f}" if pitch is not None else " | pitch=None"
    if effective_yaw is not None and effective_pitch is not None:
        line1 += f" | smooth=({effective_yaw:.1f},{effective_pitch:.1f})"
    cv2.putText(frame, line1, (20, 30), font, 0.55, color, 2)

    # line 2: reference
    ref_yaw = info.get("reference_yaw", 0)
    ref_pitch = info.get("reference_pitch", 0)
    line2 = f"ref_yaw={ref_yaw:.1f} | ref_pitch={ref_pitch:.1f}"
    cv2.putText(frame, line2, (20, 60), font, 0.55, (255, 255, 0), 2)

    # line 3: deviations + thresholds
    yaw_dev = info.get("yaw_deviation")
    pitch_dev = info.get("pitch_deviation")
    yaw_th = info.get("yaw_threshold", 0)
    pitch_th = info.get("pitch_threshold", 0)
    line3 = f"yaw_dev={yaw_dev:.1f}" if yaw_dev is not None else "yaw_dev=None"
    line3 += f" | pitch_dev={pitch_dev:.1f}" if pitch_dev is not None else " | pitch_dev=None"
    if yaw is not None and pitch is not None:
        line3 += f" | th=({yaw_th:.1f},{pitch_th:.1f})"
    cv2.putText(frame, line3, (20, 90), font, 0.55, (255, 255, 0), 2)

    # line 4: reason, facing, durations, disengaged
    reason = info.get("reason", "")
    facing = info.get("reported_screen_facing", False)
    away_dur = info.get("away_duration", 0.0)
    back_dur = info.get("reengage_duration", 0.0)
    detection_armed = info.get("detection_armed", False)
    line4 = (f"reason={reason} | facing={facing} | away={away_dur:.1f}s"
             f" | back={back_dur:.1f}s | disengaged={disengaged}")
    if not detection_armed:
        streak = info.get("valid_face_streak", 0)
        line4 += f" | arming={streak}/{MIN_VALID_FACE_FRAMES}"
    cv2.putText(frame, line4, (20, 120), font, 0.55, color, 2)

    # line 5: gaze
    if ENABLE_EYE_GAZE:
        sg_yaw = info.get("smoothed_gaze_yaw")
        sg_pitch = info.get("smoothed_gaze_pitch")
        if sg_yaw is not None and sg_pitch is not None:
            g_yaw_dev = info.get("gaze_yaw_dev")
            g_pitch_dev = info.get("gaze_pitch_dev")
            gaze_mw_dur = info.get("gaze_mw_duration", 0.0)
            dev_str = ""
            if g_yaw_dev is not None:
                dev_str = f" | dev=({g_yaw_dev:.2f},{g_pitch_dev:.2f})"
            line5 = f"gaze=({sg_yaw:.2f},{sg_pitch:.2f}){dev_str} | mw={gaze_mw_dur:.1f}s"
            gaze_color = (0, 165, 255) if info.get("gaze_looking_away") else (0, 255, 0)
            cv2.putText(frame, line5, (20, 150), font, 0.55, gaze_color, 2)
        else:
            cv2.putText(frame, "gaze=N/A", (20, 150), font, 0.55, (128, 128, 128), 2)

    # line 6: pending start / pending stop
    if detection_armed:
        active_err = info.get("active_error_reason")
        if not disengaged and active_err is not None and active_err != "gaze_mind_wandering":
            remaining = max(0.0, DISENGAGE_THRESHOLD - away_dur)
            line6 = (f"PENDING start: {active_err} | away={away_dur:.1f}s/"
                     f"{DISENGAGE_THRESHOLD:.1f}s (still {remaining:.1f}s)")
            cv2.putText(frame, line6, (20, 180), font, 0.50, (0, 165, 255), 2)
        elif disengaged and back_dur > 0:
            re_th = info.get("reengage_threshold_s", REENGAGE_THRESHOLD)
            remaining = max(0.0, re_th - back_dur)
            line6 = (f"PENDING stop | back={back_dur:.1f}s/"
                     f"{re_th:.1f}s (still {remaining:.1f}s)")
            cv2.putText(frame, line6, (20, 180), font, 0.50, (255, 128, 0), 2)

    return frame


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket connection management
# ═══════════════════════════════════════════════════════════════════════════

# Single-worker pool — MediaPipe FaceMesh is not thread-safe across
# concurrent calls, but we only need one thread to keep the event loop free.
_frame_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

# Separate executor for recording I/O (cv2 decode + VideoWriter.write)
# so that disk/codec work never blocks the asyncio event loop.
_rec_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


async def handle_extension(ext_ws: websockets.WebSocketServerProtocol) -> None:
    global _participant_ws, _camera_status

    remote = ext_ws.remote_address or ("unknown", 0)
    print(f"[BrowserBridge] Client connected from {remote[0]}:{remote[1]}")

    with _participant_lock:
        _participant_ws = ext_ws
        _camera_status = "connected"

    engine = PostureEngine()
    bridge_ws: websockets.WebSocketClientProtocol | None = None

    async def ensure_bridge() -> websockets.WebSocketClientProtocol | None:
        nonlocal bridge_ws
        if bridge_ws is not None:
            try:
                if bridge_ws.state == _WsState.OPEN:
                    return bridge_ws
            except Exception:
                pass
            bridge_ws = None

        try:
            bridge_ws = await asyncio.wait_for(
                websockets.connect(
                    BRIDGE_WS_URL,
                    ping_interval=20,
                    ping_timeout=60,
                ),
                timeout=5.0,
            )
            # Register as webcam client so server.py routes events correctly
            await bridge_ws.send(json.dumps({"client": "webcam"}))
            print(f"[BrowserBridge] Connected to bridge server: {BRIDGE_WS_URL}")
            return bridge_ws
        except asyncio.TimeoutError:
            print(f"[BrowserBridge] Bridge connection timeout (5s): {BRIDGE_WS_URL}")
            bridge_ws = None
            return None
        except Exception as exc:
            print(f"[BrowserBridge] Bridge connection failed: {exc}")
            bridge_ws = None
            return None

    # ── Frame-dropping receiver / processor architecture ──
    # The extension sends ~30fps but MediaPipe may be slower.  A separate
    # receiver task stores only the latest frame; the processor always
    # picks up the freshest data, skipping stale frames.
    # Preview frames are broadcast via a dedicated _preview_broadcaster()
    # coroutine that always sends the latest frame, dropping intermediate
    # ones when the event loop is under load.
    latest_frame_data: bytearray | None = None
    latest_preview_data: bytes | None = None
    frame_ready = asyncio.Event()
    preview_ready = asyncio.Event()
    stop_flag = asyncio.Event()
    loop = asyncio.get_event_loop()

    async def _receiver() -> None:
        """Receive WS messages; store latest binary frame, handle text cmds."""
        nonlocal latest_frame_data, latest_preview_data
        _frame_count = 0
        try:
            async for raw_message in ext_ws:
                if stop_flag.is_set():
                    break
                if isinstance(raw_message, bytes):
                    latest_frame_data = raw_message
                    frame_ready.set()
                    # Record EVERY received frame — offload to thread so
                    # cv2 decode + VideoWriter.write never block the event loop.
                    asyncio.ensure_future(
                        loop.run_in_executor(
                            _rec_executor,
                            _check_and_handle_recording_bytes,
                            raw_message,
                        )
                    )
                    # Signal preview broadcaster with latest frame
                    latest_preview_data = raw_message
                    preview_ready.set()
                    _frame_count += 1
                    if _frame_count <= 3 or _frame_count % 100 == 0:
                        print(f"[BrowserBridge] Frame #{_frame_count} received ({len(raw_message)} bytes, {len(_preview_clients)} preview clients)")
                elif isinstance(raw_message, str):
                    try:
                        cmd = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue
                    if cmd.get("type") == "stop":
                        print("[BrowserBridge] Extension requested stop")
                        stop_flag.set()
                        frame_ready.set()  # wake processor
                        break
                    elif cmd.get("type") == "reset":
                        engine.reset()
                        print("[BrowserBridge] Engine reset — will re-calibrate")
        except websockets.ConnectionClosed:
            pass
        finally:
            stop_flag.set()
            frame_ready.set()  # ensure processor exits

    async def _processor() -> None:
        """Process the latest available frame in a thread pool."""
        nonlocal bridge_ws
        while not stop_flag.is_set():
            await frame_ready.wait()
            if stop_flag.is_set():
                break
            frame_ready.clear()

            data = latest_frame_data
            if data is None:
                continue

            frame = decode_jpeg_frame(data)
            if frame is None:
                continue

            # Run CPU-heavy MediaPipe in a thread so the event loop
            # stays responsive for WS ping/pong and frame receiving.
            messages, status = await loop.run_in_executor(
                _frame_pool, engine.process_frame, frame
            )

            # Send detection status to preview clients (rendered as HTML overlay)
            # Use ensure_future to avoid blocking the processor on preview writes.
            if _preview_clients:
                try:
                    info = engine.last_frame_info
                    overlay = json.dumps({"type": "overlay", **info}, default=_json_default)
                    for pws in list(_preview_clients):
                        asyncio.ensure_future(pws.send(overlay))
                except (TypeError, ValueError) as exc:
                    print(f"[Preview] JSON serialization failed: {exc}")

            # Forward posture events to bridge server (non-blocking).
            # Resolve bridge connection once per frame to avoid repeated
            # 5-second timeouts when the server is unreachable.
            if messages:
                bridge_ws_snapshot = await ensure_bridge()
                for msg in messages:
                    if bridge_ws_snapshot is not None:
                        _payload = json.dumps(msg)
                        _ws_ref = bridge_ws_snapshot

                        async def _send_bridge(_ws=_ws_ref, _data=_payload) -> None:
                            try:
                                await asyncio.wait_for(_ws.send(_data), timeout=3.0)
                                print(f"[BrowserBridge] Sent to bridge: {_data}")
                            except asyncio.TimeoutError:
                                print("[BrowserBridge] Bridge send timeout (3s)")
                            except websockets.ConnectionClosed:
                                nonlocal bridge_ws
                                if bridge_ws is _ws:
                                    bridge_ws = None
                                print("[BrowserBridge] Bridge disconnected during send")
                            except Exception as exc:
                                print(f"[BrowserBridge] Bridge send error: {exc}")

                        asyncio.ensure_future(_send_bridge())

            # Send status to extension
            try:
                await ext_ws.send(json.dumps({"type": "status", **status}))
            except websockets.ConnectionClosed:
                break

            # Periodic stats
            stats = engine.get_stats_line()
            if stats:
                print(f"[BrowserBridge] {stats}")

    async def _preview_broadcaster() -> None:
        """Dedicated task: send only the latest JPEG frame to preview clients.

        Uses an Event + shared variable pattern so that at most ONE send
        is in-flight at a time.  If the send takes longer than one frame
        interval, intermediate frames are silently dropped — the browser
        always receives the freshest available frame.
        """
        while not stop_flag.is_set():
            await preview_ready.wait()
            if stop_flag.is_set():
                break
            preview_ready.clear()
            data = latest_preview_data
            if data is not None and _preview_clients:
                await _broadcast_frame(data)

    async def _bridge_reader() -> None:
        """Drain incoming messages from bridge_ws so the internal buffer
        does not fill up and cause backpressure on sends."""
        while not stop_flag.is_set():
            # Wait until a bridge connection exists
            if bridge_ws is None or bridge_ws.state != _WsState.OPEN:
                await asyncio.sleep(1)
                continue
            try:
                async for _msg in bridge_ws:
                    if stop_flag.is_set():
                        break
            except websockets.ConnectionClosed:
                pass
            except Exception:
                pass

    try:
        await asyncio.gather(
            _receiver(), _processor(),
            _preview_broadcaster(), _bridge_reader(),
        )
    except websockets.ConnectionClosed:
        print("[BrowserBridge] Client disconnected")
    finally:
        # Drain pending recording writes before stopping
        _rec_executor.shutdown(wait=True)
        # Stop recording if still active (e.g. client disconnect)
        if _rec_active:
            _do_stop_recording()
        with _participant_lock:
            _participant_ws = None
            _camera_status = "disconnected"
        if bridge_ws is not None:
            try:
                await bridge_ws.close()
            except Exception:
                pass
        print("[BrowserBridge] Session ended\n")


async def main() -> None:
    global _event_loop
    _event_loop = asyncio.get_event_loop()

    # HTTP server in daemon thread
    threading.Thread(target=_run_http_server, daemon=True).start()

    print("=" * 60)
    print("  Webcam Monitor — Browser Bridge Server")
    print("=" * 60)
    if PARTICIPANT_CLIENT_MODE:
        print(f"  Mode            : PARTICIPANT_CLIENT (port {LOCAL_PORT} WS disabled)")
    else:
        print(f"  Participant WS  : ws://0.0.0.0:{LOCAL_PORT}")
        print(f"  Participant page: http://0.0.0.0:{HTTP_PORT}")
    print(f"  Preview WS      : ws://0.0.0.0:{PREVIEW_PORT}")
    print(f"  Control API     : http://0.0.0.0:{HTTP_PORT}/api/camera/{{start|stop|status}}")
    print(f"  Bridge target   : {BRIDGE_WS_URL}")
    print(f"  Eye gaze        : {'ENABLED' if ENABLE_EYE_GAZE else 'DISABLED'}")
    print("=" * 60)
    if PARTICIPANT_CLIENT_MODE:
        print(f"Participant: run  python participant_client.py  (local detection mode)")
    else:
        print(f"Participant: open http://<this-ip>:{HTTP_PORT}  (standalone camera page)")
    print(f"Researcher:  open http://localhost:{HTTP_PORT}/researcher  (camera control + preview)\n")

    if PARTICIPANT_CLIENT_MODE:
        # Researcher-console-only mode: no participant browser WS (port 9876).
        # Camera commands relay through server.py to participant_client.py.
        async with websockets.serve(_handle_preview_client, "0.0.0.0", PREVIEW_PORT):
            await asyncio.Future()
    else:
        async with websockets.serve(handle_extension, LOCAL_HOST, LOCAL_PORT):
            async with websockets.serve(_handle_preview_client, "0.0.0.0", PREVIEW_PORT):
                await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BrowserBridge] Shutting down")
