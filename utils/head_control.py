from __future__ import annotations

import threading
import time

import requests


_ACTION_TIMEOUT_S = 2.0


def _perform_action_with_timeout(misty, action_name: str, payload: dict, timeout_s: float | None = None) -> bool:
    effective_timeout_s = max(0.1, float(timeout_s if timeout_s is not None else _ACTION_TIMEOUT_S))
    error_box: dict[str, Exception] = {}
    done = threading.Event()

    def _run_action() -> None:
        try:
            misty.perform_action(action_name, payload)
        except Exception as exc:
            error_box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_run_action, daemon=True)
    worker.start()
    if not done.wait(timeout=effective_timeout_s):
        print(f"Timed out waiting for Misty action '{action_name}' after {effective_timeout_s:.2f}s")
        return False
    if "error" in error_box:
        print(f"Error running Misty action '{action_name}': {error_box['error']}")
        return False
    return True


def _head_move(misty, timeout_s: float | None = None, **payload) -> bool:
    return _perform_action_with_timeout(misty, "head_move", payload, timeout_s=timeout_s)


def _arms_move(misty, timeout_s: float | None = None, **payload) -> bool:
    return _perform_action_with_timeout(misty, "arms_move", payload, timeout_s=timeout_s)


def reset_arms_down(misty, cfg) -> None:
    arm_down_deg = int(getattr(cfg, "distraction_both_arms_down_deg", 80))
    _arms_move(misty, LeftArmPosition=arm_down_deg, RightArmPosition=arm_down_deg,
               LeftArmVelocity=60, RightArmVelocity=60)


def _estimate_motion_duration_s(start_deg: float, target_deg: float, velocity_deg_per_s: float) -> float:
    safe_velocity = max(1.0, float(velocity_deg_per_s))
    return abs(float(target_deg) - float(start_deg)) / safe_velocity


def _estimate_dual_arm_motion_duration_s(
    left_start_deg: float,
    right_start_deg: float,
    left_target_deg: float,
    right_target_deg: float,
    velocity_deg_per_s: float,
) -> float:
    return max(
        _estimate_motion_duration_s(left_start_deg, left_target_deg, velocity_deg_per_s),
        _estimate_motion_duration_s(right_start_deg, right_target_deg, velocity_deg_per_s),
    )


