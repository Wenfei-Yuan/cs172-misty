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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import error, request

HOST = "0.0.0.0"
PORT = int(os.getenv("WEB_PORT", "8080"))
TRIGGER_HOST = os.getenv("TRIGGER_HOST", "127.0.0.1")
TRIGGER_PORT = int(os.getenv("TRIGGER_PORT", "5050"))
BRIDGE_HTTP_PORT = int(os.getenv("BRIDGE_HTTP_PORT", "9877"))

_pipeline_proc: subprocess.Popen | None = None
_pipeline_lock = threading.Lock()


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


def _start_pipeline(username: str) -> tuple[bool, str]:
    global _pipeline_proc
    with _pipeline_lock:
        if _pipeline_proc and _pipeline_proc.poll() is None:
            return False, "Pipeline already running"
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        python = _find_python()
        _pipeline_proc = subprocess.Popen(
            [python, script, "--username", username],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
    _notify_bridge_recording("start", username, datetime.now().astimezone().isoformat())
    return True, f"Pipeline started (PID {_pipeline_proc.pid})"


def _stop_pipeline() -> tuple[bool, str]:
    global _pipeline_proc
    with _pipeline_lock:
        if not _pipeline_proc or _pipeline_proc.poll() is not None:
            _pipeline_proc = None
            return False, "No running pipeline"
    ok, detail = _send_shutdown()
    # Wait for the process to actually exit
    with _pipeline_lock:
        if _pipeline_proc is not None:
            try:
                _pipeline_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _pipeline_proc.terminate()
                try:
                    _pipeline_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _pipeline_proc.kill()
                    _pipeline_proc.wait(timeout=2)
            _pipeline_proc = None
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
      body: JSON.stringify({username: username})
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

// Auto-connect to camera bridge server on page load
connectBridge();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
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
            ok, detail = _start_pipeline(username)
            self._json_response(200 if ok else 409, {"ok": ok, "detail": detail})

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
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Misty Web Controller running at http://{HOST}:{PORT}")
    print("Open this URL in your browser to control the robot.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web controller.")
        server.server_close()
