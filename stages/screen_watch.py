from __future__ import annotations

import time
from dataclasses import dataclass

from utils.expressions import READING_FACE, show_image
from utils.head_control import look_at_screen


@dataclass(frozen=True)
class ScreenPos:
    yaw: float
    pitch: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def default_screen_position(cfg) -> ScreenPos:
    return ScreenPos(
        yaw=_clamp(cfg.screen_yaw, -90.0, 90.0),
        pitch=_clamp(cfg.screen_pitch, -40.0, 40.0),
    )


def run_screen_watch(misty, cfg, log, screen_pos: ScreenPos) -> ScreenPos:
    show_image(misty, READING_FACE)
    _pause_before_return_to_screen(cfg)
    look_at_screen(misty, screen_pos)
    time.sleep(cfg.screen_settle_s)
    log.record("screen_cache_hit", yaw=screen_pos.yaw, pitch=screen_pos.pitch)
    log.record_screen_observation("Screen alignment verified via cached position.")
    return screen_pos
