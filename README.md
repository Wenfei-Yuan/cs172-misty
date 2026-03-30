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

- `disengaged=true`
- `disengaged=false`
- `start`
- `stop`
- `shutdown`

Supported JSON messages:

```json
{"disengaged": true}
```

```json
{"disengaged": false}
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
posture_disengagement=true
```

When the distraction end condition is satisfied and `stop` is successfully forwarded, the extension receives:

```text
ROBOT_REDIRECT
```

### Distraction state rules

- The first `disengaged=true` starts a distraction event and is forwarded to Misty as `POST /distraction/start` on port `5050`
- While already in distraction, repeated `disengaged=true` messages do not retrigger start
- After distraction has started, `disengaged=false` must be observed for 6 consecutive messages before the bridge forwards `POST /distraction/stop`
- If a `disengaged=true` arrives before the count reaches 6, the recovery count resets

### Typical flow

1. `extension` connects and sends `{"client":"extension"}`
2. `webcam` connects and sends `{"client":"webcam"}`
3. `webcam` sends `disengaged=true`
4. Server forwards `start` to Misty on port `5050`
5. Server returns a JSON success response to `webcam`
6. Server sends `posture_disengagement=true` to `extension`
7. `webcam` later sends consecutive `disengaged=false` messages
8. On the 6th consecutive `disengaged=false`, server forwards `stop` to Misty on port `5050`
9. Server returns a JSON success response to `webcam`
10. Server sends `ROBOT_REDIRECT` to `extension`
