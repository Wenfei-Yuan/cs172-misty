/*  offscreen.js — Hidden camera capture + frame transport
 *
 *  Captures webcam frames via getUserMedia(), encodes them as JPEG,
 *  and ships them as binary WebSocket messages to the local Python
 *  bridge server (browser_bridge.py) at LOCAL_WS_URL.
 */

const LOCAL_WS_URL = "ws://127.0.0.1:9876";
const FRAME_INTERVAL_MS = 66; // ~15 fps
const JPEG_QUALITY = 0.85;

const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

let ws = null;
let stream = null;
let frameTimer = null;
let running = false;
let capturing = false;

// ── Helpers ──────────────────────────────────────────────────

function sendStatus(status, detail) {
  chrome.runtime
    .sendMessage({ type: "WEBCAM_STATUS", status, detail: detail || "" })
    .catch(() => {});
}

// ── Camera ───────────────────────────────────────────────────

async function startCamera() {
  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: "user",
      width: { ideal: 640 },
      height: { ideal: 480 },
    },
    audio: false,
  });
  video.srcObject = stream;
  await video.play();
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  video.srcObject = null;
}

// ── WebSocket to Python bridge ───────────────────────────────

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  ws = new WebSocket(LOCAL_WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    console.log("[Webcam Monitor] Connected to Python bridge");
    sendStatus("CONNECTED");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.phase) {
        sendStatus(msg.phase.toUpperCase());
      }
    } catch {
      /* ignore non-JSON */
    }
  };

  ws.onclose = () => {
    ws = null;
    if (running) {
      sendStatus("RECONNECTING");
      setTimeout(connectWS, 2000);
    }
  };

  ws.onerror = () => {
    sendStatus("ERROR", "Cannot connect to Python bridge at " + LOCAL_WS_URL);
  };
}

// ── Frame capture loop ───────────────────────────────────────

function captureAndSend() {
  if (capturing) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!video.videoWidth) return;

  capturing = true;
  ctx.drawImage(video, 0, 0);

  canvas.toBlob(
    (blob) => {
      capturing = false;
      if (blob && ws && ws.readyState === WebSocket.OPEN) {
        blob.arrayBuffer().then((buf) => ws.send(buf));
      }
    },
    "image/jpeg",
    JPEG_QUALITY
  );
}

function startLoop() {
  stopLoop();
  frameTimer = setInterval(captureAndSend, FRAME_INTERVAL_MS);
}

function stopLoop() {
  if (frameTimer) {
    clearInterval(frameTimer);
    frameTimer = null;
  }
}

// ── Start / Stop ─────────────────────────────────────────────

async function start() {
  if (running) return;
  running = true;

  try {
    console.log("[Webcam Monitor] Starting camera…");
    await startCamera();
    console.log("[Webcam Monitor] Camera started, resolution:", video.videoWidth, "x", video.videoHeight);
    sendStatus("STARTED");
    connectWS();
    startLoop();
  } catch (err) {
    console.error("[Webcam Monitor] Start failed:", err);
    let detail = err.message;
    if (err.name === "NotAllowedError") {
      detail = "Camera permission denied. Go to chrome://settings/content/camera and allow this extension.";
    } else if (err.name === "NotFoundError") {
      detail = "No camera found on this device.";
    } else if (err.name === "NotReadableError") {
      detail = "Camera is in use by another app. Close other apps using the camera.";
    }
    sendStatus("ERROR", detail);
    running = false;
  }
}

function stop() {
  running = false;
  stopLoop();

  if (ws) {
    try {
      ws.send(JSON.stringify({ type: "stop" }));
    } catch {
      /* ok */
    }
    try {
      ws.close();
    } catch {
      /* ok */
    }
    ws = null;
  }

  stopCamera();
  sendStatus("STOPPED");
}

// ── Message listener from background.js ──────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== "offscreen-webcam") return;

  if (msg.type === "PING") {
    sendResponse({ ok: true });
    return;
  }

  if (msg.type === "START") {
    start()
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true; // async
  }

  if (msg.type === "STOP") {
    stop();
    sendResponse({ ok: true });
  }
});

// Signal to background.js that this script is loaded and ready
chrome.runtime.sendMessage({ type: "OFFSCREEN_READY" }).catch(() => {});
