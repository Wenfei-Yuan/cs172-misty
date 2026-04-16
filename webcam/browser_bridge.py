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

_BRIDGE_CALIBRATION_DURATION = 12.0
_BRIDGE_MIN_CALIBRATION_SAMPLES = 5

BRIDGE_WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}"


def decode_jpeg_frame(data: bytes) -> np.ndarray | None:
    buf = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return frame


# ═══════════════════════════════════════════════════════════════════════════
#  Shared state — accessed by asyncio handlers + HTTP thread
# ═══════════════════════════════════════════════════════════════════════════
_event_loop: asyncio.AbstractEventLoop | None = None
_participant_ws: websockets.WebSocketServerProtocol | None = None
_participant_lock = threading.Lock()
_camera_status = "disconnected"   # disconnected | connected | streaming
_preview_clients: set[websockets.WebSocketServerProtocol] = set()


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
    timer = setTimeout(sendFrames, 66);
  }, 'image/jpeg', 0.85);
}

connect();
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
  <canvas id="previewCanvas" width="640" height="480"></canvas>
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
  var canvas = document.getElementById('previewCanvas');
  var ctx = canvas.getContext('2d');
  canvas.style.display = 'block';
  previewWs = new WebSocket(PREVIEW_WS);
  previewWs.binaryType = 'arraybuffer';
  previewWs.onmessage = function(e) {
    var blob = new Blob([e.data], {type:'image/jpeg'});
    var url = URL.createObjectURL(blob);
    var img = new Image();
    img.onload = function() {
      canvas.width = img.width;
      canvas.height = img.height;
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
    };
    img.src = url;
  };
  previewWs.onclose = function() {
    canvas.style.display = 'none';
    document.getElementById('noPreview').style.display = 'block';
  };
}

function stopPreview() {
  if (previewWs) { try { previewWs.close(); } catch(e) {} previewWs = null; }
  document.getElementById('previewCanvas').style.display = 'none';
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
        elif self.path == "/api/camera/status":
            with _participant_lock:
                data = {"status": _camera_status, "connected": _participant_ws is not None}
            self._json(200, data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _camera_status
        if self.path == "/api/camera/start":
            ok = _send_to_participant({"type": "start_camera"})
            if ok:
                _camera_status = "streaming"
            self._json(200 if ok else 503, {"ok": ok})
        elif self.path == "/api/camera/stop":
            ok = _send_to_participant({"type": "stop_camera"})
            if ok:
                _camera_status = "connected"
            self._json(200 if ok else 503, {"ok": ok})
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
    dead = []
    for ws in list(_preview_clients):
        try:
            await ws.send(data)
        except Exception:
            dead.append(ws)
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
            bridge_ws = await websockets.connect(
                BRIDGE_WS_URL,
                ping_interval=20,
                ping_timeout=60,
            )
            # Register as webcam client so server.py routes events correctly
            await bridge_ws.send(json.dumps({"client": "webcam"}))
            print(f"[BrowserBridge] Connected to bridge server: {BRIDGE_WS_URL}")
            return bridge_ws
        except Exception as exc:
            print(f"[BrowserBridge] Bridge connection failed: {exc}")
            bridge_ws = None
            return None

    # ── Frame-dropping receiver / processor architecture ──
    # The extension sends ~15fps but MediaPipe may be slower.  A separate
    # receiver task stores only the latest frame; the processor always
    # picks up the freshest data, skipping stale frames.
    latest_frame_data: bytearray | None = None
    frame_ready = asyncio.Event()
    stop_flag = asyncio.Event()
    loop = asyncio.get_event_loop()

    async def _receiver() -> None:
        """Receive WS messages; store latest binary frame, handle text cmds."""
        nonlocal latest_frame_data
        _frame_count = 0
        try:
            async for raw_message in ext_ws:
                if stop_flag.is_set():
                    break
                if isinstance(raw_message, bytes):
                    latest_frame_data = raw_message
                    frame_ready.set()
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

            # Broadcast annotated frame to researcher preview
            if _preview_clients:
                annotated = _annotate_frame(frame, engine.last_frame_info)
                ok, buf = cv2.imencode('.jpg', annotated,
                                       [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    asyncio.ensure_future(_broadcast_frame(buf.tobytes()))

            # Forward posture events to bridge server
            for msg in messages:
                ws = await ensure_bridge()
                if ws is not None:
                    try:
                        await ws.send(json.dumps(msg))
                        print(f"[BrowserBridge] Sent to bridge: {json.dumps(msg, ensure_ascii=False)}")
                    except websockets.ConnectionClosed:
                        bridge_ws = None
                        print("[BrowserBridge] Bridge disconnected during send")

            # Send status to extension
            try:
                await ext_ws.send(json.dumps({"type": "status", **status}))
            except websockets.ConnectionClosed:
                break

            # Periodic stats
            stats = engine.get_stats_line()
            if stats:
                print(f"[BrowserBridge] {stats}")

    try:
        await asyncio.gather(_receiver(), _processor())
    except websockets.ConnectionClosed:
        print("[BrowserBridge] Client disconnected")
    finally:
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
    print(f"  Participant WS  : ws://0.0.0.0:{LOCAL_PORT}")
    print(f"  Participant page: http://0.0.0.0:{HTTP_PORT}")
    print(f"  Preview WS      : ws://0.0.0.0:{PREVIEW_PORT}")
    print(f"  Control API     : http://0.0.0.0:{HTTP_PORT}/api/camera/{{start|stop|status}}")
    print(f"  Bridge target   : {BRIDGE_WS_URL}")
    print(f"  Eye gaze        : {'ENABLED' if ENABLE_EYE_GAZE else 'DISABLED'}")
    print("=" * 60)
    print(f"Participant: open http://<this-ip>:{HTTP_PORT}  (standalone camera page)")
    print(f"Researcher:  open http://localhost:{HTTP_PORT}/researcher  (camera control + preview)\n")

    async with websockets.serve(handle_extension, LOCAL_HOST, LOCAL_PORT):
        async with websockets.serve(_handle_preview_client, "0.0.0.0", PREVIEW_PORT):
            await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BrowserBridge] Shutting down")
