from __future__ import annotations

import time


def look_at_screen(misty, screen_pos) -> None:
    misty.perform_action(
        "head_move",
        {
            "Yaw": screen_pos.yaw,
            "Pitch": screen_pos.pitch,
            "Velocity": 90,
        },
    )


def _wait_or_stop(stop_event, duration_s: float) -> bool:
    return stop_event.wait(timeout=max(0.0, duration_s))


def shake_head_only(misty, cfg, stop_event) -> None:
    pause_s = float(getattr(cfg, "shake_pause_s", 0.5))
    while not stop_event.is_set():
        misty.perform_action(
            "head_move",
            {
                "Yaw": cfg.shake_amplitude_deg,
                "Velocity": 80,
            },
        )
        if _wait_or_stop(stop_event, cfg.shake_period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break
        misty.perform_action(
            "head_move",
            {
                "Yaw": -cfg.shake_amplitude_deg,
                "Velocity": 80,
            },
        )
        if _wait_or_stop(stop_event, cfg.shake_period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break
