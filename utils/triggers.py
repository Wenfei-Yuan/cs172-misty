from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ExternalSignalReceiver:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._events = deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._server = None
        self._thread = None

    def start(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    _ = self.rfile.read(length)

                event = None
                if self.path == "/distraction/start":
                    event = "start"
                elif self.path == "/distraction/stop":
                    event = "stop"
                elif self.path == "/shutdown":
                    event = "shutdown"

                if event:
                    with parent._lock:
                        parent._events.append(event)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "event": event}).encode("utf-8"))
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def wait_for_start(self) -> str:
        while not self._stop_event.is_set():
            with self._lock:
                if self._events:
                    event = self._events.popleft()
                    if event in {"start", "shutdown"}:
                        return event
                    if event == "stop":
                        self._events.appendleft(event)
            time.sleep(0.1)
        return "shutdown"

    def end_received(self) -> bool:
        with self._lock:
            for event in list(self._events):
                if event == "stop":
                    self._events.remove("stop")
                    return True
        return False

    def has_end_event(self) -> bool:
        with self._lock:
            return "stop" in self._events

    def shutdown_received(self) -> bool:
        with self._lock:
            for event in list(self._events):
                if event == "shutdown":
                    self._events.remove("shutdown")
                    return True
        return False


class StubTrigger:
    def __init__(self, start_after_s: float = 3.0, stop_after_s: float = 8.0):
        self.start_after_s = start_after_s
        self.stop_after_s = stop_after_s
        self._start_time = time.time()

    def wait_for_start(self) -> str:
        while True:
            if time.time() - self._start_time >= self.start_after_s:
                return "start"
            time.sleep(0.1)

    def end_received(self) -> bool:
        return (time.time() - self._start_time) >= self.stop_after_s

    def shutdown_received(self) -> bool:
        return False

    def start(self) -> None:
        return

    def stop(self) -> None:
        return
