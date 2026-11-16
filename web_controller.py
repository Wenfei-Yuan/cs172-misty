"""
Simple web frontend to control the Misty robot pipeline.

Usage:
    python web_controller.py

Then open http://localhost:8080 in your browser.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

HOST = "0.0.0.0"
PORT = int(os.getenv("WEB_PORT", "8080"))
TRIGGER_HOST = os.getenv("TRIGGER_HOST", "127.0.0.1")
TRIGGER_PORT = int(os.getenv("TRIGGER_PORT", "5050"))
BRIDGE_HTTP_PORT = int(os.getenv("BRIDGE_HTTP_PORT", "9877"))

_pipeline_proc: subprocess.Popen | None = None
_pipeline_lock = threading.Lock()
_pipeline_username: str | None = None
_pipeline_started_at: str | None = None
_stopping = False

# Phase 2 A/B switch. The researcher picks the version once on this page; every
# session started afterwards (from here or from the participant /baseline page)
# runs main.py with that --policy. Saved to a file so a restart keeps it.
POLICIES = ("graded", "immediate")
POLICY_FILE = os.getenv(
    "POLICY_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions", "intervention_policy.txt"),
)
_pipeline_policy: str | None = None


def _load_policy() -> str:
    try:
        with open(POLICY_FILE, encoding="utf-8") as fh:
            value = fh.read().strip().lower()
        if value in POLICIES:
            return value
    except OSError:
        pass
    value = (os.getenv("INTERVENTION_POLICY") or "graded").strip().lower()
    return value if value in POLICIES else "graded"


def _save_policy(value: str) -> None:
    os.makedirs(os.path.dirname(POLICY_FILE), exist_ok=True)
    with open(POLICY_FILE, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")


_current_policy = _load_policy()


def _set_policy(value: str) -> tuple[bool, str]:
    global _current_policy
    value = (value or "").strip().lower()
    if value not in POLICIES:
        return False, f"policy must be one of {POLICIES}"
    with _pipeline_lock:
        _current_policy = value
    try:
        _save_policy(value)
    except OSError as exc:
        return True, f"policy set to {value} (not saved to file: {exc})"
    return True, f"policy set to {value}"


def _send_shutdown() -> tuple[bool, str]:
    url = f"http://{TRIGGER_HOST}:{TRIGGER_PORT}/shutdown"
    req = request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=5) as resp:
            return True, resp.read().decode()
    except error.URLError as exc:
        return False, str(exc)


def _notify_bridge_recording(action: str, username: str = "", session_ts: str = "") -> None:
    """Tell the webcam bridge to start/stop video recording."""
    if action == "start":
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/start"
        payload = {"username": username}
        if session_ts:
            payload["session_start_ts"] = session_ts
        data = json.dumps(payload).encode()
    else:
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/stop"
        payload = {}
        if session_ts:
            payload["session_end_ts"] = session_ts
        data = json.dumps(payload).encode()

    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            print(f"[web] Recording {action}: {result}")
    except Exception as exc:
        print(f"[web] Recording {action} failed (bridge may not be running): {exc}")


def _find_python() -> str:
    """Return the venv Python if it exists, else sys.executable."""
    base = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(base, ".venv", "bin", "python")
    if os.path.isfile(venv_python):
        return venv_python
    return sys.executable


def _status() -> dict:
    with _pipeline_lock:
        running = _pipeline_proc is not None and _pipeline_proc.poll() is None
        return {
            "running": running,
            "stopping": running and _stopping,
            "pid": _pipeline_proc.pid if running else None,
            "username": _pipeline_username if running else None,
            "started_at": _pipeline_started_at if running else None,
            "running_policy": _pipeline_policy if running else None,
            "policy": _current_policy,
        }


def _start_pipeline(username: str, policy: str | None = None) -> tuple[bool, str]:
    global _pipeline_proc, _pipeline_username, _pipeline_started_at, _stopping, _pipeline_policy
    with _pipeline_lock:
        if _pipeline_proc and _pipeline_proc.poll() is None:
            return False, "Pipeline already running"
        policy = (policy or _current_policy).strip().lower()
        if policy not in POLICIES:
            return False, f"policy must be one of {POLICIES}"
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        python = _find_python()
        _pipeline_proc = subprocess.Popen(
            [python, script, "--username", username, "--policy", policy],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        _pipeline_username = username
        _pipeline_policy = policy
        _pipeline_started_at = datetime.now().astimezone().isoformat()
        _stopping = False
    _notify_bridge_recording("start", username, datetime.now().astimezone().isoformat())
    return True, f"Pipeline started (PID {_pipeline_proc.pid}, {policy})"


def _stop_pipeline() -> tuple[bool, str]:
    """Ask the pipeline to shut down and wait for it to exit on its own.

    No timeout and no kill: the summary stage speaks, waves and then saves the
    session log, and killing it early would lose that log. Calling this twice
    (participant page + researcher) is safe: the second call waits for the same
    process.
    """
    global _pipeline_proc, _stopping
    with _pipeline_lock:
        proc = _pipeline_proc
        if not proc or proc.poll() is not None:
            _pipeline_proc = None
            return False, "No running pipeline"
        already_stopping = _stopping
        _stopping = True
    if already_stopping:
        ok, detail = True, "shutdown already requested"
    else:
        ok, detail = _send_shutdown()
        for _ in range(3):
            if ok or proc.poll() is not None:
                break
            time.sleep(1)
            ok, detail = _send_shutdown()
        if not ok and proc.poll() is None:
            # The pipeline never got the shutdown request; waiting would hang
            # forever. Leave it running and report, so the researcher can retry.
            with _pipeline_lock:
                _stopping = False
            return False, f"Shutdown request failed, pipeline still running: {detail}"
    proc.wait()
    with _pipeline_lock:
        if _pipeline_proc is proc:
            _pipeline_proc = None
        _stopping = False
    if not already_stopping:
        _notify_bridge_recording("stop", session_ts=datetime.now().astimezone().isoformat())
    return ok, detail


HTML_PAGE = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Misty Robot Controller</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
    width: 420px;
    text-align: center;
  }
  .card h1 {
    font-size: 28px;
    color: #333;
    margin-bottom: 8px;
  }
  .card .subtitle {
    font-size: 14px;
    color: #888;
    margin-bottom: 32px;
  }
  .robot-icon {
    font-size: 64px;
    margin-bottom: 16px;
  }
  label {
    display: block;
    text-align: left;
    font-weight: 600;
    color: #555;
    margin-bottom: 6px;
    font-size: 14px;
  }
  input[type="text"] {
    width: 100%;
    padding: 12px 16px;
    border: 2px solid #ddd;
    border-radius: 10px;
    font-size: 16px;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 24px;
  }
  select {
    width: 100%; padding: 12px 16px; border: 2px solid #ddd; border-radius: 10px;
    font-size: 15px; margin-bottom: 24px; background: #fff;
  }
  input[type="text"]:focus {
    border-color: #667eea;
  }
  .btn {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    font-size: 17px;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.1s, box-shadow 0.2s;
    margin-bottom: 12px;
  }
  .btn:active { transform: scale(0.98); }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
  }
  .btn-start {
    background: linear-gradient(135deg, #43e97b, #38f9d7);
    color: #fff;
    box-shadow: 0 4px 15px rgba(67,233,123,0.4);
  }
  .btn-stop {
    background: linear-gradient(135deg, #f093fb, #f5576c);
    color: #fff;
    box-shadow: 0 4px 15px rgba(245,87,108,0.4);
  }
  #status {
    margin-top: 20px;
    padding: 12px;
    border-radius: 10px;
    font-size: 14px;
    display: none;
  }
  .status-ok   { background: #e6ffed; color: #27ae60; display: block !important; }
  .status-err  { background: #ffeaea; color: #e74c3c; display: block !important; }
  .status-info { background: #eef2ff; color: #667eea; display: block !important; }
  .divider { border: none; border-top: 2px solid #eee; margin: 28px 0 20px; }
  #camStatus {
    margin-top: 0; padding: 10px; border-radius: 10px;
    font-size: 13px; display: block; background: #f0f0f0; color: #888;
  }
  .cam-ok   { background: #e6ffed !important; color: #27ae60 !important; }
  .cam-info { background: #eef2ff !important; color: #667eea !important; }
  .cam-err  { background: #ffeaea !important; color: #e74c3c !important; }
</style>
</head>
<body>
<div class="card">
  <div class="robot-icon">🤖</div>
  <h1>Misty Controller</h1>
  <p class="subtitle">Start a reading session with Misty</p>

  <label for="policy">Robot version (A/B)</label>
  <select id="policy" onchange="setPolicy(this.value)">
    <option value="graded">Graded — current 4-stage version</option>
    <option value="immediate">Immediate — old study version</option>
  </select>

  <label for="username">Username</label>
  <input type="text" id="username" placeholder="e.g. wenfei" autocomplete="off">

  <button class="btn btn-start" id="btnStart" onclick="startSession()">
    ▶ Start Session
  </button>
  <button class="btn btn-stop" id="btnStop" onclick="stopSession()" disabled>
    ■ End Session
  </button>

  <div id="status"></div>

  <!-- Camera connection status -->
  <hr class="divider">
  <div id="camStatus">📷 Connecting to camera server...</div>
</div>

<!-- Hidden camera elements -->
<video id="camVideo" style="display:none" autoplay muted playsinline></video>
<canvas id="camCanvas" style="display:none"></canvas>

<script>
var CAM_WS_PORT = 9876;
var camWs = null, camStream = null, camTimer = null, camStreaming = false;
var _camGen = 0;  // generation counter — invalidates stale WebSocket handlers
var camVideo = document.getElementById('camVideo');
var camCanvas = document.getElementById('camCanvas');
var camCtx = camCanvas.getContext('2d');

function setStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status-' + type;
}

function setCamStatus(msg, type) {
  var el = document.getElementById('camStatus');
  el.textContent = msg;
  el.className = type ? ('cam-' + type) : '';
}

function connectBridge() {
  var wsUrl = 'ws://' + location.hostname + ':' + CAM_WS_PORT;
  var gen = ++_camGen;
  setCamStatus('📷 Connecting to ' + wsUrl + '...', 'info');
  camWs = new WebSocket(wsUrl);
  camWs.binaryType = 'arraybuffer';
  camWs.onopen = function() {
    if (gen !== _camGen) return;
    camWs.send(JSON.stringify({type: 'hello', role: 'participant'}));
    setCamStatus('📷 Connected — camera ready, controlled by researcher', 'ok');
  };
  camWs.onmessage = function(e) {
    if (gen !== _camGen) return;
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'start_camera') startCamera();
      else if (msg.type === 'stop_camera') stopCamera();
    } catch(_) {}
  };
  camWs.onclose = function(ev) {
    if (gen !== _camGen) return;
    stopCamera();
    setCamStatus('📷 Disconnected (code ' + ev.code + ') — reconnecting in 3s...', 'err');
    setTimeout(connectBridge, 3000);
  };
  camWs.onerror = function() {};  // onclose always fires after onerror; let it handle messaging
}

async function startCamera() {
  if (camStreaming) return;
  setCamStatus('📷 Opening camera...', 'info');
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: {ideal: 320}, height: {ideal: 240} },
      audio: false
    });
    camVideo.srcObject = camStream;
    await camVideo.play();
    camCanvas.width = camVideo.videoWidth;
    camCanvas.height = camVideo.videoHeight;
    camStreaming = true;
    setCamStatus('📷 Camera active — streaming to researcher (' + camVideo.videoWidth + 'x' + camVideo.videoHeight + ')', 'ok');
    sendCamFrames();
  } catch(err) {
    setCamStatus('📷 Camera error: ' + err.name + ' — ' + err.message, 'err');
  }
}

function stopCamera() {
  camStreaming = false;
  if (camTimer) { clearTimeout(camTimer); camTimer = null; }
  if (camStream) {
    camStream.getTracks().forEach(function(t) { t.stop(); });
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
      blob.arrayBuffer().then(function(buf) { camWs.send(new Uint8Array(buf)); });
    camTimer = setTimeout(sendCamFrames, 33);
  }, 'image/jpeg', 0.6);
}

async function setPolicy(value) {
  try {
    const res = await fetch('/api/policy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({policy: value})
    });
    const data = await res.json();
    setStatus(data.ok ? 'Version: ' + value + ' (used by the next session)' : 'Error: ' + data.detail,
              data.ok ? 'info' : 'err');
  } catch (e) {
    setStatus('Request failed: ' + e.message, 'err');
  }
}

async function startSession() {
  const username = document.getElementById('username').value.trim();
  if (!username) {
    setStatus('Please enter a username', 'err');
    return;
  }
  setStatus('Starting pipeline...', 'info');
  document.getElementById('btnStart').disabled = true;

  try {
    const res = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: username, policy: document.getElementById('policy').value})
    });
    const data = await res.json();
    if (data.ok) {
      setStatus('Session running: ' + data.detail, 'ok');
      document.getElementById('btnStop').disabled = false;
      document.getElementById('username').disabled = true;
    } else {
      setStatus('Error: ' + data.detail, 'err');
      document.getElementById('btnStart').disabled = false;
    }
  } catch (e) {
    setStatus('Request failed: ' + e.message, 'err');
    document.getElementById('btnStart').disabled = false;
  }
}

async function stopSession() {
  setStatus('Ending session... Misty is saying goodbye 👋', 'info');
  document.getElementById('btnStop').disabled = true;

  try {
    const res = await fetch('/api/stop', {method: 'POST'});
    const data = await res.json();
    if (data.ok) {
      setStatus('Session ended! Misty said goodbye 👋', 'ok');
    } else {
      setStatus('Stop failed: ' + data.detail, 'err');
      document.getElementById('btnStop').disabled = false;
    }
  } catch (e) {
    setStatus('Request failed: ' + e.message, 'err');
    document.getElementById('btnStop').disabled = false;
  }

  // Re-enable start after a short delay
  setTimeout(() => {
    document.getElementById('btnStart').disabled = false;
    document.getElementById('username').disabled = false;
  }, 2000);
}

// Poll the backend so sessions started or ended from the participant
// reading page (/baseline on the bridge) show up here too.
async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');
    const user = document.getElementById('username');
    const pol = document.getElementById('policy');
    if (document.activeElement !== pol && d.policy) pol.value = d.policy;
    pol.disabled = !!d.running;
    if (d.running) {
      btnStart.disabled = true;
      btnStop.disabled = !!d.stopping;
      user.disabled = true;
      if (d.username && !user.value) user.value = d.username;
      setStatus(d.stopping ? 'Ending session... waiting for Misty to finish 👋'
                           : 'Session running: ' + d.username + ' [' + d.running_policy + '] (PID ' + d.pid + ', since ' + (d.started_at || '').slice(11, 19) + ')',
                d.stopping ? 'info' : 'ok');
    } else if (btnStart.disabled && !btnStop.disabled) {
      // finished elsewhere
      btnStart.disabled = false;
      btnStop.disabled = true;
      user.disabled = false;
      setStatus('No session running', 'info');
    }
  } catch (e) {}
}
setInterval(pollStatus, 2000);
pollStatus();

// Auto-connect to camera bridge server on page load
connectBridge();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            self._json_response(200, {"ok": True, **_status()})
            return
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        if self.path == "/api/start":
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            username = payload.get("username", "").strip()
            if not username:
                self._json_response(400, {"ok": False, "detail": "username required"})
                return
            ok, detail = _start_pipeline(username, payload.get("policy"))
            self._json_response(200 if ok else 409, {"ok": ok, "detail": detail})

        elif self.path == "/api/policy":
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            ok, detail = _set_policy(str(payload.get("policy", "")))
            self._json_response(200 if ok else 400, {"ok": ok, "detail": detail, "policy": _current_policy})

        elif self.path == "/api/stop":
            ok, detail = _stop_pipeline()
            self._json_response(200 if ok else 404, {"ok": ok, "detail": detail})

        else:
            self._json_response(404, {"ok": False, "detail": "not found"})

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        print(f"[web] {args[0]}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Misty Web Controller running at http://{HOST}:{PORT}")
    print("Open this URL in your browser to control the robot.")
    print(f"Robot version (A/B policy): {_current_policy}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web controller.")
        server.server_close()