def look_at_screen(misty, screen_pos) -> None:
    _head_move(
        misty,
        timeout_s=_ACTION_TIMEOUT_S,
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
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    _head_move(
        misty,
        timeout_s=action_timeout_s,
        Yaw=ack_yaw,
        Pitch=base_pitch,
        Velocity=head_velocity,
    )
    _head_move(
        misty,
        timeout_s=action_timeout_s,
        Yaw=ack_yaw,
        Pitch=nod_down_pitch,
        Velocity=nod_velocity,
    )
    time.sleep(nod_hold_s)
    _head_move(
        misty,
        timeout_s=action_timeout_s,
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


def _perform_redirect_nod(misty, cfg, stop_event=None) -> bool:
    action_name = str(getattr(cfg, "redirect_nod_action_name", "head-down-up-nod"))
    if _start_builtin_action(misty, cfg, action_name):
        wait_s = max(0.0, float(getattr(cfg, "redirect_nod_action_wait_s", 1.2)))
        if stop_event is not None:
            return _wait_or_stop(stop_event, wait_s)
        time.sleep(wait_s)
    return False


def _perform_left_arm_cue(misty, cfg, repetitions: int, sleep_fn=None, action_timeout_s: float | None = None) -> bool:
    arm_up_deg = int(getattr(cfg, "redirect_left_arm_up_deg", -65))
    arm_down_deg = int(getattr(cfg, "redirect_left_arm_down_deg", 80))
    arm_velocity = int(getattr(cfg, "redirect_left_arm_velocity", 85))
    arm_hold_s = max(0.0, float(getattr(cfg, "redirect_left_arm_hold_s", 0.5)))
    arm_pause_s = max(0.0, float(getattr(cfg, "redirect_left_arm_pause_s", 0.5)))

    def _default_sleep(duration_s: float) -> bool:
        time.sleep(duration_s)
        return False

    _do_sleep = sleep_fn if sleep_fn is not None else _default_sleep

    for _ in range(max(0, repetitions)):
        _arms_move(
            misty,
            timeout_s=action_timeout_s,
            LeftArmPosition=arm_up_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=arm_velocity,
            RightArmVelocity=arm_velocity,
        )
        if _do_sleep(arm_hold_s):
            return True
        _arms_move(
            misty,
            timeout_s=action_timeout_s,
            LeftArmPosition=arm_down_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=arm_velocity,
            RightArmVelocity=arm_velocity,
        )
        if _do_sleep(arm_pause_s):
            return True
    return False


def _perform_both_arm_wave(misty, cfg, sleep_fn=None, action_timeout_s: float | None = None) -> bool:
    arm_up_deg = int(getattr(cfg, "distraction_both_arms_up_deg", -80))
    arm_mid_deg = int(getattr(cfg, "distraction_both_arms_mid_deg", 0))
    arm_rest_down_deg = int(getattr(cfg, "distraction_both_arms_down_deg", 80))
    arm_velocity = int(getattr(cfg, "distraction_both_arms_velocity", 110))
    up_hold_s = max(0.0, float(getattr(cfg, "distraction_both_arms_up_hold_s", 0.5)))
    mid_hold_s = max(0.0, float(getattr(cfg, "distraction_both_arms_mid_hold_s", 0.3)))

    def _default_sleep(duration_s: float) -> bool:
        time.sleep(duration_s)
        return False

    _do_sleep = sleep_fn if sleep_fn is not None else _default_sleep
    current_left_arm_deg = float(arm_rest_down_deg)
    current_right_arm_deg = float(arm_rest_down_deg)

    # Step 1: raise both arms to highest point, hold
    move_s = _estimate_dual_arm_motion_duration_s(
        current_left_arm_deg, current_right_arm_deg,
        arm_up_deg, arm_up_deg, arm_velocity,
    )
    _arms_move(
        misty, timeout_s=action_timeout_s,
        LeftArmPosition=arm_up_deg, RightArmPosition=arm_up_deg,
        LeftArmVelocity=arm_velocity, RightArmVelocity=arm_velocity,
    )
    current_left_arm_deg = float(arm_up_deg)
    current_right_arm_deg = float(arm_up_deg)
    if _do_sleep(move_s + up_hold_s):
        return True

    # Step 2: lower to middle point, hold
    move_s = _estimate_dual_arm_motion_duration_s(
        current_left_arm_deg, current_right_arm_deg,
        arm_mid_deg, arm_mid_deg, arm_velocity,
    )
    _arms_move(
        misty, timeout_s=action_timeout_s,
        LeftArmPosition=arm_mid_deg, RightArmPosition=arm_mid_deg,
        LeftArmVelocity=arm_velocity, RightArmVelocity=arm_velocity,
    )
    current_left_arm_deg = float(arm_mid_deg)
    current_right_arm_deg = float(arm_mid_deg)
    if _do_sleep(move_s + mid_hold_s):
        return True

    # Step 3: raise both arms to highest point again, hold
    move_s = _estimate_dual_arm_motion_duration_s(
        current_left_arm_deg, current_right_arm_deg,
        arm_up_deg, arm_up_deg, arm_velocity,
    )
    _arms_move(
        misty, timeout_s=action_timeout_s,
        LeftArmPosition=arm_up_deg, RightArmPosition=arm_up_deg,
        LeftArmVelocity=arm_velocity, RightArmVelocity=arm_velocity,
    )
    current_left_arm_deg = float(arm_up_deg)
    current_right_arm_deg = float(arm_up_deg)
    if _do_sleep(move_s + up_hold_s):
        return True

    # Step 4: lower to rest position (reset)
    move_s = _estimate_dual_arm_motion_duration_s(
        current_left_arm_deg, current_right_arm_deg,
        arm_rest_down_deg, arm_rest_down_deg, arm_velocity,
    )
    _arms_move(
        misty, timeout_s=action_timeout_s,
        LeftArmPosition=arm_rest_down_deg, RightArmPosition=arm_rest_down_deg,
        LeftArmVelocity=arm_velocity, RightArmVelocity=arm_velocity,
    )
    if _do_sleep(move_s):
        return True

    return False


def perform_distraction_start_sequence(misty, cfg, screen_pos, stop_event=None) -> None:
    head_velocity = int(getattr(cfg, "redirect_head_velocity", 100))
    arm_down_deg = int(getattr(cfg, "distraction_both_arms_down_deg", 80))
    left_turn_yaw = float(getattr(cfg, "distraction_user_turn_yaw_deg", -45.0))
    left_turn_move_s = max(0.0, float(getattr(cfg, "distraction_user_turn_move_s", 0.45)))
    user_focus_pause_s = max(0.0, float(getattr(cfg, "distraction_user_focus_pause_s", 2.0)))
    left_arm_repetitions = max(0, int(getattr(cfg, "distraction_left_arm_repetitions", 2)))
    screen_yaw = float(screen_pos.yaw) if screen_pos is not None else 0.0
    base_pitch = float(screen_pos.pitch) if screen_pos is not None else 0.0
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    def _sleep(duration_s: float) -> bool:
        if stop_event is not None:
            return _wait_or_stop(stop_event, duration_s)
        time.sleep(duration_s)
        return False

    def _recover() -> None:
        recover_yaw = float(screen_pos.yaw) if screen_pos is not None else 0.0
        recover_pitch = float(screen_pos.pitch) if screen_pos is not None else 0.0
        _head_move(misty, timeout_s=action_timeout_s, Yaw=recover_yaw, Pitch=recover_pitch, Velocity=100)
        _arms_move(
            misty,
            timeout_s=action_timeout_s,
            LeftArmPosition=arm_down_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=100,
            RightArmVelocity=100,
        )

    _head_move(
        misty,
        timeout_s=action_timeout_s,
        Yaw=left_turn_yaw,
        Pitch=base_pitch,
        Velocity=head_velocity,
    )
    turn_to_user_move_s = max(
        left_turn_move_s,
        _estimate_motion_duration_s(screen_yaw, left_turn_yaw, head_velocity),
    )
    if _sleep(turn_to_user_move_s):
        _recover()
        return

    if _sleep(user_focus_pause_s):
        _recover()
        return

    recover_yaw = screen_yaw
    recover_pitch = float(screen_pos.pitch) if screen_pos is not None else 0.0
    _head_move(
        misty,
        timeout_s=action_timeout_s,
        Yaw=recover_yaw,
        Pitch=recover_pitch,
        Velocity=head_velocity,
    )
    screen_focus_pause_s = max(0.0, float(getattr(cfg, "redirect_screen_focus_pause_s", 1.0)))
    return_to_screen_move_s = _estimate_motion_duration_s(left_turn_yaw, recover_yaw, head_velocity)
    if _sleep(return_to_screen_move_s + screen_focus_pause_s):
        _recover()
        return

    if _perform_left_arm_cue(misty, cfg, left_arm_repetitions, sleep_fn=_sleep, action_timeout_s=action_timeout_s):
        _recover()
        return

    settle_s = max(0.0, float(getattr(cfg, "redirect_settle_s", 0.0)))
    _sleep(settle_s)


def perform_head_redirect_only(misty, cfg, screen_pos, stop_event=None) -> None:
    """Turn head toward user, pause while facing them, return to screen. No arm movement."""
    head_velocity = int(getattr(cfg, "redirect_head_velocity", 100))
    left_turn_yaw = float(getattr(cfg, "distraction_user_turn_yaw_deg", -45.0))
    left_turn_move_s = max(0.0, float(getattr(cfg, "distraction_user_turn_move_s", 0.45)))
    user_focus_pause_s = max(0.0, float(getattr(cfg, "distraction_user_focus_pause_s", 2.0)))
    screen_yaw = float(screen_pos.yaw) if screen_pos is not None else 0.0
    base_pitch = float(screen_pos.pitch) if screen_pos is not None else 0.0
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    def _sleep(duration_s: float) -> bool:
        if stop_event is not None:
            return _wait_or_stop(stop_event, duration_s)
        time.sleep(duration_s)
        return False

    def _recover() -> None:
        _head_move(misty, timeout_s=action_timeout_s, Yaw=screen_yaw, Pitch=base_pitch, Velocity=100)
        # No arm reset needed since arms were never moved in this function

    # 1. Turn head toward user
    _head_move(misty, timeout_s=action_timeout_s, Yaw=left_turn_yaw, Pitch=base_pitch, Velocity=head_velocity)
    turn_to_user_move_s = max(
        left_turn_move_s,
        _estimate_motion_duration_s(screen_yaw, left_turn_yaw, head_velocity),
    )
    if _sleep(turn_to_user_move_s):
        _recover()
        return

    # 2. Pause while looking at user
    if _sleep(user_focus_pause_s):
        _recover()
        return

    # 3. Return head to screen — NO arm cue
    _head_move(misty, timeout_s=action_timeout_s, Yaw=screen_yaw, Pitch=base_pitch, Velocity=head_velocity)
    screen_focus_pause_s = max(0.0, float(getattr(cfg, "redirect_screen_focus_pause_s", 1.5)))
    return_to_screen_move_s = _estimate_motion_duration_s(left_turn_yaw, screen_yaw, head_velocity)
    if _sleep(return_to_screen_move_s + screen_focus_pause_s):
        _recover()
        return


def redirect_attention_to_screen(misty, cfg, screen_pos, stop_event=None) -> None:
    head_velocity = int(getattr(cfg, "redirect_head_velocity", 100))
    arm_down_deg = int(getattr(cfg, "redirect_left_arm_down_deg", 80))
    arm_velocity = int(getattr(cfg, "redirect_left_arm_velocity", 85))
    arm_repetitions = max(0, int(getattr(cfg, "redirect_left_arm_repetitions", 1)))
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    def _sleep(duration_s: float) -> bool:
        if stop_event is not None:
            return _wait_or_stop(stop_event, duration_s)
        time.sleep(duration_s)
        return False

    def _recover() -> None:
        # 被 stop 中断时，以最大速度快速归位，避免用户回神后机器人仍在缓慢运动
        _head_move(misty, timeout_s=action_timeout_s, Yaw=screen_pos.yaw, Pitch=screen_pos.pitch, Velocity=100)
        _arms_move(
            misty,
            timeout_s=action_timeout_s,
            LeftArmPosition=arm_down_deg,
            RightArmPosition=arm_down_deg,
            LeftArmVelocity=100,
            RightArmVelocity=100,
        )

    # 1. 官方标准点头动作
    if _perform_redirect_nod(misty, cfg, stop_event):
        _recover()
        return

    # 2. 转向屏幕
    _head_move(
        misty,
        timeout_s=action_timeout_s,
        Yaw=screen_pos.yaw,
        Pitch=screen_pos.pitch,
        Velocity=head_velocity,
    )
    screen_focus_pause_s = max(0.0, float(getattr(cfg, "redirect_screen_focus_pause_s", 2.0)))
    if _sleep(screen_focus_pause_s):
        _recover()
        return

    # 3. 左臂动作
    if _perform_left_arm_cue(misty, cfg, arm_repetitions, sleep_fn=_sleep, action_timeout_s=action_timeout_s):
        _recover()
        return

    settle_s = max(0.0, float(getattr(cfg, "redirect_settle_s", 0.0)))
    _sleep(settle_s)


def cue_screen_with_left_arm(misty, cfg, repetitions: int = 1, stop_event=None) -> None:
    sleep_fn = (lambda d: _wait_or_stop(stop_event, d)) if stop_event is not None else None
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))
    _perform_left_arm_cue(misty, cfg, repetitions, sleep_fn=sleep_fn, action_timeout_s=action_timeout_s)


