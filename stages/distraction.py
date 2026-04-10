from __future__ import annotations

import threading
import time
from queue import Empty, Full, Queue
from dataclasses import dataclass

from utils.audio import speak_text
from utils.expressions import DISTRACTION_FACE, SPEAKING_FACE, show_image
from utils.head_control import acknowledge_gaze_recovery, look_at_screen, shake_head_only
from utils.vision import VisionCheckResult, analyze_gaze_capture, capture_frame_result


@dataclass(frozen=True)
class DistractionResult:
    outcome: str
    gaze_seen: bool = False
    gaze_latency_s: float | None = None


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def _stop_thread(stop_event, thread, timeout: float = 2.0) -> None:
    stop_event.set()
    thread.join(timeout=timeout)


def _vision_worker(
    misty,
    cfg,
    stop_event: threading.Event,
    result_queue: "Queue[VisionCheckResult]",
) -> None:
    """Continuously capture frames and run gaze analysis, independent of the shake thread."""
    min_interval_s = max(0.1, float(getattr(cfg, "vision_worker_min_interval_s", 0.1)))
    while not stop_event.is_set():
        t0 = time.monotonic()
        frame = capture_frame_result(misty)
        if stop_event.is_set():
            break
        if frame.reason == "camera_busy":
            stop_event.wait(timeout=0.3)
            continue
        result = analyze_gaze_capture(frame, cfg)
        if stop_event.is_set():
            break
        # Keep only the freshest result; drop stale ones
        try:
            result_queue.get_nowait()
        except Empty:
            pass
        try:
            result_queue.put_nowait(result)
        except Full:
            pass
        elapsed = time.monotonic() - t0
        remaining = min_interval_s - elapsed
        if remaining > 0:
            stop_event.wait(timeout=remaining)


def run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None) -> DistractionResult:
    log.record_distraction_start()
    show_image(misty, DISTRACTION_FACE)

    # --- shake thread (head movement only, no camera I/O) ---
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

    # --- vision thread (camera + OpenAI, never blocks shake) ---
    stop_vision = threading.Event()
    vision_results: Queue[VisionCheckResult] = Queue(maxsize=1)
    vision_thread = threading.Thread(
        target=_vision_worker,
        args=(misty, cfg, stop_vision, vision_results),
        daemon=True,
    )
    vision_thread.start()

    poll_start = time.monotonic()
    gaze_seen = False
    gaze_latency = None
    recovered_yaw = None
    outcome = "timeout"
    # How long the main loop waits for a pose update each iteration.
    # Short enough to stay responsive to interrupts and fresh vision results.
    _POSE_WAIT_S = 0.1

    try:
        while not gaze_seen:
            interrupt_event = consume_interrupt() if consume_interrupt else None
            if interrupt_event == "shutdown":
                outcome = "shutdown"
                break
            if interrupt_event == "stop":
                outcome = "stop"
                break
            elapsed = time.monotonic() - poll_start
            if elapsed > cfg.gaze_timeout_s:
                outcome = "timeout"
                break

            # Wait briefly for a pose sample (non-blocking on vision I/O)
            sampled_yaw = None
            try:
                sampled_yaw = pose_updates.get(timeout=_POSE_WAIT_S)
            except Empty:
                sampled_yaw = None
                if consume_interrupt:
                    _mid_event = consume_interrupt()
                    if _mid_event == "stop":
                        outcome = "stop"
                        break
                    if _mid_event == "shutdown":
                        outcome = "shutdown"
                        break

            # Check for a fresh vision result (non-blocking)
            try:
                result = vision_results.get_nowait()
            except Empty:
                continue

            if result.status in {"capture_error", "vlm_error"}:
                log.record("gaze_check_error", reason=result.reason, status=result.status)
                continue

            gaze_seen = result.status == "gazing"
            if sampled_yaw is not None:
                log.record("gaze_sampled_pose", yaw_deg=round(sampled_yaw, 2), gaze_status=result.status)
            if gaze_seen:
                recovered_yaw = sampled_yaw if sampled_yaw is not None else latest_pose["yaw"]
                gaze_latency = time.monotonic() - poll_start
                outcome = "gaze"
                _stop_thread(stop_vision, vision_thread)
                _stop_thread(stop_shake, shake_thread)
                #show_image(misty, SPEAKING_FACE)
                #speak_text(
                   # misty,
                   # cfg,
                  #  "I see you! Let me check what you were working on.",
                   # log=log,
                   # stage="gaze_recovered",
               # )
                break
    finally:
        _stop_thread(stop_vision, vision_thread)
        _stop_thread(stop_shake, shake_thread)

    if outcome == "gaze":
        acknowledge_gaze_recovery(misty, cfg, current_yaw=recovered_yaw)
    elif screen_pos is not None:
        if outcome != "stop":
            _pause_before_return_to_screen(cfg)
        look_at_screen(misty, screen_pos)

    log.record_distraction_result(outcome=outcome, gaze_seen=gaze_seen, latency_s=gaze_latency)
    return DistractionResult(outcome=outcome, gaze_seen=gaze_seen, gaze_latency_s=gaze_latency)
