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

DISENGAGED_STOP_STREAK = int(os.getenv("DISENGAGED_STOP_STREAK", "6"))
CLIENT_ROLES = {"webcam", "extension"}

connected_clients = set()
client_roles = {}
role_clients = {}
ROBOT_REDIRECT_MESSAGE = "ROBOT_REDIRECT"
POSTURE_DISENGAGEMENT_MESSAGE = "posture_disengagement=true"


class DisengagementTracker:
    def __init__(self, stop_streak: int = DISENGAGED_STOP_STREAK) -> None:
        self.stop_streak = stop_streak
        self.distraction_active = False
        self.engaged_streak = 0

    def resolve_signal(self, signal: str) -> str | None:
        if signal == "disengaged_true":
            self.engaged_streak = 0
            if self.distraction_active:
                return None
            self.distraction_active = True
            return "start"

        if signal == "disengaged_false":
            if not self.distraction_active:
                return None
            self.engaged_streak += 1
            if self.engaged_streak < self.stop_streak:
                return None
            self.distraction_active = False
            self.engaged_streak = 0
            return "stop"

        self.engaged_streak = 0
        if signal == "start":
            self.distraction_active = True
        elif signal in {"stop", "shutdown"}:
            self.distraction_active = False
        return signal


tracker = DisengagementTracker()


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
    if lowered == "disengaged=true":
        return "disengaged_true"
    if lowered == "disengaged=false":
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

    disengaged = payload.get("disengaged")
    if isinstance(disengaged, bool):
        return "disengaged_true" if disengaged else "disengaged_false"

    return None


def parse_message_event(message: str) -> str | None:
    signal = parse_message_signal(message)
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


async def forward_event(event: str) -> tuple[bool, str]:
    return await asyncio.to_thread(post_trigger_event, event)


async def notify_extension_redirect(event: str, forwarded: bool) -> None:
    if event != "stop" or not forwarded:
        return
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        print("extension 通知未发送: 未找到已注册的 extension 客户端")
        return
    print(f"WebSocket 定向发送 -> extension: {ROBOT_REDIRECT_MESSAGE}")
    await extension_socket.send(ROBOT_REDIRECT_MESSAGE)


async def notify_extension_posture_disengagement(signal: str | None, event: str | None, forwarded: bool) -> None:
    if signal != "disengaged_true" or event != "start" or not forwarded:
        return
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        print("extension 通知未发送: 未找到已注册的 extension 客户端")
        return
    print(f"WebSocket 定向发送 -> extension: {POSTURE_DISENGAGEMENT_MESSAGE}")
    await extension_socket.send(POSTURE_DISENGAGEMENT_MESSAGE)


async def handler(websocket):
    remote = websocket.remote_address or ("unknown", 0)
    client_ip = remote[0]
    print(f"客户端已连接: {client_ip}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            print(f"收到消息 from {client_ip}: {message}")
            registration = parse_client_registration(message)
            if registration:
                register_client(websocket, registration)
                print(f"客户端已注册: {client_ip} -> {registration}")
                print(f"WebSocket 定向发送 -> {registration}: registration_ack")
                await websocket.send(json.dumps({"ok": True, "type": "registered", "client": registration}))
                continue

            signal = parse_message_signal(message)
            event = parse_message_event(message)
            if not event:
                reason = "state_not_changed" if signal else "unrecognized_message"
                print(f"只是收到消息但没触发: {reason}")
                await websocket.send(json.dumps({"ok": False, "reason": reason}))
                continue

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
            receiver_role = client_roles.get(websocket, "unregistered")
            print(f"WebSocket 定向发送 -> {receiver_role}: {json.dumps(response, ensure_ascii=False)}")
            await websocket.send(json.dumps(response))
            await notify_extension_posture_disengagement(signal, event, ok)
            await notify_extension_redirect(event, ok)

    except websockets.exceptions.ConnectionClosed:
        print(f"客户端断开: {client_ip}")
    finally:
        connected_clients.discard(websocket)
        unregister_client(websocket)


async def main():
    server = await websockets.serve(handler, WS_HOST, WS_PORT)
    print("WebSocket bridge 已启动")
    print(f"监听地址: ws://{WS_HOST}:{WS_PORT}")
    print(f"触发器目标: http://{TRIGGER_HOST}:{TRIGGER_PORT}")
    print('客户端可先发送 JSON 注册身份: {"client": "webcam"} 或 {"client": "extension"}')
    print("支持消息: start, stop, shutdown, disengaged=true, disengaged=false")
    print('也支持 JSON: {"event": "start"} 或 {"disengaged": true}')
    await server.wait_closed()



if __name__ == "__main__":
    asyncio.run(main())


    