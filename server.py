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

connected_clients = set()


def parse_message_event(message: str) -> str | None:
    text = message.strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in EVENT_TO_PATH:
        return lowered
    if lowered == "disengaged=true":
        return "start"
    if lowered == "disengaged=false":
        return "stop"

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
        return "start" if disengaged else "stop"

    return None


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


async def handler(websocket):
    remote = websocket.remote_address or ("unknown", 0)
    client_ip = remote[0]
    print(f"客户端已连接: {client_ip}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            print(f"收到消息 from {client_ip}: {message}")
            event = parse_message_event(message)
            if not event:
                await websocket.send(json.dumps({"ok": False, "reason": "unrecognized_message"}))
                continue

            ok, detail = await forward_event(event)
            response = {"ok": ok, "event": event}
            if ok:
                response["trigger_response"] = detail
                print(f"已转发事件 {event} -> {trigger_url_for_event(event)}")
            else:
                response["reason"] = detail
                print(f"转发事件失败 {event}: {detail}")
            await websocket.send(json.dumps(response))

    except websockets.exceptions.ConnectionClosed:
        print(f"客户端断开: {client_ip}")
    finally:
        connected_clients.discard(websocket)


async def main():
    server = await websockets.serve(handler, WS_HOST, WS_PORT)
    print("WebSocket bridge 已启动")
    print(f"监听地址: ws://{WS_HOST}:{WS_PORT}")
    print(f"触发器目标: http://{TRIGGER_HOST}:{TRIGGER_PORT}")
    print("支持消息: start, stop, shutdown, disengaged=true, disengaged=false")
    print('也支持 JSON: {"event": "start"} 或 {"disengaged": true}')
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())