from __future__ import annotations

import time


def _head_move(misty, **payload) -> None:
    try:
        misty.perform_action("head_move", payload)
    except Exception as exc:
        print(f"Error moving head: {exc}")
        return False
    return True


def _arms_move(misty, **payload) -> None:
    try:
        misty.perform_action("arms_move", payload)
    except Exception as exc:
        print(f"Error moving arms: {exc}")
        return False
    return True


def look_at_screen(misty, screen_pos) -> None:
    _head_move(
        misty,
        Yaw=screen_pos.yaw,
        Pitch=screen_pos.pitch,
        Velocity=90,
    )


def redirect_attention_to_screen(misty, cfg, screen_pos) -> None:
    forward_yaw = float(getattr(cfg, "redirect_forward_yaw", 0.0))
    forward_pitch = float(getattr(cfg, "redirect_forward_pitch", 0.0))
    forward_pause_s = max(0.0, float(getattr(cfg, "redirect_forward_pause_s", 0.4)))
    nod_down_pitch = float(getattr(cfg, "redirect_nod_down_pitch", 12.0))
    nod_velocity = int(getattr(cfg, "redirect_nod_velocity", 85))
    nod_hold_s = max(0.0, float(getattr(cfg, "redirect_nod_hold_s", 0.18)))
    head_velocity = int(getattr(cfg, "redirect_head_velocity", 100))
    arm_up_deg = int(getattr(cfg, "redirect_left_arm_up_deg", -65))
    arm_down_deg = int(getattr(cfg, "redirect_left_arm_down_deg", 80))
    arm_velocity = int(getattr(cfg, "redirect_left_arm_velocity", 65))
    arm_hold_s = max(0.0, float(getattr(cfg, "redirect_left_arm_hold_s", 0.18)))
    arm_pause_s = max(0.0, float(getattr(cfg, "redirect_left_arm_pause_s", 0.12)))
    arm_repetitions = max(0, int(getattr(cfg, "redirect_left_arm_repetitions", 2)))
    settle_s = max(0.0, float(getattr(cfg, "redirect_settle_s", 1.0)))

    _head_move(
        misty,
        Yaw=forward_yaw,
        Pitch=forward_pitch,
        Velocity=head_velocity,
    )
    time.sleep(forward_pause_s)

    _head_move(
        misty,
        Yaw=forward_yaw,
        Pitch=nod_down_pitch,
        Velocity=nod_velocity,
    )
    time.sleep(nod_hold_s)
    _head_move(
        misty,
        Yaw=forward_yaw,
        Pitch=forward_pitch,
        Velocity=nod_velocity,
    )

    _head_move(
        misty,
        Yaw=screen_pos.yaw,
        Pitch=screen_pos.pitch,
        Velocity=head_velocity,
    )

    for _ in range(arm_repetitions):
        _arms_move(
            misty,
            LeftArmPosition=arm_up_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=arm_velocity,
            RightArmVelocity=arm_velocity,
        )
        time.sleep(arm_hold_s)
        _arms_move(
            misty,
            LeftArmPosition=arm_down_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=arm_velocity,
            RightArmVelocity=arm_velocity,
        )
        time.sleep(arm_pause_s)

    time.sleep(settle_s)


def _wait_or_stop(stop_event, duration_s: float) -> bool:
    return stop_event.wait(timeout=max(0.0, duration_s))


def shake_head_only(misty, cfg, stop_event) -> None:
    pause_s = float(getattr(cfg, "shake_pause_s", 0.5))
    while not stop_event.is_set():
        if not _head_move(
            misty,
            Yaw=cfg.shake_amplitude_deg,
            Velocity=80,
        ):
            break
        if _wait_or_stop(stop_event, cfg.shake_period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break
        if not _head_move(
            misty,
            Yaw=-cfg.shake_amplitude_deg,
            Velocity=80,
        ):
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
        if not _arms_move(
            misty,
            LeftArmPosition=up_deg,
            RightArmPosition=up_deg,
            LeftArmVelocity=velocity,
            RightArmVelocity=velocity,
        ):
            break
        if _wait_or_stop(stop_event, period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break

        if not _arms_move(
            misty,
            LeftArmPosition=down_deg,
            RightArmPosition=down_deg,
            LeftArmVelocity=velocity,
            RightArmVelocity=velocity,
        ):
            break
        if _wait_or_stop(stop_event, period_s):
            break
        if _wait_or_stop(stop_event, pause_s):
            break

