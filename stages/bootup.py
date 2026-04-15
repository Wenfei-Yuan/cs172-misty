from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import BOOT_FACE, SPEAKING_FACE, show_image
from utils.head_control import look_at_screen
from stages.screen_watch import default_screen_position, find_screen_position_with_vlm


def run_bootup(misty, cfg, log):
    show_image(misty, BOOT_FACE)
    speak_text(misty, cfg, "Hello! I'm Misty. Let's focus today!", log=log, stage="bootup_intro")
    speak_text(misty, cfg, "I'm looking for your screen now.", log=log, stage="bootup_screen_search_start")
    log.record("screen_search_started")

    try:
        search = find_screen_position_with_vlm(misty, cfg, log=log)
        screen_pos = search.position
    except Exception as exc:
        fallback = default_screen_position(cfg)
        log.record("bootup_screen_search_exception", reason=f"{type(exc).__name__}: {exc}")
        look_at_screen(misty, fallback)
        log.record("screen_position_initialized", yaw=fallback.yaw, pitch=fallback.pitch, source="bootup_fallback")
        speak_text(
            misty,
            cfg,
            "I had trouble checking the screen, so I'm using a default position for now.",
            log=log,
            stage="bootup_screen_fallback",
        )
        show_image(misty, SPEAKING_FACE)
        log.record("bootup_complete")
        return fallback

    if screen_pos is not None:
        look_at_screen(misty, screen_pos)
        log.record("screen_search_verified", yaw=screen_pos.yaw, pitch=screen_pos.pitch, reason=search.reason)
        log.record("screen_position_initialized", yaw=screen_pos.yaw, pitch=screen_pos.pitch)
        speak_text(misty, cfg, "I've found the screen!", log=log, stage="bootup_screen_found")
        speak_text(
            misty,
            cfg,
            "Let's start reading. You can open the browser extension and start reading now.",
            log=log,
            stage="bootup_start_reading",
        )
    else:
        log.record("screen_search_error", reason=search.reason or "screen_not_found")
        speak_text(misty, cfg, "I'm still looking for the screen.", log=log, stage="bootup_screen_missing")

    show_image(misty, SPEAKING_FACE)
    log.record("bootup_complete")
    return screen_pos
