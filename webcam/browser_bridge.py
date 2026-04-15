#!/usr/bin/env python3
"""
Browser Bridge: receives webcam frames from the Chrome extension via
a local WebSocket server, runs the IDENTICAL MediaPipe Face Mesh +
head pose + eye gaze pipeline from participant_client.py, and forwards
posture events to the main bridge server (server.py).

Usage:
    cd webcam
    python browser_bridge.py

The Chrome extension connects to ws://127.0.0.1:9876 and sends raw
JPEG frames as binary WebSocket messages.  This script decodes each
frame, feeds it through the same analyze_frame() / posture_logic used
by participant_client.py, and forwards state-change messages to the
bridge server on SERVER_IP:SERVER_PORT.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import cv2
import numpy as np
import websockets

# ---------------------------------------------------------------------------
# Import detection + posture logic from the existing webcam package
# ---------------------------------------------------------------------------
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from participant_client import (
    analyze_frame,
    smooth_angle,
    SERVER_IP,
    SERVER_PORT,
    DISENGAGE_THRESHOLD,
    REENGAGE_THRESHOLD,
    GAZE_REENGAGE_THRESHOLD,
    EVENT_CALIBRATION_COMPLETE,
    EVENT_POSTURE,
    CALIBRATION_DURATION,
    YAW_DEVIATION_THRESHOLD,
    PITCH_DEVIATION_THRESHOLD,
    REENGAGE_YAW_DEVIATION_THRESHOLD,
    REENGAGE_PITCH_DEVIATION_THRESHOLD,
    YAW_HYSTERESIS_MARGIN,
    PITCH_HYSTERESIS_MARGIN,
    POSE_SMOOTHING_ALPHA,
    REENGAGE_BREAK_TOLERANCE,
    AWAY_BREAK_TOLERANCE,
    MIN_VALID_FACE_FRAMES,
    FACE_MISSING_GRACE_PERIOD,
    POSE_INVALID_GRACE_PERIOD,
    ENABLE_EYE_GAZE,
    GAZE_SMOOTHING_ALPHA,
    GAZE_YAW_DEVIATION_THRESHOLD,
    GAZE_PITCH_DEVIATION_THRESHOLD,
    GAZE_YAW_HYSTERESIS,
    GAZE_PITCH_HYSTERESIS,
    GAZE_MIND_WANDERING_DURATION,
    GAZE_MW_BREAK_TOLERANCE,
    GAZE_HEAD_YAW_LIMIT,
)

from posture_logic import (
    compute_full_recovery,
    compute_reengage_threshold,
    compute_screen_facing,
)

# ---------------------------------------------------------------------------
# Local server config — the Chrome extension connects here
# ---------------------------------------------------------------------------
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = int(os.getenv("BRIDGE_LOCAL_PORT", "9876"))

# Bridge server — the main server.py
BRIDGE_WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}"


def decode_jpeg_frame(data: bytes) -> np.ndarray | None:
    buf = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return frame


# ═══════════════════════════════════════════════════════════════════════════
#  PostureEngine — identical state machine from participant_client.py
# ═══════════════════════════════════════════════════════════════════════════

class PostureEngine:
    """Encapsulates the full posture detection state machine.

    Every threshold, timer, grace-period, hysteresis branch and gaze
    mind-wandering path is a line-for-line port of run_client() in
    participant_client.py.  The only difference: frames arrive via
    argument instead of cv2.VideoCapture, and there is no cv2 display.
    """

    def __init__(self) -> None:
        self.reset()

    # ── lifecycle ──────────────────────────────────────────────────────

    def reset(self) -> None:
        # calibration
        self.calibration_start: float | None = None
        self.yaw_samples: list[float] = []
        self.pitch_samples: list[float] = []
        self.gaze_yaw_samples: list[float] = []
        self.gaze_pitch_samples: list[float] = []
        self.calibrated = False

        # reference values (set after calibration)
        self.reference_yaw: float | None = None
        self.reference_pitch: float | None = None
        self.reference_gaze_yaw: float = 0.5
        self.reference_gaze_pitch: float = 0.5

        # detection state
        self.disengaged = False
        self.away_start_time: float | None = None
        self.away_break_start_time: float | None = None
        self.reengage_start_time: float | None = None
        self.reengage_break_start_time: float | None = None
        self.detection_armed = False
        self.valid_face_streak = 0
        self.disengage_reason_latched: str | None = None

        # smoothing
        self.smoothed_yaw: float | None = None
        self.smoothed_pitch: float | None = None
        self.smoothed_gaze_yaw: float | None = None
        self.smoothed_gaze_pitch: float | None = None

        # grace periods
        self.face_missing_start_time: float | None = None
        self.invalid_pose_start_time: float | None = None
        self.last_stable_screen_facing: bool | None = None

        # gaze mind wandering
        self.gaze_mw_start_time: float | None = None
        self.gaze_mw_break_start_time: float | None = None
        self.gaze_mind_wandering = False

        # persisted between frames
        self.away_duration = 0.0
        self.reengage_duration = 0.0

        # stats
        self._frame_count = 0
        self._stats_time = time.time()

    # ── public API ─────────────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> tuple[list[dict], dict]:
        """Process one video frame.

        Returns
        -------
        (bridge_messages, status)
            bridge_messages — list of dicts to send to the bridge server
            status          — dict describing current phase for the extension UI
        """
        self._frame_count += 1
        now = time.time()
        result = analyze_frame(frame)

        if not self.calibrated:
            return self._calibrate(result, now)

        return self._detect(result, now)

    def get_stats_line(self) -> str | None:
        now = time.time()
        elapsed = now - self._stats_time
        if elapsed < 5.0:
            return None
        fps = self._frame_count / elapsed
        self._frame_count = 0
        self._stats_time = now
        return (
            f"fps={fps:.1f} | armed={self.detection_armed} "
            f"| disengaged={self.disengaged}"
        )

    # ── calibration ────────────────────────────────────────────────────

    def _calibrate(self, result: dict, now: float) -> tuple[list[dict], dict]:
        if self.calibration_start is None:
            self.calibration_start = now

        elapsed = now - self.calibration_start

        if result["face_present"] and result["yaw"] is not None and result["pitch"] is not None:
            self.yaw_samples.append(result["yaw"])
            self.pitch_samples.append(result["pitch"])

        if ENABLE_EYE_GAZE and result["gaze"] is not None:
            self.gaze_yaw_samples.append(result["gaze"]["gaze_yaw_ratio"])
            self.gaze_pitch_samples.append(result["gaze"]["gaze_pitch_ratio"])

        remaining = max(0.0, CALIBRATION_DURATION - elapsed)

        if elapsed < CALIBRATION_DURATION:
            return [], {
                "phase": "calibrating",
                "remaining": round(remaining, 1),
                "samples": len(self.yaw_samples),
            }

        # ── calibration period done ──
        if len(self.yaw_samples) < 10 or len(self.pitch_samples) < 10:
            print("[BrowserBridge] Calibration failed — not enough samples, retrying")
            self.calibration_start = None
            self.yaw_samples.clear()
            self.pitch_samples.clear()
            self.gaze_yaw_samples.clear()
            self.gaze_pitch_samples.clear()
            return [], {"phase": "calibration_retry"}

        self.reference_yaw = float(np.mean(self.yaw_samples))
        self.reference_pitch = float(np.mean(self.pitch_samples))

        if ENABLE_EYE_GAZE and len(self.gaze_yaw_samples) >= 10:
            self.reference_gaze_yaw = float(np.median(self.gaze_yaw_samples))
            self.reference_gaze_pitch = float(np.median(self.gaze_pitch_samples))
        else:
            self.reference_gaze_yaw = 0.5
            self.reference_gaze_pitch = 0.5

        self.calibrated = True
        self.detection_armed = False
        self.valid_face_streak = 0

        print(
            f"[BrowserBridge] Calibration complete: "
            f"ref_yaw={self.reference_yaw:.2f}, ref_pitch={self.reference_pitch:.2f}"
        )
        if ENABLE_EYE_GAZE:
            print(
                f"[BrowserBridge]   ref_gaze_yaw={self.reference_gaze_yaw:.4f}, "
                f"ref_gaze_pitch={self.reference_gaze_pitch:.4f}"
            )

        msg = {
            "source": "webcam",
            "type": EVENT_CALIBRATION_COMPLETE,
            "reference_yaw": round(self.reference_yaw, 2),
            "reference_pitch": round(self.reference_pitch, 2),
            "enable_eye_gaze": ENABLE_EYE_GAZE,
            "reference_gaze_yaw": round(self.reference_gaze_yaw, 4) if ENABLE_EYE_GAZE else None,
            "reference_gaze_pitch": round(self.reference_gaze_pitch, 4) if ENABLE_EYE_GAZE else None,
            "timestamp": now,
        }
        return [msg], {"phase": "calibrated"}

    # ── detection (main state machine) ─────────────────────────────────

    def _detect(self, result: dict, now: float) -> tuple[list[dict], dict]:  # noqa: C901 — mirrors original complexity
        raw_face_present = result["face_present"]
        yaw = result["yaw"]
        pitch = result["pitch"]
        gaze = result["gaze"]
        has_valid_pose = raw_face_present and yaw is not None and pitch is not None

        # ── face missing / invalid pose grace ──
        if raw_face_present:
            self.face_missing_start_time = None
        elif self.face_missing_start_time is None:
            self.face_missing_start_time = now

        if raw_face_present and yaw is None:
            if self.invalid_pose_start_time is None:
                self.invalid_pose_start_time = now
        else:
            self.invalid_pose_start_time = None

        face_missing_duration = 0.0 if self.face_missing_start_time is None else (now - self.face_missing_start_time)
        invalid_pose_duration = 0.0 if self.invalid_pose_start_time is None else (now - self.invalid_pose_start_time)

        # ── smoothing ──
        if has_valid_pose:
            self.smoothed_yaw = smooth_angle(self.smoothed_yaw, yaw, POSE_SMOOTHING_ALPHA)
            self.smoothed_pitch = smooth_angle(self.smoothed_pitch, pitch, POSE_SMOOTHING_ALPHA)
        else:
            self.smoothed_yaw = None
            self.smoothed_pitch = None

        effective_yaw = self.smoothed_yaw if self.smoothed_yaw is not None else yaw
        effective_pitch = self.smoothed_pitch if self.smoothed_pitch is not None else pitch

        # ── eye gaze smoothing ──
        if ENABLE_EYE_GAZE and gaze is not None:
            self.smoothed_gaze_yaw = smooth_angle(self.smoothed_gaze_yaw, gaze["gaze_yaw_ratio"], GAZE_SMOOTHING_ALPHA)
            self.smoothed_gaze_pitch = smooth_angle(self.smoothed_gaze_pitch, gaze["gaze_pitch_ratio"], GAZE_SMOOTHING_ALPHA)
        else:
            self.smoothed_gaze_yaw = None
            self.smoothed_gaze_pitch = None

        # ── screen facing determination ──
        if not raw_face_present:
            screen_facing = False
            yaw_deviation = None
            pitch_deviation = None
            yaw_threshold = YAW_DEVIATION_THRESHOLD
            pitch_threshold = PITCH_DEVIATION_THRESHOLD
            if self.detection_armed and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
                reason = "face_missing_pending"
            else:
                reason = "face_missing"
        elif yaw is None or pitch is None:
            screen_facing = False
            yaw_deviation = None
            pitch_deviation = None
            yaw_threshold = YAW_DEVIATION_THRESHOLD
            pitch_threshold = PITCH_DEVIATION_THRESHOLD
            if self.detection_armed and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
                reason = "pose_estimation_pending"
            else:
                reason = "pose_estimation_failed"
        else:
            yaw_deviation = abs(effective_yaw - self.reference_yaw)
            pitch_deviation = abs(effective_pitch - self.reference_pitch)

            screen_facing, yaw_threshold, pitch_threshold = compute_screen_facing(
                yaw_deviation=yaw_deviation,
                pitch_deviation=pitch_deviation,
                disengaged=self.disengaged,
                yaw_threshold=YAW_DEVIATION_THRESHOLD,
                pitch_threshold=PITCH_DEVIATION_THRESHOLD,
                reengage_yaw_threshold=REENGAGE_YAW_DEVIATION_THRESHOLD,
                reengage_pitch_threshold=REENGAGE_PITCH_DEVIATION_THRESHOLD,
                yaw_hysteresis_margin=YAW_HYSTERESIS_MARGIN,
                pitch_hysteresis_margin=PITCH_HYSTERESIS_MARGIN,
            )

            reason = "screen_facing" if screen_facing else "looking_away"
            self.last_stable_screen_facing = screen_facing

        # ── grace period overrides ──
        face_present = raw_face_present
        reported_screen_facing = screen_facing

        if self.detection_armed and (not raw_face_present) and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
            face_present = True
            if self.last_stable_screen_facing is not None:
                reported_screen_facing = self.last_stable_screen_facing
        elif self.detection_armed and raw_face_present and (yaw is None or pitch is None) and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
            if self.last_stable_screen_facing is not None:
                reported_screen_facing = self.last_stable_screen_facing

        face_missing_confirmed = (
            self.detection_armed
            and (not raw_face_present)
            and face_missing_duration >= FACE_MISSING_GRACE_PERIOD
        )
        invalid_pose_confirmed = (
            self.detection_armed
            and raw_face_present
            and (yaw is None or pitch is None)
            and invalid_pose_duration >= POSE_INVALID_GRACE_PERIOD
        )
        looking_away_confirmed = raw_face_present and yaw is not None and pitch is not None and (not screen_facing)
        state_changed = False
        active_error_reason = None
        reengage_threshold_s = REENGAGE_THRESHOLD

        # ── gaze deviation (computed before recovery logic) ──
        gaze_yaw_dev = None
        gaze_pitch_dev = None
        gaze_looking_away = False
        gaze_error_confirmed = False

        if ENABLE_EYE_GAZE and self.smoothed_gaze_yaw is not None:
            gaze_yaw_dev = abs(self.smoothed_gaze_yaw - self.reference_gaze_yaw)
            gaze_pitch_dev = abs(self.smoothed_gaze_pitch - self.reference_gaze_pitch)

            if yaw_deviation is not None and yaw_deviation < GAZE_HEAD_YAW_LIMIT:
                if self.gaze_mind_wandering:
                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD - GAZE_YAW_HYSTERESIS
                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD - GAZE_PITCH_HYSTERESIS
                else:
                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD + GAZE_YAW_HYSTERESIS
                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD + GAZE_PITCH_HYSTERESIS
                gaze_looking_away = gaze_yaw_dev > gy_thresh or gaze_pitch_dev > gp_thresh

        # ── arming ──
        if not self.detection_armed:
            if has_valid_pose:
                self.valid_face_streak += 1
            else:
                self.valid_face_streak = 0

            self.away_start_time = None
            self.away_break_start_time = None
            self.reengage_start_time = None
            self.reengage_break_start_time = None
            self.away_duration = 0.0
            self.reengage_duration = 0.0

            if self.valid_face_streak >= MIN_VALID_FACE_FRAMES:
                self.detection_armed = True
                print("[BrowserBridge] Detection armed")

            status = {
                "phase": "arming",
                "streak": self.valid_face_streak,
                "needed": MIN_VALID_FACE_FRAMES,
            }
            return [], status

        # ── gaze mind-wandering timer ──
        if ENABLE_EYE_GAZE:
            if gaze_looking_away:
                if self.gaze_mw_start_time is None:
                    self.gaze_mw_start_time = now
                self.gaze_mw_break_start_time = None
            elif self.gaze_mw_start_time is not None:
                if self.gaze_mw_break_start_time is None:
                    self.gaze_mw_break_start_time = now
                elif now - self.gaze_mw_break_start_time >= GAZE_MW_BREAK_TOLERANCE:
                    self.gaze_mw_start_time = None
                    self.gaze_mw_break_start_time = None

            gaze_error_confirmed = (
                self.gaze_mw_start_time is not None
                and (now - self.gaze_mw_start_time) >= GAZE_MIND_WANDERING_DURATION
            )
        else:
            self.gaze_mw_start_time = None
            self.gaze_mw_break_start_time = None

        self.gaze_mind_wandering = gaze_error_confirmed

        # ── active error reason ──
        active_error_reason = None
        if face_missing_confirmed:
            active_error_reason = "face_missing"
        elif invalid_pose_confirmed:
            active_error_reason = "pose_estimation_failed"
        elif looking_away_confirmed:
            active_error_reason = "looking_away"
        elif gaze_error_confirmed:
            active_error_reason = "gaze_mind_wandering"

        # ── full recovery check ──
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=ENABLE_EYE_GAZE,
            disengage_reason=self.disengage_reason_latched,
            raw_face_present=raw_face_present,
            yaw=yaw,
            pitch=pitch,
            screen_facing=screen_facing,
            smoothed_gaze_yaw=self.smoothed_gaze_yaw,
            smoothed_gaze_pitch=self.smoothed_gaze_pitch,
            gaze_looking_away=gaze_looking_away,
            gaze_error_confirmed=gaze_error_confirmed,
        )

        reengage_threshold_s = compute_reengage_threshold(
            disengage_reason=self.disengage_reason_latched,
            default_threshold_s=REENGAGE_THRESHOLD,
            gaze_threshold_s=GAZE_REENGAGE_THRESHOLD,
        )

        # ── state transitions ──
        if active_error_reason is not None:
            reason = self.disengage_reason_latched or active_error_reason

            if self.away_start_time is None:
                self.away_start_time = now
            self.away_break_start_time = None
            self.away_duration = now - self.away_start_time

            if self.reengage_start_time is not None:
                if self.reengage_break_start_time is None:
                    self.reengage_break_start_time = now
                elif now - self.reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                    self.reengage_start_time = None
                    self.reengage_duration = 0.0
            else:
                self.reengage_duration = 0.0

            if not self.disengaged:
                if active_error_reason == "gaze_mind_wandering" or self.away_duration >= DISENGAGE_THRESHOLD:
                    self.disengaged = True
                    self.disengage_reason_latched = active_error_reason
                    reason = self.disengage_reason_latched
                    state_changed = True

        elif fully_recovered:
            reason = "gaze_recovered" if ENABLE_EYE_GAZE else "screen_facing"
            self.reengage_break_start_time = None

            if self.away_start_time is not None:
                if self.away_break_start_time is None:
                    self.away_break_start_time = now
                elif now - self.away_break_start_time >= AWAY_BREAK_TOLERANCE:
                    self.away_start_time = None
                    self.away_duration = 0.0
            else:
                self.away_duration = 0.0

            if self.reengage_start_time is None:
                self.reengage_start_time = now
            self.reengage_duration = now - self.reengage_start_time

            if self.disengaged and self.reengage_duration >= reengage_threshold_s:
                self.disengaged = False
                self.disengage_reason_latched = None
                state_changed = True

        else:
            reason = self.disengage_reason_latched or "waiting_full_recovery"
            self.away_break_start_time = None
            self.away_duration = 0.0 if self.away_start_time is None else (now - self.away_start_time)

            if self.disengaged and self.reengage_start_time is not None:
                if self.reengage_break_start_time is None:
                    self.reengage_break_start_time = now
                elif now - self.reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                    self.reengage_start_time = None
                    self.reengage_duration = 0.0
            else:
                self.reengage_duration = 0.0

        # ── build message if state changed ──
        messages: list[dict] = []
        if state_changed:
            msg = {
                "client": "webcam",
                "source": "webcam",
                "type": EVENT_POSTURE,
                "disengage": self.disengaged,
                "face_present": face_present,
                "screen_facing": reported_screen_facing,
                "reason": reason,
                "yaw": None if yaw is None else round(yaw, 2),
                "pitch": None if pitch is None else round(pitch, 2),
                "reference_yaw": round(self.reference_yaw, 2),
                "reference_pitch": round(self.reference_pitch, 2),
                "yaw_deviation": None if yaw_deviation is None else round(yaw_deviation, 2),
                "pitch_deviation": None if pitch_deviation is None else round(pitch_deviation, 2),
                "away_duration": round(self.away_duration, 2),
                "reengage_duration": round(self.reengage_duration, 2),
                "gaze_yaw_ratio": round(self.smoothed_gaze_yaw, 4) if self.smoothed_gaze_yaw is not None else None,
                "gaze_pitch_ratio": round(self.smoothed_gaze_pitch, 4) if self.smoothed_gaze_pitch is not None else None,
                "gaze_yaw_deviation": round(gaze_yaw_dev, 4) if gaze_yaw_dev is not None else None,
                "gaze_pitch_deviation": round(gaze_pitch_dev, 4) if gaze_pitch_dev is not None else None,
                "gaze_mind_wandering": self.gaze_mind_wandering if ENABLE_EYE_GAZE else None,
                "timestamp": now,
            }
            messages.append(msg)
            label = "DISENGAGED" if self.disengaged else "RE-ENGAGED"
            print(f"[BrowserBridge] *** {label} *** reason={reason}")

        status = {
            "phase": "active",
            "disengaged": self.disengaged,
            "armed": self.detection_armed,
            "reason": reason,
        }
        return messages, status


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket connection management
# ═══════════════════════════════════════════════════════════════════════════

async def handle_extension(ext_ws: websockets.WebSocketServerProtocol) -> None:
    remote = ext_ws.remote_address or ("unknown", 0)
    print(f"[BrowserBridge] Extension connected from {remote[0]}:{remote[1]}")

    engine = PostureEngine()
    bridge_ws: websockets.WebSocketClientProtocol | None = None

    async def ensure_bridge() -> websockets.WebSocketClientProtocol | None:
        nonlocal bridge_ws
        if bridge_ws is not None:
            try:
                if bridge_ws.open:
                    return bridge_ws
            except Exception:
                pass
            bridge_ws = None

        try:
            bridge_ws = await websockets.connect(
                BRIDGE_WS_URL,
                ping_interval=20,
                ping_timeout=60,
            )
            print(f"[BrowserBridge] Connected to bridge server: {BRIDGE_WS_URL}")
            return bridge_ws
        except Exception as exc:
            print(f"[BrowserBridge] Bridge connection failed: {exc}")
            bridge_ws = None
            return None

    try:
        async for raw_message in ext_ws:
            # ── binary frame = JPEG data ──
            if isinstance(raw_message, bytes):
                frame = decode_jpeg_frame(raw_message)
                if frame is None:
                    continue

                messages, status = engine.process_frame(frame)

                # Forward posture events to bridge server
                for msg in messages:
                    ws = await ensure_bridge()
                    if ws is not None:
                        try:
                            await ws.send(json.dumps(msg))
                            print(f"[BrowserBridge] Sent to bridge: {json.dumps(msg, ensure_ascii=False)}")
                        except websockets.exceptions.ConnectionClosed:
                            bridge_ws = None
                            print("[BrowserBridge] Bridge disconnected during send")

                # Send status to extension
                await ext_ws.send(json.dumps({"type": "status", **status}))

                # Periodic stats
                stats = engine.get_stats_line()
                if stats:
                    print(f"[BrowserBridge] {stats}")

            # ── text message = control command ──
            elif isinstance(raw_message, str):
                try:
                    cmd = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue

                if cmd.get("type") == "stop":
                    print("[BrowserBridge] Extension requested stop")
                    break
                elif cmd.get("type") == "reset":
                    engine.reset()
                    print("[BrowserBridge] Engine reset — will re-calibrate")

    except websockets.exceptions.ConnectionClosed:
        print("[BrowserBridge] Extension disconnected")
    finally:
        if bridge_ws is not None:
            try:
                await bridge_ws.close()
            except Exception:
                pass
        print("[BrowserBridge] Session ended\n")


async def main() -> None:
    print("=" * 60)
    print("  Webcam Monitor — Browser Bridge Server")
    print("=" * 60)
    print(f"  Local WebSocket : ws://{LOCAL_HOST}:{LOCAL_PORT}")
    print(f"  Bridge target   : {BRIDGE_WS_URL}")
    print(f"  Eye gaze        : {'ENABLED' if ENABLE_EYE_GAZE else 'DISABLED'}")
    print("=" * 60)
    print("Waiting for Chrome extension connection...\n")

    async with websockets.serve(handle_extension, LOCAL_HOST, LOCAL_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BrowserBridge] Shutting down")
