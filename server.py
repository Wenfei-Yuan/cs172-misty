from __future__ import annotations

import asyncio
import json
import os
from urllib import error, request

import websockets

WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
TRIGGER_HOST = os.getenv("TRIGGER_HOST", "127.0.0.1")
TRIGGER_PORT = int(os.getenv("TRIGGER_PORT", "5050"))

EVENT_TO_PATH = {
    "start": "/distraction/start",
    "stop": "/distraction/stop",
    "shutdown": "/shutdown",
}
CURRENT_TEXT_PATH = "/current_text"
CLIENT_ROLES = {"webcam", "extension"}
DISENGAGEMENT_START_ALIASES = {
    "covert_disengagement=true",
    "covert_disengagemnt=true",
    "covert_disengagemnt=ture",
}
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
REENGAGEMENT_MESSAGE = json.dumps({"eventName": "AttentionResumed"})


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
_last_covert_disengagement: dict = {}


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


def _get_covert_disengagement_value(message: str):
    """Return the covert_disengagemnt bool value if the field is present, else None."""
    try:
        payload = json.loads(message.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    for key in ("covert_disengagemnt", "covert_disengagement"):
        if key in payload:
            return coerce_bool(payload[key])
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
    if lowered in DISENGAGEMENT_START_ALIASES:
        return "disengaged_true"
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

    covert_disengagement = coerce_bool(payload.get("covert_disengagement"))
    if covert_disengagement:
        return "disengaged_true"

    covert_disengagemnt = coerce_bool(payload.get("covert_disengagemnt"))
    if covert_disengagemnt:
        return "disengaged_true"

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


def is_signal_allowed_for_role(signal: str, source_role: str | None) -> bool:
    # Only webcam is allowed to define recovery/re-engagement signals.
    if source_role == "extension" and signal == "disengaged_false":
        return False
    return True


def parse_message_signal_for_role(message: str, source_role: str | None = None) -> str | None:
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


def trigger_url_for_event(event: str) -> str:
    return f"http://{TRIGGER_HOST}:{TRIGGER_PORT}{EVENT_TO_PATH[event]}"


def post_trigger_event(event: str) -> tuple[bool, str]:
    endpoint = trigger_url_for_event(event)
    req = request.Request(endpoint, data=b"{}", method="POST")
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


async def forward_event(event: str) -> tuple[bool, str]:
    return await asyncio.to_thread(post_trigger_event, event)


async def forward_current_text(text: str) -> tuple[bool, str]:
    return await asyncio.to_thread(post_current_text, text)


async def notify_extension_redirect(event: str, forwarded: bool) -> None:
    if event != "stop":
        return
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        print("extension 通知未发送: 未找到已注册的 extension 客户端")
        return
    print(f"WebSocket 定向发送 -> extension: {REENGAGEMENT_MESSAGE}")
    await extension_socket.send(REENGAGEMENT_MESSAGE)


async def notify_extension_posture_disengagement(source_role: str | None, signal: str | None, event: str | None, forwarded: bool) -> None:
    if signal != "disengaged_true" or event != "start":
        return
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        print("extension 通知未发送: 未找到已注册的 extension 客户端")
        return
    print(f"WebSocket 定向发送 -> extension: {POSTURE_DISENGAGED_MESSAGE}")
    await extension_socket.send(POSTURE_DISENGAGED_MESSAGE)


async def handler(websocket):
    remote = websocket.remote_address or ("unknown", 0)
    client_ip = remote[0]
    print(f"客户端已连接: {client_ip}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            _cv = _get_covert_disengagement_value(message)
            if _cv is None:
                print(f"收到消息 from {client_ip}: {message}")
            elif _cv != _last_covert_disengagement.get(websocket):
                _last_covert_disengagement[websocket] = _cv
                print(f"收到消息 from {client_ip}: {message}")
            registration = parse_client_registration(message)
            if registration:
                register_client(websocket, registration)
                print(f"客户端已注册: {client_ip} -> {registration}")
                print(f"WebSocket 定向发送 -> {registration}: registration_ack")
                await websocket.send(json.dumps({"ok": True, "type": "registered", "client": registration}))
                continue

            receiver_role = client_roles.get(websocket, "unregistered")
            signal = parse_message_signal_for_role(message, receiver_role)
            event = parse_message_event(message, receiver_role)
            if not event:
                current_text = parse_current_text_message(message)
                if current_text:
                    ok, detail = await forward_current_text(current_text)
                    response = {"ok": ok, "type": "current_text"}
                    if ok:
                        response["trigger_response"] = detail
                    else:
                        response["reason"] = detail
                    await websocket.send(json.dumps(response))
                    # Still notify extension if distraction is active
                    if signal == "disengaged_true" and tracker.distraction_active:
                        await notify_extension_posture_disengagement(receiver_role, signal, "start", False)
                    continue

            if not event:
                reason = "state_not_changed" if signal else "unrecognized_message"
                print(f"只是收到消息但没触发: {reason}")
                await websocket.send(json.dumps({"ok": False, "reason": reason}))
                # Even if state didn't change, notify extension if distraction is active
                if signal == "disengaged_true" and tracker.distraction_active:
                    await notify_extension_posture_disengagement(receiver_role, signal, "start", False)
                continue

            current_text = parse_current_text_message(message)
            if current_text:
                text_ok, text_detail = await forward_current_text(current_text)

            ok, detail = await forward_event(event)
            response = {"ok": ok, "event": event}
            if ok:
                response["trigger_response"] = detail
                print(f"消息已转发到机器人流程: {event}")
                print(f"已转发事件 {event} -> {trigger_url_for_event(event)}")
            else:
                response["reason"] = detail
                print(f"只是收到消息但没触发: {event} -> {detail}")
                print(f"转发事件失败 {event}: {detail}")
            print(f"WebSocket 定向发送 -> {receiver_role}: {json.dumps(response, ensure_ascii=False)}")
            await websocket.send(json.dumps(response))
            await notify_extension_posture_disengagement(receiver_role, signal, event, ok)
            await notify_extension_redirect(event, ok)

    except websockets.exceptions.ConnectionClosed:
        print(f"客户端断开: {client_ip}")
    finally:
        connected_clients.discard(websocket)
        unregister_client(websocket)
        _last_covert_disengagement.pop(websocket, None)


async def main():
    server = await websockets.serve(handler, WS_HOST, WS_PORT)
    print("WebSocket bridge 已启动")
    print(f"监听地址: ws://{WS_HOST}:{WS_PORT}")
    print(f"触发器目标: http://{TRIGGER_HOST}:{TRIGGER_PORT}")
    print('客户端可先发送 JSON 注册身份: {"client": "webcam"} 或 {"client": "extension"}')
    print("支持消息: start, stop, shutdown, posture_disengaged=true/false, covert_disengagement=true, re-engagement")
    print('也支持 JSON: {"event": "start"} 或 {"posture_disengaged": true}')
    await server.wait_closed()



if __name__ == "__main__":
    asyncio.run(main())


    
