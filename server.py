from __future__ import annotations
from datetime import datetime

import asyncio
import json
import os
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import websockets

WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
COMMAND_HTTP_HOST = os.getenv("COMMAND_HTTP_HOST", "127.0.0.1")
COMMAND_HTTP_PORT = int(os.getenv("COMMAND_HTTP_PORT", "8766"))
TRIGGER_HOST = os.getenv("TRIGGER_HOST", "127.0.0.1")
TRIGGER_PORT = int(os.getenv("TRIGGER_PORT", "5050"))
BRIDGE_HTTP_PORT = int(os.getenv("BRIDGE_HTTP_PORT", "9877"))
CONTROL_PARTICIPANT_ID = os.getenv("CONTROL_PARTICIPANT_ID", "control").strip() or "control"

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
CLIENT_ROLES = {"webcam", "extension", "baseline"}
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
HIGHLIGHT_CURRENT_SENTENCE_MESSAGE = {
    "type": "HIGHLIGHT_CURRENT_SENTENCE",
    "durationMs": 10000,
    "source": "stage2_confused_sound",
}
OFFER_READING_MODE_MESSAGE = {
    "type": "OFFER_READING_MODE",
    "recommendedMode": "para",
    "source": "stage4_fatigue_support",
}

# ── Control-group session management ──────────────────────────────────

