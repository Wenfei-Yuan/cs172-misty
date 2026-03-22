from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import BOOT_FACE, SPEAKING_FACE, arm_gesture, show_image
from utils.head_control import look_at_screen
from stages.screen_watch import find_screen_position_with_vlm


def run_bootup(misty, cfg, log):
    show_image(misty, BOOT_FACE)
    arm_gesture(misty, "wave")
    speak_text(misty, cfg, "Hello! I'm Misty. Let's focus today!", log=log, stage="bootup_intro")

    search = find_screen_position_with_vlm(misty, cfg, log=log)
    screen_pos = search.position
    if screen_pos is not None:
        look_at_screen(misty, screen_pos)
        log.record("screen_search_verified", yaw=screen_pos.yaw, pitch=screen_pos.pitch, reason=search.reason)
        log.record("screen_position_initialized", yaw=screen_pos.yaw, pitch=screen_pos.pitch)
        speak_text(misty, cfg, "I've found the screen!", log=log, stage="bootup_screen_found")
    else:
        log.record("screen_search_error", reason=search.reason or "screen_not_found")
        speak_text(misty, cfg, "I'm still looking for the screen.", log=log, stage="bootup_screen_missing")

    show_image(misty, SPEAKING_FACE)
    log.record("bootup_complete")
    return screen_pos
