from __future__ import annotations
from datetime import datetime

import asyncio
import json
import os
from urllib import error, request

import websockets

WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
TRIGGER_HOST = os.getenv("TRIGGER_HOST", "127.0.0.1")
TRIGGER_PORT = int(os.getenv("TRIGGER_PORT", "5050"))

# ── Control-group mode ────────────────────────────────────────────────
# Set CONTROL_MODE=1 to run webcam-only monitoring without robot or
# extension interventions.  All distraction events are still logged
# to sessions/control_<timestamp>.json for post-study analysis.
CONTROL_MODE = os.getenv("CONTROL_MODE", "0").strip().lower() in ("1", "true", "yes")

EVENT_TO_PATH = {
    "start": "/distraction/start",
    "stop": "/distraction/stop",
    "shutdown": "/shutdown",
}
CURRENT_TEXT_PATH = "/current_text"
CLIENT_ROLES = {"webcam", "extension"}
REENGAGEMENT_ALIASES = {
    "re-engagement",
    "re_engagement",
    "reengagement",
    "re-engament",
    "re_engament",
    "reengament",
    "re-engagement=true",
    "re_engagement=true",
    "reengagement=true",
    "re-engament=true",
    "re_engament=true",
    "reengament=true",
}

connected_clients = set()
client_roles = {}
role_clients = {}
POSTURE_DISENGAGED_MESSAGE = "ROBOT_REDIRECT"

# ── Control-group session logger ──────────────────────────────────────

