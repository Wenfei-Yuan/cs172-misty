# cs172-misty

## WebSocket protocol

The WebSocket bridge runs on `ws://<host>:8765` by default.

There are two intended WebSocket clients:

- `webcam`: sends posture and disengagement signals into the bridge
- `extension`: receives redirect and posture notifications from the bridge

### Client registration

Each client should register once after connecting.

Extension registration:

```json
{"client":"extension"}
```

Webcam registration:

```json
{"client":"webcam"}
```

Registration success response:

```json
{"ok":true,"type":"registered","client":"extension"}
```

The `client` field in the response will match the registered role.

### Webcam -> server messages

The webcam client can send either plain text or JSON.

Supported plain text messages:

- `posture_disengaged=true` (webcam-side distraction start)
- `posture_disengaged=false` (webcam-side re-engagement)
- `re-engagement` (webcam-side re-engagement alias)
- `re-engament` (legacy typo alias, still supported)
- `start`
- `stop`
- `shutdown`

Supported JSON messages:

```json
{"posture_disengaged": true}
```

```json
{"posture_disengaged": false}
```

```json
{"re_engagement": true}
```

```json
{"re_engament": true}
```

```json
{"event": "start"}
```

```json
{"event": "stop"}
```

```json
{"event": "shutdown"}
```

### Extension -> server current text message

When extension needs to pass reading context to Misty, it can send:

```json
{"currentText": "the text user is currently reading"}
```

The server forwards this payload to `POST /current_text` on port `5050` (Misty side).

### Server -> webcam responses

For accepted events, the webcam connection receives a JSON response:

```json
{"ok": true, "event": "start", "trigger_response": "..."}
```

or:

```json
{"ok": true, "event": "stop", "trigger_response": "..."}
```

If the message is understood but does not change state, the webcam receives:

```json
{"ok": false, "reason": "state_not_changed"}
```

If the message is not understood, the webcam receives:

```json
{"ok": false, "reason": "unrecognized_message"}
```

### Server -> extension notifications

These notifications are only sent to the client registered as `extension`.

When the webcam sends a disengagement-start signal that successfully becomes a distraction start event, the extension receives:

```text
ROBOT_REDIRECT
```

When the distraction end condition is satisfied and `stop` is successfully forwarded, the extension receives:

```json
{"eventName": "AttentionResumed"}
```

### Distraction state rules

- `posture_disengaged=true` (webcam) maps to the distraction-start signal and forwards `POST /distraction/start` to port `5050`
- While already in distraction, repeated start-side signals do not retrigger start
- `posture_disengaged=false` (webcam) or webcam-side `re-engagement` / `{"re_engagement": true}` map to distraction stop and forward `POST /distraction/stop` to port `5050`
- extension-side re-engagement messages are ignored and do not trigger stop
- While already recovered, repeated recovery signals do not retrigger stop
- extension `ReadingState` payloads and `{"currentText":"..."}` messages are context-only and are forwarded to `POST /current_text` when text is present

### Typical flow

1. `extension` connects and sends `{"client":"extension"}`
2. `webcam` connects and sends `{"client":"webcam"}`
3. `webcam` sends `{"posture_disengaged": true}`
4. Server forwards `start` to Misty on port `5050`
5. Server returns a JSON success response to `webcam`
6. Server sends `ROBOT_REDIRECT` to `extension`
7. `extension` sends `{"currentText":"..."}` to server
8. Server forwards that text to Misty on `POST /current_text` (port `5050`)
9. `webcam` later sends `{"posture_disengaged": false}` (or webcam-side `re-engagement`)
10. Server forwards `stop` to Misty on port `5050`
11. Server returns a JSON success response to `webcam`
12. Server sends `{"eventName": "AttentionResumed"}` to `extension`

## Webcam setup

`webcam/participant_client.py` depends on `opencv-python`, `numpy`, `mediapipe`, and `websockets`.

`mediapipe` does not currently publish wheels for Python 3.13, so the webcam client must run on Python 3.12.

Example setup on macOS with Homebrew:

```bash
brew install python@3.12
python3.12 -m venv .venv312
. .venv312/bin/activate
python -m pip install -r webcam/requirements.txt
python webcam/participant_client.py
```

On the first `mediapipe` import, macOS may spend a short time building the matplotlib font cache.
*** Add File: /Users/yuanwenfei/Documents/tufts/first sem/hci /misty/webcam/requirements.txt
numpy==2.4.4
opencv-python==4.13.0.92
mediapipe==0.10.14
websockets==16.0
