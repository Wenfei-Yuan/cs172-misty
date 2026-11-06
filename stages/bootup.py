from __future__ import annotations

import time

from utils.audio import speak_text
from utils.expressions import BOOT_FACE, SPEAKING_FACE, show_image
from utils.head_control import look_at_screen, reset_arms_down, reset_head
from stages.screen_watch import ScreenPos, default_screen_position


def run_bootup(misty, cfg, log) -> ScreenPos:
    show_image(misty, BOOT_FACE)
    reset_arms_down(misty, cfg)
    reset_head(misty, cfg)
    time.sleep(cfg.head_reset_settle_s)
    speak_text(misty, cfg, "Hello! I'm Misty. Let's focus today!", log=log, stage="bootup_intro")
    speak_text(misty, cfg, "I'm looking for your screen now.", log=log, stage="bootup_screen_search_start")

    # The robot is placed by hand before the session, so "finding" the screen is
    # a scripted turn to the configured position. No camera or VLM is involved.
    screen_pos = default_screen_position(cfg)
    time.sleep(cfg.bootup_intro_speech_wait_s)
    look_at_screen(misty, screen_pos)
    time.sleep(cfg.screen_settle_s)
    time.sleep(cfg.bootup_screen_hold_s)
    log.record("screen_position_initialized", yaw=screen_pos.yaw, pitch=screen_pos.pitch, source="config")

    speak_text(misty, cfg, "I've found the screen!", log=log, stage="bootup_screen_found")
    speak_text(
        misty,
        cfg,
        "Let's start reading. You can open the browser extension and start reading now.",
        log=log,
        stage="bootup_start_reading",
    )

    show_image(misty, SPEAKING_FACE)
    log.record("bootup_complete")
    return screen_pos
