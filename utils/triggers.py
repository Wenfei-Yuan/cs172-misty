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
    trigger_reason: str | None = None


class ExternalSignalReceiver:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._events = deque()
        self._current_text = ""
        self._current_mode = "full"
        self._last_trigger_reason: str | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._redirect_stop_event = threading.Event()
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
                trigger_reason = None
                if self.path == "/distraction/start":
                    event = "start"
                    if raw_body:
                        try:
                            body_payload = json.loads(raw_body.decode("utf-8"))
                            r = body_payload.get("reason")
                            if isinstance(r, str) and r.strip():
                                trigger_reason = r.strip()
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                elif self.path == "/distraction/stop":
                    event = "stop"
                elif self.path == "/shutdown":
                    event = "shutdown"
                elif self.path == "/current_text":
                    text_payload = ""
                    mode_payload = None
                    context_payload = {}
                    if raw_body:
                        try:
                            payload = json.loads(raw_body.decode("utf-8"))
                            for key in ("text", "currentText", "current_text"):
                                value = payload.get(key)
                                if isinstance(value, str) and value.strip():
                                    text_payload = value.strip()
                                    break
                            for key in ("activeMode", "active_mode", "mode"):
                                value = payload.get(key)
                                if isinstance(value, str) and value.strip().lower() in {"full", "para", "sentence"}:
                                    mode_payload = value.strip().lower()
                                    break
                            for source_key, context_key in (
                                ("fullArticleText", "fullArticleText"),
                                ("full_article_text", "fullArticleText"),
                                ("readSoFarText", "readSoFarText"),
                                ("read_so_far_text", "readSoFarText"),
                                ("upcomingText", "upcomingText"),
                                ("upcoming_text", "upcomingText"),
                                ("currentSentence", "currentSentence"),
                                ("current_sentence", "currentSentence"),
                            ):
                                value = payload.get(source_key)
                                if isinstance(value, str) and value.strip():
                                    context_payload[context_key] = value.strip()
                        except json.JSONDecodeError:
                            text_payload = ""
                    if text_payload or mode_payload:
                        with parent._lock:
                            if text_payload:
                                if context_payload:
                                    context_payload.setdefault("text", text_payload)
                                    parent._current_text = json.dumps(context_payload, ensure_ascii=False)
                                else:
                                    parent._current_text = text_payload
                            if mode_payload:
                                parent._current_mode = mode_payload
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "event": "reading_context_updated"}).encode("utf-8"))
                        return
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "reason": "missing_text"}).encode("utf-8"))
                    return

                if event:
                    with parent._lock:
                        parent._events.append(event)
                        if event == "start" and trigger_reason:
                            parent._last_trigger_reason = trigger_reason
                    if event in ("stop", "shutdown"):
                        parent._redirect_stop_event.set()
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
                        reason = self._last_trigger_reason
                        self._last_trigger_reason = None
                        return StartSignalResult(event="start", stale_stop_events_cleared=stale_stop_events_cleared, trigger_reason=reason)
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

    def consume_stop_or_shutdown(self) -> str | None:
        with self._lock:
            for event in list(self._events):
                if event == "shutdown":
                    self._events.remove("shutdown")
                    return "shutdown"

            stop_count = 0
            retained_events = deque()
            while self._events:
                event = self._events.popleft()
                if event == "stop":
                    stop_count += 1
                    continue
                if event == "start":
                    continue
                retained_events.append(event)
            self._events = retained_events
            if stop_count:
                return "stop"
        return None

    def wait_for_stop_or_shutdown(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self._stop_event.is_set():
            interrupt = self.consume_interrupt()
            if interrupt in {"stop", "shutdown"}:
                return interrupt
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)
        return "shutdown"

    def wait_for_stop_or_shutdown_only(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self._stop_event.is_set():
            interrupt = self.consume_stop_or_shutdown()
            if interrupt in {"stop", "shutdown"}:
                return interrupt
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)
        return "shutdown"

    def has_shutdown_event(self) -> bool:
        with self._lock:
            return "shutdown" in self._events

    def current_text(self) -> str:
        with self._lock:
            return self._current_text

    def current_mode(self) -> str:
        with self._lock:
            return self._current_mode

    def clear_redirect_stop(self) -> None:
        self._redirect_stop_event.clear()

    @property
    def redirect_stop_event(self) -> threading.Event:
        return self._redirect_stop_event




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

    def has_shutdown_event(self) -> bool:
        return False

    def wait_for_stop_or_shutdown_only(self, timeout_s: float) -> str | None:
        return self.consume_interrupt()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def clear_redirect_stop(self) -> None:
        pass

    @property
    def redirect_stop_event(self) -> threading.Event:
        return threading.Event()
