from __future__ import annotations

import time

import requests


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

def acknowledge_gaze_recovery(misty, cfg, current_yaw: float | None = None) -> None:
    ack_yaw = float(
        current_yaw
        if current_yaw is not None
        else getattr(cfg, "acknowledgement_yaw_deg", getattr(cfg, "shake_center_yaw_deg", 0.0))
    )
    base_pitch = float(getattr(cfg, "acknowledgement_pitch_deg", 0.0))
    nod_down_pitch = float(getattr(cfg, "acknowledgement_nod_down_pitch", 12.0))
    head_velocity = int(getattr(cfg, "acknowledgement_head_velocity", 95))
    nod_velocity = int(getattr(cfg, "acknowledgement_nod_velocity", 85))
    nod_hold_s = max(0.0, float(getattr(cfg, "acknowledgement_nod_hold_s", 0.18)))

    _head_move(
        misty,
        Yaw=ack_yaw,
        Pitch=base_pitch,
        Velocity=head_velocity,
    )
    _head_move(
        misty,
        Yaw=ack_yaw,
        Pitch=nod_down_pitch,
        Velocity=nod_velocity,
    )
    time.sleep(nod_hold_s)
    _head_move(
        misty,
        Yaw=ack_yaw,
        Pitch=base_pitch,
        Velocity=nod_velocity,
    )


def _start_builtin_action(misty, cfg, action_name: str) -> bool:
    robot_ip = getattr(misty, "ip", None) or getattr(cfg, "ip", None)
    if not robot_ip:
        print(f"Unable to start Misty action '{action_name}': robot IP not available.")
        return False

    url = f"http://{robot_ip}/api/actions/start"
    payload = {"Name": action_name}
    timeout_s = max(0.1, float(getattr(cfg, "redirect_action_timeout_s", 10.0)))

    try:
        response = requests.post(url, json=payload, timeout=timeout_s)
        response.raise_for_status()
    except Exception as exc:
        print(f"Error starting Misty action '{action_name}': {exc}")
        return False
    return True


def _perform_redirect_nod(misty, cfg) -> None:
    action_name = str(getattr(cfg, "redirect_nod_action_name", "head-down-up-nod"))
    if _start_builtin_action(misty, cfg, action_name):
        time.sleep(max(0.0, float(getattr(cfg, "redirect_nod_action_wait_s", 1.2))))


def redirect_attention_to_screen(misty, cfg, screen_pos) -> None:
    head_velocity = int(getattr(cfg, "redirect_head_velocity", 100))

    # 1. 官方标准点头动作
    _perform_redirect_nod(misty, cfg)

    # 2. 转向屏幕
    _head_move(
        misty,
        Yaw=screen_pos.yaw,
        Pitch=screen_pos.pitch,
        Velocity=head_velocity,
    )
    screen_focus_pause_s = max(0.0, float(getattr(cfg, "redirect_screen_focus_pause_s", 2.5)))
    time.sleep(screen_focus_pause_s)

    # 3. 左臂动作
    arm_up_deg = int(getattr(cfg, "redirect_left_arm_up_deg", -65))
    arm_down_deg = int(getattr(cfg, "redirect_left_arm_down_deg", 80))
    arm_velocity = int(getattr(cfg, "redirect_left_arm_velocity", 85))
    arm_hold_s = max(0.0, float(getattr(cfg, "redirect_left_arm_hold_s", 3)))
    arm_pause_s = max(0.0, float(getattr(cfg, "redirect_left_arm_pause_s", 0.5)))
    arm_repetitions = max(0, int(getattr(cfg, "redirect_left_arm_repetitions", 2)))
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

    settle_s = max(0.0, float(getattr(cfg, "redirect_settle_s", 1.0)))
    time.sleep(settle_s)


def cue_screen_with_left_arm(misty, cfg, repetitions: int = 2) -> None:
    arm_up_deg = int(getattr(cfg, "redirect_left_arm_up_deg", -65))
    arm_down_deg = int(getattr(cfg, "redirect_left_arm_down_deg", 80))
    arm_velocity = int(getattr(cfg, "redirect_left_arm_velocity", 85))
    arm_hold_s = max(0.0, float(getattr(cfg, "redirect_left_arm_hold_s", 3)))
    arm_pause_s = max(0.0, float(getattr(cfg, "redirect_left_arm_pause_s", 0.5)))

    for _ in range(max(0, repetitions)):
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


def _wait_or_stop(stop_event, duration_s: float) -> bool:
    return stop_event.wait(timeout=max(0.0, duration_s))


def shake_head_only(misty, cfg, stop_event, position_callback=None) -> None:
    side_pause_s = float(getattr(cfg, "shake_pause_s", 0.5))
    side_period_s = float(getattr(cfg, "shake_period_s", 0.6))
    center_yaw = float(getattr(cfg, "shake_center_yaw_deg", 0.0))
    center_period_s = float(getattr(cfg, "shake_center_period_s", max(0.25, side_period_s * 0.5)))
    center_pause_s = float(getattr(cfg, "shake_center_pause_s", max(0.1, side_pause_s * 0.35)))
    velocity = int(getattr(cfg, "shake_velocity", 80))

    def move_and_hold(yaw: float, move_s: float, hold_s: float) -> bool:
        if not _head_move(
            misty,
            Yaw=yaw,
            Velocity=velocity,
        ):
            return False
        if _wait_or_stop(stop_event, move_s):
            return False
        if position_callback:
            try:
                position_callback(float(yaw))
            except Exception:
                pass
        if _wait_or_stop(stop_event, hold_s):
            return False
        return True

    while not stop_event.is_set():
        if not move_and_hold(cfg.shake_amplitude_deg, side_period_s, side_pause_s):
            break
        if not move_and_hold(center_yaw, center_period_s, center_pause_s):
            break
        if not move_and_hold(-cfg.shake_amplitude_deg, side_period_s, side_pause_s):
            break
        if not move_and_hold(center_yaw, center_period_s, center_pause_s):
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
