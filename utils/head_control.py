from __future__ import annotations

import time


def look_at_screen(misty, screen_pos) -> None:
    try:
        misty.perform_action(
            "head_move",
            {
                "Yaw": screen_pos.yaw,
                "Pitch": screen_pos.pitch,
                "Velocity": 90,
            },
        )
    except Exception as exc:
        print(f"Error moving head to screen position: {exc}")


def _wait_or_stop(stop_event, duration_s: float) -> bool:
    return stop_event.wait(timeout=max(0.0, duration_s))


def shake_head_only(misty, cfg, stop_event) -> None:
    pause_s = float(getattr(cfg, "shake_pause_s", 0.5))
    while not stop_event.is_set():
        try:
            misty.perform_action(
                "head_move",
                {
                    "Yaw": cfg.shake_amplitude_deg,
                    "Velocity": 80,
                },
            )
        except Exception as exc:
            print(f"Error in head shake: {exc}")
            break
        if _wait_or_stop(stop_event, cfg.shake_period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break
        try:
            misty.perform_action(
                "head_move",
                {
                    "Yaw": -cfg.shake_amplitude_deg,
                    "Velocity": 80,
                },
            )
        except Exception as exc:
            print(f"Error in head shake: {exc}")
            break
        if _wait_or_stop(stop_event, cfg.shake_period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break


def swing_arms_only(misty, cfg, stop_event) -> None:
    up_deg = int(getattr(cfg, "arm_swing_up_deg", -80))
    down_deg = int(getattr(cfg, "arm_swing_down_deg", 80))
    velocity = int(getattr(cfg, "arm_swing_velocity", 55))
    period_s = float(getattr(cfg, "arm_swing_period_s", 0.5))
    pause_s = float(getattr(cfg, "arm_swing_pause_s", 0.2))

    while not stop_event.is_set():
        try:
            misty.perform_action(
                "arms_move",
                {
                    "LeftArmPosition": up_deg,
                    "RightArmPosition": up_deg,
                    "LeftArmVelocity": velocity,
                    "RightArmVelocity": velocity,
                },
            )
        except Exception as exc:
            print(f"Error in arm swing: {exc}")
            break
        if _wait_or_stop(stop_event, period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break

        try:
            misty.perform_action(
                "arms_move",
                {
                    "LeftArmPosition": down_deg,
                    "RightArmPosition": down_deg,
                    "LeftArmVelocity": velocity,
                    "RightArmVelocity": velocity,
                },
            )
        except Exception as exc:
            print(f"Error in arm swing: {exc}")
            break
        if _wait_or_stop(stop_event, period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break
