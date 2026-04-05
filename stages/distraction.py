from __future__ import annotations

import threading
import time
from queue import Empty, Full, Queue
from dataclasses import dataclass

from utils.audio import speak_text
from utils.expressions import DISTRACTION_FACE, SPEAKING_FACE, show_image
from utils.head_control import acknowledge_gaze_recovery, look_at_screen, shake_head_only
from utils.vision import analyze_gaze_capture, capture_frame_result


@dataclass(frozen=True)
class DistractionResult:
    outcome: str
    gaze_seen: bool = False
    gaze_latency_s: float | None = None


def _gaze_poll_interval(cfg, elapsed_s: float) -> float:
    default_interval = max(0.0, float(getattr(cfg, "gaze_poll_interval_s", 2.0)))
    fast_interval = max(0.0, float(getattr(cfg, "fast_gaze_poll_interval_s", default_interval)))
    fast_window = max(0.0, float(getattr(cfg, "fast_gaze_poll_window_s", 0.0)))
    if elapsed_s < fast_window:
        return min(default_interval, fast_interval)
    return default_interval


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def _stop_shake_thread(stop_shake, shake_thread) -> None:
    stop_shake.set()
    shake_thread.join(timeout=2)


def run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None) -> DistractionResult:
    log.record_distraction_start()
    show_image(misty, DISTRACTION_FACE)

    stop_shake = threading.Event()
    pose_updates: Queue[float] = Queue(maxsize=8)
    latest_pose = {"yaw": None}

    def _on_stable_pose(yaw: float) -> None:
        latest_pose["yaw"] = yaw
        try:
            pose_updates.put_nowait(yaw)
        except Full:
            try:
                pose_updates.get_nowait()
            except Empty:
                pass
            try:
                pose_updates.put_nowait(yaw)
            except Full:
                pass

    shake_thread = threading.Thread(
        target=shake_head_only,
        args=(misty, cfg, stop_shake),
        kwargs={"position_callback": _on_stable_pose},
        daemon=True,
    )
    shake_thread.start()

    poll_start = time.time()
    gaze_seen = False
    gaze_latency = None
    recovered_yaw = None
    outcome = "timeout"

    try:
        while not gaze_seen:
            loop_started = time.monotonic()
            interrupt_event = consume_interrupt() if consume_interrupt else None
            if interrupt_event == "shutdown":
                outcome = "shutdown"
                break
            if interrupt_event == "stop":
                outcome = "stop"
                break
            elapsed = time.time() - poll_start
            if elapsed > cfg.gaze_timeout_s:
                outcome = "timeout"
                break
            poll_interval_s = _gaze_poll_interval(cfg, elapsed)
            sampled_yaw = None
            if poll_interval_s > 0:
                try:
                    sampled_yaw = pose_updates.get(timeout=poll_interval_s)
                except Empty:
                    sampled_yaw = None
            else:
                try:
                    sampled_yaw = pose_updates.get_nowait()
                except Empty:
                    sampled_yaw = None
            frame = capture_frame_result(misty)
            if frame.reason == "camera_busy":
                time.sleep(0.3)
                continue
            result = analyze_gaze_capture(frame, cfg)
            if result.status in {"capture_error", "vlm_error"}:
                log.record("gaze_check_error", reason=result.reason, status=result.status)
                remaining_sleep = max(0.0, poll_interval_s - (time.monotonic() - loop_started))
                time.sleep(remaining_sleep)
                continue
            gaze_seen = result.status == "gazing"
            if sampled_yaw is not None:
                log.record("gaze_sampled_pose", yaw_deg=round(sampled_yaw, 2), gaze_status=result.status)
            if gaze_seen:
                recovered_yaw = sampled_yaw if sampled_yaw is not None else latest_pose["yaw"]
                gaze_latency = time.time() - poll_start
                outcome = "gaze"
                _stop_shake_thread(stop_shake, shake_thread)
                show_image(misty, SPEAKING_FACE)
                speak_text(
                    misty,
                    cfg,
                    "I see you! Let me check what you were working on.",
                    log=log,
                    stage="gaze_recovered",
                )
                break
            remaining_sleep = max(0.0, poll_interval_s - (time.monotonic() - loop_started))
            time.sleep(remaining_sleep)
    finally:
        _stop_shake_thread(stop_shake, shake_thread)

    if outcome == "gaze":
        acknowledge_gaze_recovery(misty, cfg, current_yaw=recovered_yaw)
        if screen_pos is not None:
            look_at_screen(misty, screen_pos)
    elif screen_pos is not None:
        _pause_before_return_to_screen(cfg)
        look_at_screen(misty, screen_pos)

    log.record_distraction_result(outcome=outcome, gaze_seen=gaze_seen, latency_s=gaze_latency)
    return DistractionResult(outcome=outcome, gaze_seen=gaze_seen, gaze_latency_s=gaze_latency)