class ControlSessionLog:
    """Append-only JSON log for control-group distraction events."""

    def __init__(self) -> None:
        self._path: str | None = None
        self._events: list[dict] = []
        self._start_time: str | None = None

    def ensure_started(self) -> None:
        if self._path is not None:
            return
        ts = datetime.now()
        self._start_time = ts.isoformat()
        fname = f"control_{ts.strftime('%Y%m%d_%H%M%S')}.json"
        sessions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        self._path = os.path.join(sessions_dir, fname)
        self._flush()

    def log_event(self, event_type: str, reason: str | None = None) -> None:
        self.ensure_started()
        entry = {"ts": datetime.now().isoformat(), "event": event_type}
        if reason:
            entry["reason"] = reason
        self._events.append(entry)
        self._flush()

    def _flush(self) -> None:
        if self._path is None:
            return
        data = {
            "mode": "control",
            "start_time": self._start_time,
            "events": self._events,
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


control_log = ControlSessionLog()


def _log(sender: str, receiver: str, message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{sender} -> {receiver}] {message}")


class DisengagementTracker:
    def __init__(self) -> None:
        self.distraction_active = False

    def resolve_signal(self, signal: str) -> str | None:
        if signal == "disengaged_true":
            if self.distraction_active:
                return None
            self.distraction_active = True
            return "start"

        if signal == "disengaged_false":
            if not self.distraction_active:
                return None
            self.distraction_active = False
            return "stop"

        if signal == "start":
            self.distraction_active = True
        elif signal in {"stop", "shutdown"}:
            self.distraction_active = False
        return signal


tracker = DisengagementTracker()


def coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def parse_client_registration(message: str) -> str | None:
    try:
        payload = json.loads(message.strip())
    except json.JSONDecodeError:
        return None

    client = payload.get("client")
    if not isinstance(client, str):
        return None

    normalized = client.strip().lower()
    if normalized in CLIENT_ROLES:
        if set(payload.keys()) != {"client"}:
            return None
        return normalized
    return None


def register_client(websocket, role: str) -> None:
    previous_socket = role_clients.get(role)
    if previous_socket and previous_socket is not websocket:
        client_roles.pop(previous_socket, None)
    role_clients[role] = websocket
    client_roles[websocket] = role


def unregister_client(websocket) -> None:
    role = client_roles.pop(websocket, None)
    if role and role_clients.get(role) is websocket:
        role_clients.pop(role, None)


def parse_message_signal(message: str) -> str | None:
    text = message.strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in EVENT_TO_PATH:
        return lowered
    if lowered in REENGAGEMENT_ALIASES:
        return "disengaged_false"
    if lowered == "disengaged=true":
        return "disengaged_true"
    if lowered == "disengaged=false":
        return "disengaged_false"
    if lowered == "posture_disengaged=true":
        return "disengaged_true"
    if lowered == "posture_disengaged=false":
        return "disengaged_false"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    event = payload.get("event")
    if isinstance(event, str):
        normalized = event.strip().lower()
        if normalized in EVENT_TO_PATH:
            return normalized

    disengaged = coerce_bool(payload.get("disengaged"))
    if disengaged is not None:
        return "disengaged_true" if disengaged else "disengaged_false"

    posture_disengaged = coerce_bool(payload.get("posture_disengaged"))
    if posture_disengaged is not None:
        return "disengaged_true" if posture_disengaged else "disengaged_false"

    disengage = coerce_bool(payload.get("disengage"))
    if disengage is not None:
        return "disengaged_true" if disengage else "disengaged_false"

    for key in ("re_engagement", "re-engagement", "reengagement", "re_engament", "re-engament", "reengament"):
        if coerce_bool(payload.get(key)):
            return "disengaged_false"

    return None


def parse_current_text_message(message: str) -> str | None:
    text = message.strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    for key in ("text", "currentText", "current_text"):
        value = payload.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def is_extension_reading_state_message(message: str, source_role: str | None) -> bool:
    try:
        payload = json.loads(message.strip())
    except json.JSONDecodeError:
        return False

    message_type = payload.get("type")
    if not (isinstance(message_type, str) and message_type.strip().lower() == "readingstate"):
        return False

    client = payload.get("client")
    normalized_client = client.strip().lower() if isinstance(client, str) else None
    return source_role == "extension" or normalized_client == "extension"


def is_signal_allowed_for_role(signal: str, source_role: str | None) -> bool:
    # Only webcam is allowed to define recovery/re-engagement signals.
    if source_role == "extension" and signal == "disengaged_false":
        return False
    return True


def parse_message_signal_for_role(message: str, source_role: str | None = None) -> str | None:
    if is_extension_reading_state_message(message, source_role):
        return None
    signal = parse_message_signal(message)
    if not signal:
        return None
    if not is_signal_allowed_for_role(signal, source_role):
        return None
    return signal


def parse_message_event(message: str, source_role: str | None = None) -> str | None:
    signal = parse_message_signal_for_role(message, source_role)
    if not signal:
        return None
    return tracker.resolve_signal(signal)


def is_duplicate_active_start(signal: str | None, event: str | None) -> bool:
    return signal == "disengaged_true" and event is None and tracker.distraction_active


def trigger_url_for_event(event: str) -> str:
    return f"http://{TRIGGER_HOST}:{TRIGGER_PORT}{EVENT_TO_PATH[event]}"


def _extract_disengage_reason(message: str) -> str | None:
    """Extract the webcam 'reason' field from a JSON message."""
    try:
        payload = json.loads(message.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    reason = payload.get("reason")
    return reason if isinstance(reason, str) and reason.strip() else None


def post_trigger_event(event: str, reason: str | None = None) -> tuple[bool, str]:
    endpoint = trigger_url_for_event(event)
    body = json.dumps({"reason": reason}).encode("utf-8") if reason else b"{}"
    req = request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8") or "{}"
        return True, body
    except error.URLError as exc:
        return False, str(exc)


def post_current_text(text: str) -> tuple[bool, str]:
    endpoint = f"http://{TRIGGER_HOST}:{TRIGGER_PORT}{CURRENT_TEXT_PATH}"
    body = json.dumps({"text": text}).encode("utf-8")
    req = request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=5) as response:
            payload = response.read().decode("utf-8") or "{}"
        return True, payload
    except error.URLError as exc:
        return False, str(exc)


async def forward_event(event: str, reason: str | None = None) -> tuple[bool, str]:
    return await asyncio.to_thread(post_trigger_event, event, reason)


async def forward_current_text(text: str) -> tuple[bool, str]:
    return await asyncio.to_thread(post_current_text, text)


async def notify_extension_posture_disengagement(source_role: str | None, signal: str | None, event: str | None, forwarded: bool) -> None:
    if signal != "disengaged_true" or event != "start":
        return
    if CONTROL_MODE:
        _log("server", "console", "[对照组] 跳过 extension 高亮通知")
        return
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        _log("server", "console", "extension 通知未发送: 未找到已注册的 extension 客户端")
        return
    # Notify extension regardless of whether the robot pipeline (port 5050) responded.
    # Extension highlight should fire as long as webcam detects distraction.
    if not forwarded:
        _log("server", "console", "pipeline 转发失败，但仍通知 extension 高亮")
    _log("server", "extension", f"WebSocket 定向发送 -> extension: {POSTURE_DISENGAGED_MESSAGE}")
    try:
        await extension_socket.send(POSTURE_DISENGAGED_MESSAGE)
    except websockets.ConnectionClosed:
        _log("server", "console", "extension WebSocket 在发送前关闭，通知未送达")


async def handler(websocket):
    remote = websocket.remote_address or ("unknown", 0)
    client_ip = remote[0]
    _log(client_ip, "server", f"客户端已连接: {client_ip}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            source_role = client_roles.get(websocket, "unregistered")
            _log(source_role, "server", f"收到消息 from {client_ip}: {message}")
            registration = parse_client_registration(message)
            if registration:
                register_client(websocket, registration)
                _log(client_ip, "server", f"客户端已注册: {client_ip} -> {registration}")
                _log("server", registration, f"WebSocket 定向发送 -> {registration}: registration_ack")
                await websocket.send(json.dumps({"ok": True, "type": "registered", "client": registration}))
                continue

            receiver_role = client_roles.get(websocket, "unregistered")
            signal = parse_message_signal_for_role(message, receiver_role)
            _saved_active = tracker.distraction_active
            event = parse_message_event(message, receiver_role)
            if not event:
                current_text = parse_current_text_message(message)
                if current_text:
                    if CONTROL_MODE:
                        response = {"ok": True, "type": "current_text", "mode": "control"}
                    else:
                        ok, detail = await forward_current_text(current_text)
                        response = {"ok": ok, "type": "current_text"}
                        if ok:
                            response["trigger_response"] = detail
                        else:
                            response["reason"] = detail
                    await websocket.send(json.dumps(response))
                    if is_duplicate_active_start(signal, event):
                        _log(source_role, "console", "检测到重复的 disengaged_true，当前已处于 distraction_active，跳过重复 start 转发到 5050")
                    continue

            if not event:
                reason = "state_not_changed" if signal else "unrecognized_message"
                _log(source_role, "console", f"只是收到消息但没触发: {reason}")
                if is_duplicate_active_start(signal, event):
                    _log(source_role, "console", "检测到重复的 disengaged_true，当前已处于 distraction_active，跳过重复 start 转发到 5050")
                await websocket.send(json.dumps({"ok": False, "reason": reason}))
                continue

            current_text = parse_current_text_message(message)
            if current_text and not CONTROL_MODE:
                text_ok, text_detail = await forward_current_text(current_text)

            disengage_reason = _extract_disengage_reason(message) if event == "start" else None

            if CONTROL_MODE:
                # 对照组: 只记录，不转发到 pipeline，不触发机器人
                control_log.log_event(event, reason=disengage_reason)
                _log("server", "console", f"[对照组] 记录事件: {event} (reason={disengage_reason})")
                response = {"ok": True, "event": event, "mode": "control"}
                _log("server", receiver_role, f"WebSocket 定向发送 -> {receiver_role}: {json.dumps(response, ensure_ascii=False)}")
                await websocket.send(json.dumps(response))
                await notify_extension_posture_disengagement(receiver_role, signal, event, False)
                continue

            ok, detail = await forward_event(event, reason=disengage_reason)
            response = {"ok": ok, "event": event}
            if ok:
                response["trigger_response"] = detail
                _log("server", "misty", f"消息已转发到机器人流程: {event}")
                _log("server", "misty", f"已转发事件 {event} -> {trigger_url_for_event(event)}")
            else:
                tracker.distraction_active = _saved_active
                response["reason"] = detail
                _log("server", "console", f"只是收到消息但没触发: {event} -> {detail}")
                _log("server", "console", f"转发事件失败 {event}: {detail}")
            _log("server", receiver_role, f"WebSocket 定向发送 -> {receiver_role}: {json.dumps(response, ensure_ascii=False)}")
            await websocket.send(json.dumps(response))
            await notify_extension_posture_disengagement(receiver_role, signal, event, ok)

    except websockets.ConnectionClosed:
        _log(client_ip, "server", f"客户端断开: {client_ip}")
    finally:
        connected_clients.discard(websocket)
        unregister_client(websocket)


async def main():
    server = await websockets.serve(handler, WS_HOST, WS_PORT)
    _log("server", "console", "WebSocket bridge 已启动")
    if CONTROL_MODE:
        _log("server", "console", "══════ 对照组模式 ══════")
        _log("server", "console", "  • 不转发事件到 pipeline（无机器人干预）")
        _log("server", "console", "  • 不发送 ROBOT_REDIRECT 到扩展（无页面高亮）")
        _log("server", "console", "  • 走神事件记录到 sessions/control_*.json")
    else:
        _log("server", "console", f"触发器目标: http://{TRIGGER_HOST}:{TRIGGER_PORT}")
    _log("server", "console", f"监听地址: ws://{WS_HOST}:{WS_PORT}")
    _log("server", "console", '客户端可先发送 JSON 注册身份: {"client": "webcam"} 或 {"client": "extension"}')
    _log("server", "console", "支持消息: start, stop, shutdown, posture_disengaged=true/false, re-engagement")
    _log("server", "console", '也支持 JSON: {"event": "start"} 或 {"posture_disengaged": true}')
    await server.wait_closed()



if __name__ == "__main__":
    asyncio.run(main())


    
