from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class StartSignalResult:
    event: str
    stale_stop_events_cleared: int = 0


class ExternalSignalReceiver:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._events = deque()
        self._current_texts = deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._server = None
        self._thread = None

    def start(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = b""
                if length:
                    raw_body = self.rfile.read(length)

                event = None
                if self.path == "/distraction/start":
                    event = "start"
                elif self.path == "/distraction/stop":
                    event = "stop"
                elif self.path == "/shutdown":
                    event = "shutdown"
                elif self.path == "/current_text":
                    try:
                        payload = json.loads(raw_body.decode("utf-8") or "{}")
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        payload = {}
                    current_text = payload.get("currentText")
                    if isinstance(current_text, str) and current_text.strip():
                        with parent._lock:
                            parent._current_texts.append(current_text.strip())
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "event": "current_text"}).encode("utf-8"))
                        return

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

    def wait_for_start(self) -> StartSignalResult:
        stale_stop_events_cleared = 0
        while not self._stop_event.is_set():
            with self._lock:
                if self._events:
                    retained_events = deque()
                    start_seen = False
                    shutdown_seen = False

                    while self._events:
                        event = self._events.popleft()
                        if event == "stop":
                            stale_stop_events_cleared += 1
                            continue
                        if event == "shutdown":
                            shutdown_seen = True
                            continue
                        if event == "start":
                            start_seen = True
                            continue
                        retained_events.append(event)

                    self._events = retained_events

                    if shutdown_seen:
                        return StartSignalResult(event="shutdown", stale_stop_events_cleared=stale_stop_events_cleared)
                    if start_seen:
                        return StartSignalResult(event="start", stale_stop_events_cleared=stale_stop_events_cleared)
            time.sleep(0.1)
        return StartSignalResult(event="shutdown", stale_stop_events_cleared=stale_stop_events_cleared)

    def consume_interrupt(self) -> str | None:
        with self._lock:
            for event in list(self._events):
                if event == "shutdown":
                    self._events.remove("shutdown")
                    return "shutdown"

            stop_count = 0
            retained_events = deque()
            start_retained = False
            while self._events:
                event = self._events.popleft()
                if event == "stop":
                    stop_count += 1
                    continue
                if event == "start":
                    if start_retained:
                        continue
                    start_retained = True
                    retained_events.append(event)
                    continue
                retained_events.append(event)
            self._events = retained_events
            if stop_count:
                return "stop"
        return None

    def end_received(self) -> bool:
        with self._lock:
            removed = False
            retained_events = deque()
            while self._events:
                event = self._events.popleft()
                if event == "stop":
                    removed = True
                    continue
                retained_events.append(event)
            self._events = retained_events
            if removed:
                return True
        return False

    def has_end_event(self) -> bool:
        with self._lock:
            return "stop" in self._events

    def has_shutdown_event(self) -> bool:
        with self._lock:
            return "shutdown" in self._events

    def shutdown_received(self) -> bool:
        with self._lock:
            for event in list(self._events):
                if event == "shutdown":
                    self._events.remove("shutdown")
                    return True
        return False

    def consume_current_text(self) -> str | None:
        with self._lock:
            if not self._current_texts:
                return None
            return self._current_texts.popleft()


class StubTrigger:
    def __init__(self, start_after_s: float = 3.0, stop_after_s: float = 8.0):
        self.start_after_s = start_after_s
        self.stop_after_s = stop_after_s
        self._waiting_since = time.time()
        self._active_since = None

    def wait_for_start(self) -> StartSignalResult:
        while True:
            if self._active_since is None and (time.time() - self._waiting_since) >= self.start_after_s:
                self._active_since = time.time()
                return StartSignalResult(event="start")
            time.sleep(0.1)

    def consume_interrupt(self) -> str | None:
        if self._active_since is not None and (time.time() - self._active_since) >= self.stop_after_s:
            self._active_since = None
            self._waiting_since = time.time()
            return "stop"
        return None

    def end_received(self) -> bool:
        return self.consume_interrupt() == "stop"

    def shutdown_received(self) -> bool:
        return False

    def has_shutdown_event(self) -> bool:
        return False

    def start(self) -> None:
        return

    def stop(self) -> None:
        return
