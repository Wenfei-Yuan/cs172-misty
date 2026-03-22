from __future__ import annotations

from misty2py.basic_skills.speak import speak

from utils.expressions import BOOT_FACE, SPEAKING_FACE, arm_gesture, show_image
from utils.head_control import look_at_screen
from stages.screen_watch import find_screen_position_with_vlm


def run_bootup(misty, cfg, log):
    show_image(misty, BOOT_FACE)
    arm_gesture(misty, "wave")
    speak(misty, "Hello! I'm Misty. Let's focus today!")

    screen_pos = find_screen_position_with_vlm(misty, cfg)
    look_at_screen(misty, screen_pos)
    log.record("screen_position_initialized", yaw=screen_pos.yaw, pitch=screen_pos.pitch)

    show_image(misty, SPEAKING_FACE)
    log.record("bootup_complete")
    return screen_pos
