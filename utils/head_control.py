from __future__ import annotations

import time


def look_at_screen(misty, screen_pos) -> None:
    misty.perform_action(
        "head_move",
        {
            "Yaw": screen_pos.yaw,
            "Pitch": screen_pos.pitch,
            "Velocity": 50,
        },
    )


def shake_head_only(misty, cfg, stop_event) -> None:
    while not stop_event.is_set():
        misty.perform_action(
            "head_move",
            {
                "Yaw": cfg.shake_amplitude_deg,
                "Velocity": 80,
            },
        )
        time.sleep(cfg.shake_period_s)
        if stop_event.is_set():
            break
        misty.perform_action(
            "head_move",
            {
                "Yaw": -cfg.shake_amplitude_deg,
                "Velocity": 80,
            },
        )
        time.sleep(cfg.shake_period_s)
