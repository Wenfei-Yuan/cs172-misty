# Webcam Posture Monitor — Chrome Extension

Standalone Chrome extension that captures the webcam in the background
and sends frames to a local Python bridge for **identical** MediaPipe
Face Mesh + head pose + eye gaze detection.

## Architecture

```
Chrome Extension (webcam-monitor/)
  └─ offscreen.html  ── getUserMedia → JPEG frames
        │
        │  binary WebSocket  (ws://127.0.0.1:9876)
        ▼
Python Bridge  (webcam/browser_bridge.py)
  ├─ MediaPipe Face Mesh  (same as participant_client.py)
  ├─ Head pose via solvePnP
  ├─ Eye gaze via Iris Position Ratios
  └─ State machine (disengage / reengage)
        │
        │  WebSocket  (ws://SERVER_IP:8765)
        ▼
Bridge Server  (server.py)
```

## Prerequisites

- Python 3.10+ with the webcam dependencies already installed
  (`mediapipe`, `opencv-python`, `numpy`, `websockets`)
- The main bridge server (`server.py`) running on `SERVER_IP:8765`

## How to Run

### 1. Start the Python bridge

```bash
cd webcam
python browser_bridge.py
```

You should see:

```
  Webcam Monitor — Browser Bridge Server
  Local WebSocket : ws://127.0.0.1:9876
  Bridge target   : ws://10.5.15.160:8765
  Eye gaze        : ENABLED
Waiting for Chrome extension connection...
```

### 2. Load the extension in Chrome

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right)
3. Click **Load unpacked** → select the `webcam-monitor/` folder
4. The extension icon appears in the toolbar

### 3. Start monitoring

1. Click the extension icon → popup opens
2. Click **Start**
3. The badge shows **CAM** → **CAL** (calibrating, ~3 s) → **ON** (active)
4. Look straight ahead during calibration

### 4. Stop

Click the icon again → **Stop**. The camera and WebSocket connections
are cleaned up automatically.

## Badge Meanings

| Badge | Meaning                               |
| ----- | ------------------------------------- |
| CAM   | Camera started, connecting to bridge  |
| CAL   | Calibrating (look straight ahead)     |
| ARM   | Arming detection (building baseline)  |
| ON    | Actively monitoring                   |
| REC   | Reconnecting to Python bridge         |
| ERR   | Error (check console / Python output) |

## Notes

- The extension captures at ~15 fps, JPEG quality 0.85
- All face detection, head pose, eye gaze, and the state machine run
  in Python — **bit-for-bit identical** to `participant_client.py`
- The extension only handles camera access and frame transport
- WebSocket messages to the bridge server use the same format as the
  native webcam client
