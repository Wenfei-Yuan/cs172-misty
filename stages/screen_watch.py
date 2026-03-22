from __future__ import annotations

import time
from dataclasses import dataclass

from utils.expressions import READING_FACE, SPEAKING_FACE, show_image
from utils.head_control import look_at_screen
from utils.vision import capture_frame, vlm_is_facing_screen


@dataclass(frozen=True)
class ScreenPos:
    yaw: float
    pitch: float


def _calibrate_screen(cfg) -> ScreenPos:
    return ScreenPos(yaw=cfg.default_screen_yaw, pitch=cfg.default_screen_pitch)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _search_screen_position_with_vlm(misty, cfg, seed: ScreenPos) -> ScreenPos:
    for pitch_offset in cfg.screen_search_pitch_offsets:
        for yaw_offset in cfg.screen_search_yaw_offsets:
            candidate = ScreenPos(
                yaw=_clamp(seed.yaw + yaw_offset, -90.0, 90.0),
                pitch=_clamp(seed.pitch + pitch_offset, -40.0, 40.0),
            )
            look_at_screen(misty, candidate)
            time.sleep(cfg.screen_settle_s)
            frame_b64 = capture_frame(misty)
            if vlm_is_facing_screen(frame_b64, cfg):
                return candidate
    return seed


def find_screen_position_with_vlm(misty, cfg) -> ScreenPos:
    seed = ScreenPos(
        yaw=_clamp(cfg.screen_init_left_front_yaw, -90.0, 90.0),
        pitch=_clamp(cfg.screen_init_left_front_pitch, -40.0, 40.0),
    )
    look_at_screen(misty, seed)
    time.sleep(cfg.screen_settle_s)
    return _search_screen_position_with_vlm(misty, cfg, seed)


def run_screen_watch(misty, cfg, log, screen_pos: ScreenPos | None) -> ScreenPos:
    if screen_pos is None:
        position = find_screen_position_with_vlm(misty, cfg)
    else:
        show_image(misty, READING_FACE)
        position = screen_pos

    aligned = vlm_is_facing_screen(capture_frame(misty), cfg)

    show_image(misty, SPEAKING_FACE)
    from misty2py.basic_skills.speak import speak

    speak(misty, "I found the screen and aligned my head to face it." if aligned else "I'm still adjusting to face your screen.")
    show_image(misty, READING_FACE)

    log.record_screen_observation(
        "Screen alignment verified." if aligned else "Screen alignment not yet verified."
    )
    return position