_ROOT_DIR = Path(__file__).resolve().parent
_SESSIONS_DIR = _ROOT_DIR / "sessions"


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _gen_baseline_id(participant_id: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    return f"baseline_{date_str}_{participant_id}_{uuid.uuid4().hex[:6]}"


def _process_baseline_events(raw_events: list[dict], session_end_time: str) -> list[dict]:
    distraction_events: list[dict] = []
    current_start: str | None = None
    current_reason: str | None = None
    idx = 0

    for event in raw_events:
        event_type = event.get("event")
        if event_type == "start":
            current_start = event.get("ts")
            current_reason = event.get("reason")
            continue

        if event_type == "stop" and current_start:
            idx += 1
            start_dt = datetime.fromisoformat(current_start)
            end_dt = datetime.fromisoformat(event["ts"])
            duration_s = round((end_dt - start_dt).total_seconds(), 2)
            distraction_events.append({
                "event_index": idx,
                "distraction_start_time": current_start,
                "distraction_end_time": event["ts"],
                "distraction_duration_s": duration_s,
                "distraction_end_signal_received": True,
                "exit_reason": "self_recovered",
                "trigger_source": current_reason or "unknown",
                "voice_prompt_used": False,
                "voice_prompt_count": 0,
                "gaze_detected": True,
                "gaze_latency_s": duration_s,
            })
            current_start = None
            current_reason = None

    if current_start:
        idx += 1
        start_dt = datetime.fromisoformat(current_start)
        end_dt = datetime.fromisoformat(session_end_time)
        duration_s = round((end_dt - start_dt).total_seconds(), 2)
        distraction_events.append({
            "event_index": idx,
            "distraction_start_time": current_start,
            "distraction_end_time": session_end_time,
            "distraction_duration_s": duration_s,
            "distraction_end_signal_received": False,
            "exit_reason": "session_ended",
            "trigger_source": current_reason or "unknown",
            "voice_prompt_used": False,
            "voice_prompt_count": 0,
            "gaze_detected": False,
            "gaze_latency_s": None,
        })

    return distraction_events


def _refresh_analysis_csvs() -> None:
    from generate_csv import (
        INTERVENTION_HEADER,
        SESSION_HEADER,
        build_intervention_events,
        build_session_summary,
        load_sessions,
        write_csv,
    )

    sessions = load_sessions()
    intervention_rows = build_intervention_events(sessions)
    summary_rows = build_session_summary(sessions, intervention_rows)
    write_csv(_ROOT_DIR / "intervention_events.csv", INTERVENTION_HEADER, intervention_rows)
    write_csv(_ROOT_DIR / "session_summary.csv", SESSION_HEADER, summary_rows)


class ControlSessionManager:
    """Create baseline-format no_system session logs and refresh summary CSVs."""

    def __init__(self, sessions_dir: Path, csv_refresher=None) -> None:
        self._sessions_dir = sessions_dir
        self._csv_refresher = csv_refresher or (lambda: None)
        self._active_session: dict | None = None

    def has_active_session(self) -> bool:
        return self._active_session is not None

    def status(self) -> dict:
        session = self._active_session
        if session is None:
            return {
                "control_mode": CONTROL_MODE,
                "active": False,
                "session_id": None,
                "participant_id": None,
                "start_time": None,
                "num_events": 0,
            }
        return {
            "control_mode": CONTROL_MODE,
            "active": True,
            "session_id": session["session_id"],
            "participant_id": session["participant_id"],
            "start_time": session["start_time"],
            "num_events": len(session["raw_events"]),
        }

    def start_session(self, participant_id: str, start_time: str | None = None) -> dict:
        normalized = participant_id.strip()
        if not normalized:
            raise ValueError("participant_id required")
        if self._active_session is not None:
            raise RuntimeError("session already active")

        session_id = _gen_baseline_id(normalized)
        resolved_start_time = start_time or _local_iso()
        self._active_session = {
            "session_id": session_id,
            "participant_id": normalized,
            "start_time": resolved_start_time,
            "raw_events": [],
        }
        return {"session_id": session_id, "participant_id": normalized, "start_time": resolved_start_time}

    def log_event(self, event_type: str, reason: str | None = None) -> bool:
        if self._active_session is None:
            return False
        entry = {"ts": _local_iso(), "event": event_type}
        if reason:
            entry["reason"] = reason
        self._active_session["raw_events"].append(entry)
        return True

    def stop_session(self, end_time: str | None = None) -> dict:
        if self._active_session is None:
            raise RuntimeError("no active session")

        session = self._active_session
        self._active_session = None
        resolved_end_time = end_time or _local_iso()
        raw_events = session["raw_events"]
        events = []
        for event in raw_events:
            name = "disengagement_start" if event.get("event") == "start" else "disengagement_end"
            entry = {"timestamp": event.get("ts", ""), "name": name, "payload": {}}
            if event.get("reason"):
                entry["payload"]["reason"] = event["reason"]
            events.append(entry)

        distraction_events = _process_baseline_events(raw_events, resolved_end_time)
        session_data = {
            "session_id": session["session_id"],
            "participant_id": session["participant_id"],
            "condition": "no_system",
            "start_time": session["start_time"],
            "end_time": resolved_end_time,
            "events": events,
            "distraction_events": distraction_events,
            "total_distraction_count": len(distraction_events),
            "total_voice_prompts": 0,
        }

        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._sessions_dir / f"{session['session_id']}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        self._csv_refresher()
        return {
            "session_id": session["session_id"],
            "participant_id": session["participant_id"],
            "start_time": session["start_time"],
            "end_time": resolved_end_time,
            "num_distractions": len(distraction_events),
            "session_path": str(output_path),
        }


control_session_manager = ControlSessionManager(_SESSIONS_DIR, _refresh_analysis_csvs)


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


def _notify_bridge_recording(action: str, username: str = "control") -> None:
    """Tell the webcam bridge to start/stop video recording."""
    if action == "start":
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/start"
        data = json.dumps({"username": username}).encode()
    else:
        url = f"http://127.0.0.1:{BRIDGE_HTTP_PORT}/api/recording/stop"
        data = b"{}"
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            _log("server", "console", f"[对照组] Recording {action}: {result}")
    except Exception as exc:
        _log("server", "console", f"[对照组] Recording {action} failed (bridge may not be running): {exc}")


def register_client(websocket, role: str) -> None:
    previous_socket = role_clients.get(role)
    if previous_socket and previous_socket is not websocket:
        client_roles.pop(previous_socket, None)
    role_clients[role] = websocket
    client_roles[websocket] = role
    if CONTROL_MODE and role == "webcam":
        _log("server", "console", "[对照组] webcam 已注册，等待研究者开始 session")


def unregister_client(websocket) -> None:
    role = client_roles.pop(websocket, None)
    if role and role_clients.get(role) is websocket:
        role_clients.pop(role, None)
    if CONTROL_MODE and role == "webcam":
        _log("server", "console", "[对照组] webcam 已断开")


async def send_role_command(role: str, payload: dict) -> tuple[bool, str]:
    target = role_clients.get(role)
    if target is None:
        return False, f"{role}_not_connected"
    try:
        await target.send(json.dumps(payload))
        return True, "ok"
    except websockets.ConnectionClosed:
        unregister_client(target)
        return False, f"{role}_connection_closed"


async def handle_control_session_command(payload: dict) -> dict:
    action = payload.get("type")
    if not CONTROL_MODE:
        return {"ok": False, "detail": "control_mode_disabled", "control_mode": False}

    if action == "control_session_status":
        return {"ok": True, **control_session_manager.status()}

    if action == "control_session_start":
        participant_id = str(
            payload.get("participant_id")
            or payload.get("username")
            or CONTROL_PARTICIPANT_ID
        ).strip()
        if not participant_id:
            return {"ok": False, "detail": "participant_id required", **control_session_manager.status()}

        if control_session_manager.has_active_session():
            return {"ok": False, "detail": "session already active", **control_session_manager.status()}

        session_start_ts = _local_iso()

        ok, reason = await send_role_command("webcam", {"type": "start_camera"})
        if not ok:
            return {"ok": False, "detail": reason, **control_session_manager.status()}

        ok, reason = await send_role_command(
            "webcam",
            {
                "type": "recording_start",
                "username": participant_id,
                "session_start_ts": session_start_ts,
            },
        )
        if not ok:
            await send_role_command("webcam", {"type": "stop_camera"})
            return {"ok": False, "detail": reason, **control_session_manager.status()}

        tracker.distraction_active = False
        started = control_session_manager.start_session(participant_id, start_time=session_start_ts)
        _log("server", "console", f"[对照组] Session started: {started['session_id']} (participant={participant_id})")
        return {"ok": True, **control_session_manager.status()}

    if action == "control_session_stop":
        if not control_session_manager.has_active_session():
            return {"ok": False, "detail": "no active session", **control_session_manager.status()}

        session_end_ts = _local_iso()
        await send_role_command("webcam", {"type": "recording_stop", "session_end_ts": session_end_ts})
        await send_role_command("webcam", {"type": "stop_camera"})
        tracker.distraction_active = False
        result = control_session_manager.stop_session(end_time=session_end_ts)
        _log("server", "console", f"[对照组] Session saved: {result['session_path']} ({result['num_distractions']} distractions)")
        return {"ok": True, **control_session_manager.status(), **result}

    return {"ok": False, "detail": f"unknown control action: {action}", **control_session_manager.status()}


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


def parse_reading_context_message(message: str) -> dict | None:
    text = message.strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    context = {}
    for key in ("text", "currentText", "current_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            context["text"] = value.strip()
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
            context[context_key] = value.strip()

    for key in ("activeMode", "active_mode", "mode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower() in {"full", "para", "sentence"}:
            context["activeMode"] = value.strip().lower()
            break

    return context or None


def has_extended_reading_context(context: dict | None) -> bool:
    if not context:
        return False
    return any(context.get(key) for key in ("fullArticleText", "readSoFarText", "upcomingText", "currentSentence"))


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


def post_current_text(text: str, active_mode: str | None = None, context: dict | None = None) -> tuple[bool, str]:
    endpoint = f"http://{TRIGGER_HOST}:{TRIGGER_PORT}{CURRENT_TEXT_PATH}"
    payload = {"text": text}
    if active_mode:
        payload["activeMode"] = active_mode
    if context:
        for key in ("fullArticleText", "readSoFarText", "upcomingText", "currentSentence"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
    body = json.dumps(payload).encode("utf-8")
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


async def forward_current_text(text: str, active_mode: str | None = None, context: dict | None = None) -> tuple[bool, str]:
    return await asyncio.to_thread(post_current_text, text, active_mode, context)


async def notify_baseline_event(event: str, reason: str | None = None) -> None:
    """Send disengagement event to the baseline observer client."""
    baseline_socket = role_clients.get("baseline")
    if baseline_socket is None:
        return
    msg = json.dumps({
        "type": "baseline_event",
        "event": event,
        "reason": reason,
        "ts": datetime.now().astimezone().isoformat(),
    })
    try:
        await baseline_socket.send(msg)
        _log("server", "baseline", f"baseline 事件通知: {event} (reason={reason})")
    except websockets.ConnectionClosed:
        _log("server", "console", "baseline WebSocket 已关闭，通知未送达")


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


async def notify_extension_highlight_current_sentence(duration_ms: int = 10000, source: str = "stage2_confused_sound") -> tuple[bool, str]:
    if CONTROL_MODE:
        _log("server", "console", "[对照组] 跳过 extension 当前句子高亮通知")
        return False, "control_mode"
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        _log("server", "console", "当前句子高亮未发送: 未找到已注册的 extension 客户端")
        return False, "extension_not_connected"

    payload = {
        **HIGHLIGHT_CURRENT_SENTENCE_MESSAGE,
        "durationMs": max(1, int(duration_ms)),
        "source": source,
        "ts": _local_iso(),
    }
    _log("server", "extension", f"WebSocket 定向发送 -> extension: {json.dumps(payload, ensure_ascii=False)}")
    try:
        await extension_socket.send(json.dumps(payload))
        return True, "ok"
    except websockets.ConnectionClosed:
        unregister_client(extension_socket)
        _log("server", "console", "extension WebSocket 在发送当前句子高亮前关闭，通知未送达")
        return False, "extension_connection_closed"


async def notify_extension_offer_reading_mode(
    current_mode: str = "full",
    recommended_mode: str = "para",
    source: str = "stage4_fatigue_support",
) -> tuple[bool, str]:
    if CONTROL_MODE:
        _log("server", "console", "[对照组] 跳过 extension 阅读模式切换邀请")
        return False, "control_mode"
    extension_socket = role_clients.get("extension")
    if extension_socket is None:
        _log("server", "console", "阅读模式切换邀请未发送: 未找到已注册的 extension 客户端")
        return False, "extension_not_connected"

    current_mode = current_mode if current_mode in {"full", "para", "sentence"} else "full"
    recommended_mode = recommended_mode if recommended_mode in {"para", "sentence"} else "para"
    payload = {
        **OFFER_READING_MODE_MESSAGE,
        "currentMode": current_mode,
        "recommendedMode": recommended_mode,
        "source": source,
        "ts": _local_iso(),
    }
    _log("server", "extension", f"WebSocket 定向发送 -> extension: {json.dumps(payload, ensure_ascii=False)}")
    try:
        await extension_socket.send(json.dumps(payload))
        return True, "ok"
    except websockets.ConnectionClosed:
        unregister_client(extension_socket)
        _log("server", "console", "extension WebSocket 在发送阅读模式切换邀请前关闭，通知未送达")
        return False, "extension_connection_closed"


def start_command_http_server(loop: asyncio.AbstractEventLoop) -> ThreadingHTTPServer:
    class CommandHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path not in (
                "/extension/highlight_current_sentence",
                "/highlight_current_sentence",
                "/extension/offer_reading_mode",
                "/offer_reading_mode",
            ):
                self.send_response(404)
                self.end_headers()
                return

            is_offer_path = self.path in ("/extension/offer_reading_mode", "/offer_reading_mode")
            duration_ms = HIGHLIGHT_CURRENT_SENTENCE_MESSAGE["durationMs"]
            source = OFFER_READING_MODE_MESSAGE["source"] if is_offer_path else HIGHLIGHT_CURRENT_SENTENCE_MESSAGE["source"]
            current_mode = "full"
            recommended_mode = OFFER_READING_MODE_MESSAGE["recommendedMode"]
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                raw_body = self.rfile.read(length)
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    requested_duration = payload.get("durationMs") or payload.get("duration_ms")
                    if requested_duration is not None:
                        duration_ms = int(requested_duration)
                    requested_source = payload.get("source")
                    if isinstance(requested_source, str) and requested_source.strip():
                        source = requested_source.strip()
                    requested_current_mode = payload.get("currentMode") or payload.get("current_mode")
                    if isinstance(requested_current_mode, str) and requested_current_mode.strip().lower() in {"full", "para", "sentence"}:
                        current_mode = requested_current_mode.strip().lower()
                    requested_recommended_mode = payload.get("recommendedMode") or payload.get("recommended_mode")
                    if isinstance(requested_recommended_mode, str) and requested_recommended_mode.strip().lower() in {"para", "sentence"}:
                        recommended_mode = requested_recommended_mode.strip().lower()
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                    pass

            if is_offer_path:
                future = asyncio.run_coroutine_threadsafe(
                    notify_extension_offer_reading_mode(
                        current_mode=current_mode,
                        recommended_mode=recommended_mode,
                        source=source,
                    ),
                    loop,
                )
                event_name = "offer_reading_mode"
            else:
                future = asyncio.run_coroutine_threadsafe(
                    notify_extension_highlight_current_sentence(duration_ms=duration_ms, source=source),
                    loop,
                )
                event_name = "highlight_current_sentence"
            try:
                ok, detail = future.result(timeout=5)
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            status = 200 if ok else 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "event": event_name, "detail": detail}).encode("utf-8"))

        def log_message(self, format, *args):
            return

    httpd = ThreadingHTTPServer((COMMAND_HTTP_HOST, COMMAND_HTTP_PORT), CommandHandler)
    thread = asyncio.to_thread(httpd.serve_forever)
    asyncio.create_task(thread)
    _log("server", "console", f"内部控制 HTTP 已启动: http://{COMMAND_HTTP_HOST}:{COMMAND_HTTP_PORT}")
    return httpd


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

            # ── Camera command relay ─────────────────────────────
            try:
                _payload = json.loads(message.strip())
                _msg_type = _payload.get("type")
                if isinstance(_msg_type, str) and _msg_type.startswith("control_session_"):
                    result = await handle_control_session_command(_payload)
                    _log("server", source_role, f"WebSocket 定向发送 -> {source_role}: {json.dumps(result, ensure_ascii=False)}")
                    await websocket.send(json.dumps(result))
                    continue
                if isinstance(_msg_type, str) and _msg_type in ("start_camera", "stop_camera", "recording_start", "recording_stop"):
                    webcam_ws = role_clients.get("webcam")
                    if webcam_ws is not None and webcam_ws is not websocket:
                        await webcam_ws.send(message)
                        _log("server", "webcam", f"已转发指令: {_msg_type}")
                        await websocket.send(json.dumps({"ok": True, "type": _msg_type, "relayed": True}))
                    else:
                        _log("server", "console", f"指令 {_msg_type} 无法转发: webcam 客户端未连接")
                        await websocket.send(json.dumps({"ok": False, "type": _msg_type, "reason": "webcam_not_connected"}))
                    continue
            except (json.JSONDecodeError, ValueError):
                pass

            receiver_role = client_roles.get(websocket, "unregistered")
            signal = parse_message_signal_for_role(message, receiver_role)
            _saved_active = tracker.distraction_active
            event = parse_message_event(message, receiver_role)
            if not event:
                reading_context = parse_reading_context_message(message)
                if reading_context:
                    if CONTROL_MODE:
                        response = {"ok": True, "type": "current_text", "mode": "control"}
                    else:
                        if has_extended_reading_context(reading_context):
                            ok, detail = await forward_current_text(
                                reading_context.get("text", ""),
                                reading_context.get("activeMode"),
                                reading_context,
                            )
                        else:
                            ok, detail = await forward_current_text(
                                reading_context.get("text", ""),
                                reading_context.get("activeMode"),
                            )
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

            reading_context = parse_reading_context_message(message)
            if reading_context and not CONTROL_MODE:
                if has_extended_reading_context(reading_context):
                    text_ok, text_detail = await forward_current_text(
                        reading_context.get("text", ""),
                        reading_context.get("activeMode"),
                        reading_context,
                    )
                else:
                    text_ok, text_detail = await forward_current_text(
                        reading_context.get("text", ""),
                        reading_context.get("activeMode"),
                    )

            disengage_reason = _extract_disengage_reason(message) if event == "start" else None

            if CONTROL_MODE:
                # 对照组: 只记录到当前 baseline session，不转发到 pipeline，不触发机器人
                recorded = control_session_manager.log_event(event, reason=disengage_reason)
                if recorded:
                    _log("server", "console", f"[对照组] 记录事件: {event} (reason={disengage_reason})")
                else:
                    _log("server", "console", f"[对照组] 忽略事件（当前无 active session）: {event}")
                response = {"ok": True, "event": event, "mode": "control", "session_active": recorded}
                _log("server", receiver_role, f"WebSocket 定向发送 -> {receiver_role}: {json.dumps(response, ensure_ascii=False)}")
                await websocket.send(json.dumps(response))
                if recorded:
                    await notify_baseline_event(event, disengage_reason)
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
    command_http_server = start_command_http_server(asyncio.get_running_loop())
    _log("server", "console", "WebSocket bridge 已启动")
    if CONTROL_MODE:
        _log("server", "console", "══════ 对照组模式 ══════")
        _log("server", "console", "  • 不转发事件到 pipeline（无机器人干预）")
        _log("server", "console", "  • 不发送 ROBOT_REDIRECT 到扩展（无页面高亮）")
        _log("server", "console", "  • 研究者通过 control_session_start/stop 管理 no_system session")
        _log("server", "console", "  • 会话保存为 sessions/baseline_*.json，并自动刷新 CSV")
    else:
        _log("server", "console", f"触发器目标: http://{TRIGGER_HOST}:{TRIGGER_PORT}")
    _log("server", "console", f"监听地址: ws://{WS_HOST}:{WS_PORT}")
    _log("server", "console", '客户端可先发送 JSON 注册身份: {"client": "webcam"} 或 {"client": "extension"}')
    _log("server", "console", "支持消息: start, stop, shutdown, posture_disengaged=true/false, re-engagement")
    _log("server", "console", '也支持 JSON: {"event": "start"} 或 {"posture_disengaged": true}')
    try:
        await server.wait_closed()
    finally:
        command_http_server.shutdown()
        command_http_server.server_close()



if __name__ == "__main__":
    asyncio.run(main())


    
