from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from utils.expressions import DISTRACTION_FACE, show_image
from utils.head_control import look_at_screen, shake_head_only
from utils.vision import analyze_gaze_capture, capture_frame_result


@dataclass(frozen=True)
class DistractionResult:
    outcome: str
    gaze_seen: bool = False
    gaze_latency_s: float | None = None


def run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None) -> DistractionResult:
    log.record_distraction_start()
    show_image(misty, DISTRACTION_FACE)

    stop_shake = threading.Event()
    shake_thread = threading.Thread(
        target=shake_head_only,
        args=(misty, cfg, stop_shake),
        daemon=True,
    )
    shake_thread.start()

    poll_start = time.time()
    gaze_seen = False
    gaze_latency = None
    outcome = "timeout"

    try:
        while not gaze_seen:
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
            frame = capture_frame_result(misty)
            result = analyze_gaze_capture(frame, cfg)
            if result.status in {"capture_error", "vlm_error"}:
                log.record("gaze_check_error", reason=result.reason, status=result.status)
                time.sleep(cfg.gaze_poll_interval_s)
                continue
            gaze_seen = result.status == "gazing"
            if gaze_seen:
                gaze_latency = time.time() - poll_start
                outcome = "gaze"
                break
            time.sleep(cfg.gaze_poll_interval_s)
    finally:
        stop_shake.set()
        shake_thread.join(timeout=2)
        if screen_pos is not None:
            look_at_screen(misty, screen_pos)

    log.record_distraction_result(outcome=outcome, gaze_seen=gaze_seen, latency_s=gaze_latency)
    return DistractionResult(outcome=outcome, gaze_seen=gaze_seen, gaze_latency_s=gaze_latency)