def _wait_or_stop(stop_event, duration_s: float) -> bool:
    return stop_event.wait(timeout=max(0.0, duration_s))


def shake_head_only(misty, cfg, stop_event, position_callback=None) -> None:
    side_pause_s = float(getattr(cfg, "shake_pause_s", 0.5))
    side_period_s = float(getattr(cfg, "shake_period_s", 0.6))
    center_yaw = float(getattr(cfg, "shake_center_yaw_deg", 0.0))
    center_period_s = float(getattr(cfg, "shake_center_period_s", max(0.25, side_period_s * 0.5)))
    center_pause_s = float(getattr(cfg, "shake_center_pause_s", max(0.1, side_pause_s * 0.35)))
    velocity = int(getattr(cfg, "shake_velocity", 80))
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    def move_and_hold(yaw: float, move_s: float, hold_s: float) -> bool:
        if not _head_move(
            misty,
            timeout_s=action_timeout_s,
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
    pause_s = float(getattr(cfg, "arm_swing_pause_s", 0.5))
    action_timeout_s = max(0.1, float(getattr(cfg, "robot_action_timeout_s", _ACTION_TIMEOUT_S)))

    while not stop_event.is_set():
        if not _arms_move(
            misty,
            timeout_s=action_timeout_s,
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
            timeout_s=action_timeout_s,
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
