"""
Baseline condition web controller — no robot intervention.

Participants open this page, enter their name, and click Start / End reading.
The webcam (controlled by researcher on another machine) sends detection data
through server.py.  Disengagement events are relayed to this controller and
saved as session JSON for post-study analysis.

Usage:
    1. Start server.py  (CONTROL_MODE=1 python server.py)
    2. Start webcam bridge  (python webcam/browser_bridge.py)
    3. Start this controller  (python baseline_controller.py)

    Participant opens  http://<this-ip>:8081
    Researcher controls camera from the webcam bridge researcher page.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import error, request

HOST = "0.0.0.0"
PORT = int(os.getenv("BASELINE_PORT", "8081"))
BRIDGE_HTTP_PORT = int(os.getenv("BRIDGE_HTTP_PORT", "9877"))
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")

# ── Active session state (one at a time) ─────────────────────────────
_session_lock = threading.Lock()
_active_session: dict | None = None


def _notify_bridge_recording(action: str, username: str = "") -> None:
    """Tell the webcam bridge to start/stop video recording."""
    if action == "start":
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/start"
        data = json.dumps({"username": username}).encode()
    else:
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/stop"
        data = b"{}"
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            print(f"[baseline] Recording {action}: {result}")
    except Exception as exc:
        print(f"[baseline] Recording {action} failed (bridge may not be running): {exc}")


def _gen_session_id(participant_id: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    short_hash = uuid.uuid4().hex[:6]
    return f"baseline_{date_str}_{participant_id}_{short_hash}"


def _process_events(raw_events: list[dict], session_end_time: str) -> list[dict]:
    """Convert raw start/stop events into distraction_events list."""
    distraction_events: list[dict] = []
    current_start: str | None = None
    current_reason: str | None = None
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

    # Unclosed distraction at session end
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


# ── HTML page ─────────────────────────────────────────────────────────

HTML_PAGE = """\
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
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .card {
    background: #fff;
    border-radius: 20px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    padding: 48px 40px;
    width: 440px;
    text-align: center;
  }
  .card h1 { font-size: 26px; color: #333; margin-bottom: 8px; }
  .card .subtitle { font-size: 14px; color: #888; margin-bottom: 32px; }
  .icon { font-size: 56px; margin-bottom: 12px; }
  label {
    display: block; text-align: left; font-weight: 600;
    color: #555; margin-bottom: 6px; font-size: 14px;
  }
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
  .btn-start {
    background: linear-gradient(135deg, #43e97b, #38f9d7);
    color: #fff; box-shadow: 0 4px 15px rgba(67,233,123,0.4);
  }
  .btn-stop {
    background: linear-gradient(135deg, #f093fb, #f5576c);
    color: #fff; box-shadow: 0 4px 15px rgba(245,87,108,0.4);
  }
  #status {
    margin-top: 20px; padding: 12px; border-radius: 10px;
    font-size: 14px; display: none;
  }
  .status-ok   { background: #e6ffed; color: #27ae60; display: block !important; }
  .status-err  { background: #ffeaea; color: #e74c3c; display: block !important; }
  .status-info { background: #eef2ff; color: #667eea; display: block !important; }
  .divider { border: none; border-top: 2px solid #eee; margin: 28px 0 20px; }
  #camStatus {
    padding: 10px; border-radius: 10px; font-size: 13px;
    display: block; background: #f0f0f0; color: #888;
  }
  .cam-ok   { background: #e6ffed !important; color: #27ae60 !important; }
  .cam-info { background: #eef2ff !important; color: #667eea !important; }
  .cam-err  { background: #ffeaea !important; color: #e74c3c !important; }
  #serverStatus {
    margin-top: 10px; padding: 10px; border-radius: 10px;
    font-size: 13px; background: #f0f0f0; color: #888;
  }
  .srv-ok  { background: #e6ffed !important; color: #27ae60 !important; }
  .srv-err { background: #ffeaea !important; color: #e74c3c !important; }
  #eventLog {
    margin-top: 10px; padding: 10px; border-radius: 10px;
    font-size: 13px; background: #f9f9f9; color: #555;
  }
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

  <button class="btn btn-start" id="btnStart" onclick="startSession()">
    ▶ Start Reading
  </button>
  <button class="btn btn-stop" id="btnStop" onclick="stopSession()" disabled>
    ■ End Reading
  </button>

  <div id="timer" class="timer" style="display:none">00:00</div>
  <div id="status"></div>

  <hr class="divider">
  <div id="camStatus">📷 Connecting to camera server…</div>
  <div id="serverStatus">🔗 Connecting to detection server…</div>
  <div id="eventLog"></div>
</div>

<!-- Hidden camera elements -->
<video id="camVideo" style="display:none" autoplay muted playsinline></video>
<canvas id="camCanvas" style="display:none"></canvas>

<script>
/* ── Configuration ─────────────────────────────────────── */
var CAM_WS_PORT   = 9876;
var SERVER_WS_PORT = 8765;

/* ── State ─────────────────────────────────────────────── */
var sessionId     = null;
var sessionEvents = [];
var timerInterval = null;
var sessionStart  = null;

/* camera */
var camWs = null, camStream = null, camTimer = null, camStreaming = false;
var camVideo  = document.getElementById('camVideo');
var camCanvas = document.getElementById('camCanvas');
var camCtx    = camCanvas.getContext('2d');

/* server.py WebSocket */
var serverWs = null;

/* ── UI helpers ────────────────────────────────────────── */
function setStatus(msg, type) {
  var el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-' + type;
}
function setCamStatus(msg, type) {
  var el = document.getElementById('camStatus');
  el.textContent = msg;
  el.className = type ? ('cam-' + type) : '';
}
function setServerStatus(msg, type) {
  var el = document.getElementById('serverStatus');
  el.textContent = msg;
  el.className = type ? ('srv-' + type) : '';
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

/* ── Camera bridge (same as Misty controller) ──────────── */
function connectBridge() {
  var wsUrl = 'ws://' + location.hostname + ':' + CAM_WS_PORT;
  setCamStatus('📷 Connecting to ' + wsUrl + '…', 'info');
  camWs = new WebSocket(wsUrl);
  camWs.binaryType = 'arraybuffer';
  camWs.onopen = function() {
    camWs.send(JSON.stringify({type: 'hello', role: 'participant'}));
    setCamStatus('📷 Connected — camera ready, controlled by researcher', 'ok');
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
    setCamStatus('📷 Disconnected (code ' + ev.code + ') — reconnecting in 3 s…', 'err');
    setTimeout(connectBridge, 3000);
  };
  camWs.onerror = function() {
    setCamStatus('📷 Connection error', 'err');
  };
}

async function startCamera() {
  if (camStreaming) return;
  setCamStatus('📷 Opening camera…', 'info');
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: {ideal: 320}, height: {ideal: 240} },
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
  if (camStream) {
    camStream.getTracks().forEach(function(t){ t.stop(); });
    camStream = null;
  }
  camVideo.srcObject = null;
  if (camWs && camWs.readyState === WebSocket.OPEN)
    setCamStatus('📷 Connected — camera ready, controlled by researcher', 'ok');
}

function sendCamFrames() {
  if (!camStreaming) return;
  camCtx.drawImage(camVideo, 0, 0);
  camCanvas.toBlob(function(blob) {
    if (blob && camWs && camWs.readyState === WebSocket.OPEN)
      blob.arrayBuffer().then(function(buf){ camWs.send(new Uint8Array(buf)); });
    camTimer = setTimeout(sendCamFrames, 33);
  }, 'image/jpeg', 0.6);
}

/* ── Detection server WebSocket ────────────────────────── */
function connectServer() {
  var wsUrl = 'ws://' + location.hostname + ':' + SERVER_WS_PORT;
  setServerStatus('🔗 Connecting to ' + wsUrl + '…', '');
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
    setServerStatus('🔗 Detection server disconnected — reconnecting…', 'err');
    setTimeout(connectServer, 3000);
  };

  serverWs.onerror = function() {
    setServerStatus('🔗 Detection server error', 'err');
  };
}

/* ── Session control ───────────────────────────────────── */
async function startSession() {
  var username = document.getElementById('username').value.trim();
  if (!username) { setStatus('Please enter your name', 'err'); return; }

  setStatus('Starting session…', 'info');
  document.getElementById('btnStart').disabled = true;

  try {
    var res = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username})
    });
    var data = await res.json();
    if (data.ok) {
      sessionId = data.session_id;
      sessionEvents = [];
      sessionStart = Date.now();
      setStatus('Reading session active — focus on your reading!', 'ok');
      document.getElementById('btnStop').disabled = false;
      document.getElementById('username').disabled = true;
      document.getElementById('timer').style.display = 'block';
      timerInterval = setInterval(updateTimer, 1000);
      updateEventLog();
      // Auto-start camera
      startCamera();
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

  // Stop camera first
  stopCamera();

  try {
    var res = await fetch('/api/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, events: sessionEvents})
    });
    var data = await res.json();
    if (data.ok) {
      setStatus('Session ended! Distractions recorded: ' + data.num_distractions, 'ok');
    } else {
      setStatus('Error: ' + data.detail, 'err');
      document.getElementById('btnStop').disabled = false;
    }
  } catch(e) {
    setStatus('Request failed: ' + e.message, 'err');
    document.getElementById('btnStop').disabled = false;
  }

  sessionId = null;
  sessionEvents = [];
  sessionStart = null;

  setTimeout(function() {
    document.getElementById('btnStart').disabled = false;
    document.getElementById('username').disabled = false;
    document.getElementById('timer').style.display = 'none';
    document.getElementById('eventLog').textContent = '';
  }, 2000);
}

/* ── Boot ──────────────────────────────────────────────── */
connectBridge();
connectServer();
</script>
</body>
</html>
"""


# ── HTTP handler ──────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        if self.path == "/api/start":
            self._handle_start(body)
        elif self.path == "/api/stop":
            self._handle_stop(body)
        else:
            self._json_response(404, {"ok": False, "detail": "not found"})

    # ── Start session ──

    def _handle_start(self, body: bytes):
        global _active_session
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}
        username = payload.get("username", "").strip()
        if not username:
            self._json_response(400, {"ok": False, "detail": "username required"})
            return

        with _session_lock:
            if _active_session:
                self._json_response(409, {"ok": False, "detail": "Session already active"})
                return
            session_id = _gen_session_id(username)
            _active_session = {
                "session_id": session_id,
                "participant_id": username,
                "start_time": datetime.now().astimezone().isoformat(),
            }

        print(f"[baseline] Session started: {session_id} (participant={username})")
        _notify_bridge_recording("start", username)
        self._json_response(200, {"ok": True, "session_id": session_id})

    # ── Stop session ──

    def _handle_stop(self, body: bytes):
        global _active_session
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

        raw_events = payload.get("events", [])

        with _session_lock:
            if not _active_session:
                self._json_response(404, {"ok": False, "detail": "No active session"})
                return
            session = dict(_active_session)
            _active_session = None

        end_time = datetime.now().astimezone().isoformat()

        # Build events list (compatible with generate_csv.py)
        events: list[dict] = []
        for e in raw_events:
            ev_name = "disengagement_start" if e.get("event") == "start" else "disengagement_end"
            entry: dict = {"timestamp": e.get("ts", ""), "name": ev_name, "payload": {}}
            if e.get("reason"):
                entry["payload"]["reason"] = e["reason"]
            events.append(entry)

        distraction_events = _process_events(raw_events, end_time)

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

        os.makedirs(SESSIONS_DIR, exist_ok=True)
        fpath = os.path.join(SESSIONS_DIR, f"{session['session_id']}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        print(f"[baseline] Session saved: {fpath}  ({len(distraction_events)} distractions)")
        _notify_bridge_recording("stop")
        self._json_response(200, {
            "ok": True,
            "session_id": session["session_id"],
            "num_distractions": len(distraction_events),
        })

    # ── Helpers ──

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[baseline] {args[0]}")


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Baseline Controller running at http://{HOST}:{PORT}")
    print("Participant opens this page to start/end reading sessions.")
    print("Researcher controls the camera from the webcam bridge page.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down baseline controller.")
        server.server_close()
