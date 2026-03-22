from __future__ import annotations

import threading
import time

from utils.expressions import DISTRACTION_FACE, show_image
from utils.head_control import look_at_screen, shake_head_only
from utils.vision import capture_frame, vlm_is_gazing


def run_distraction(misty, cfg, log, screen_pos, should_interrupt=None) -> bool:
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

    try:
        while not gaze_seen:
            if should_interrupt and should_interrupt():
                break
            elapsed = time.time() - poll_start
            if elapsed > cfg.gaze_timeout_s:
                break
            frame_b64 = capture_frame(misty)
            gaze_seen = vlm_is_gazing(frame_b64, cfg)
            if gaze_seen:
                gaze_latency = time.time() - poll_start
                break
            time.sleep(cfg.gaze_poll_interval_s)
    finally:
        stop_shake.set()
        shake_thread.join(timeout=2)
        if screen_pos is not None:
            look_at_screen(misty, screen_pos)

    log.record_distraction_gaze(gaze_seen, gaze_latency)
    return gaze_seen
