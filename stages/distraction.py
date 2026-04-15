from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from utils.expressions import DISTRACTION_FACE, show_image
from utils.head_control import look_at_screen, perform_distraction_start_sequence


@dataclass(frozen=True)
class DistractionResult:
    outcome: str
    sequence_completed: bool = False
    completion_latency_s: float | None = None


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def _stop_thread(stop_event, thread, timeout: float = 2.0) -> None:
    stop_event.set()
    thread.join(timeout=timeout)


def run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None) -> DistractionResult:
    log.record_distraction_start()
    show_image(misty, DISTRACTION_FACE)

    motion_done = threading.Event()
    stop_motion = threading.Event()

    def _run_motion() -> None:
        try:
            perform_distraction_start_sequence(misty, cfg, screen_pos, stop_event=stop_motion)
        finally:
            motion_done.set()

    motion_thread = threading.Thread(target=_run_motion, daemon=True)
    motion_thread.start()

    poll_start = time.monotonic()
    outcome = "timeout"
    _POLL_S = 0.1

    try:
        while not motion_done.is_set():
            interrupt = consume_interrupt() if consume_interrupt else None
            if interrupt == "shutdown":
                outcome = "shutdown"
                break
            if interrupt == "stop":
                outcome = "stop"
                break
            elapsed = time.monotonic() - poll_start
            if elapsed >= cfg.gaze_timeout_s:
                outcome = "timeout"
                break
            motion_done.wait(timeout=_POLL_S)
        else:
            outcome = "sequence_complete"
    finally:
        _stop_thread(stop_motion, motion_thread)

    if outcome == "timeout" and screen_pos is not None:
        _pause_before_return_to_screen(cfg)
        look_at_screen(misty, screen_pos)

    log.record_distraction_result(outcome=outcome, sequence_completed=(outcome == "sequence_complete"), latency_s=None)
    return DistractionResult(
        outcome=outcome,
        sequence_completed=(outcome == "sequence_complete"),
        completion_latency_s=None,
    )
