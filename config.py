from __future__ import annotations

import os
from dataclasses import dataclass

from misty2py.utils.env_loader import EnvLoader


@dataclass(frozen=True)
class Config:
    ip: str
    openai_api_key: str
    participant_id: str
    openai_timeout_s: float = 6.0
    openai_max_retries: int = 0
    camera_timeout_s: float = 1.5
    speech_volume: int = 30
    speech_voice: str = "Justin"
    gaze_timeout_s: float = 45.0
    gaze_poll_interval_s: float = 0.7
    fast_gaze_poll_interval_s: float = 0.18
    fast_gaze_poll_window_s: float = 12.0
    vision_max_image_dim_px: int = 768
    vision_jpeg_quality: int = 72
    max_attempts: int = 3
    shake_amplitude_deg: int = 45
    shake_period_s: float = 0.45
    shake_pause_s: float = 0.22
    shake_center_yaw_deg: float = 0.0
    shake_center_period_s: float = 0.25
    shake_center_pause_s: float = 0.35
    shake_velocity: int = 95
    distraction_shake_cycles: int = 2
    arm_swing_up_deg: int = -80
    arm_swing_down_deg: int = 80
    arm_swing_velocity: int = 55
    arm_swing_period_s: float = 0.5
    arm_swing_pause_s: float = 0.2
    signal_host: str = "127.0.0.1"
    signal_port: int = 5050
    robot_action_timeout_s: float = 2.0
    speech_timeout_s: float = 8.0
    screen_search_seed_yaw: float = 0.0
    screen_search_seed_pitch: float = 0.0
    screen_search_yaw_offsets: tuple[float, ...] = (0.0, -10.0, 10.0, -20.0, 20.0, -30.0, 30.0)
    screen_search_pitch_offsets: tuple[float, ...] = (0.0, -8.0, 8.0, -15.0, 15.0)
    screen_search_fine_yaw_offsets: tuple[float, ...] = (0.0, -5.0, 5.0, -10.0, 10.0)
    screen_search_fine_pitch_offsets: tuple[float, ...] = (0.0, -4.0, 4.0, -8.0, 8.0)
    screen_alignment_confirm_checks: int = 2
    screen_alignment_confirm_settle_s: float = 0.4
    screen_settle_s: float = 0.7
    return_to_screen_pause_s: float = 2.0
    redirect_nod_repetitions: int = 3
    redirect_left_arm_repetitions: int = 1
    redirect_nod_down_pitch: float = 18.0
    redirect_nod_velocity: int = 68
    redirect_nod_move_s: float = 0.45
    redirect_nod_hold_s: float = 0.28
    redirect_nod_return_s: float = 0.25
    redirect_nod_action_name: str = "head-down-up-nod"
    redirect_nod_action_wait_s: float = 1.2
    redirect_action_timeout_s: float = 10.0
    redirect_screen_focus_pause_s: float = 1.5
    redirect_left_arm_pause_s: float = 0.3
    redirect_settle_s: float = 1.5
    redirect_confirmation_wait_s: float = 60.0
    distraction_user_focus_pause_s: float = 2.0
    distraction_both_arms_repetitions: int = 2  # vestigial — kept for config compat
    distraction_both_arms_mid_deg: int = 0
    distraction_both_arms_velocity: int = 110
    distraction_both_arms_up_hold_s: float = 2.0
    distraction_both_arms_mid_hold_s: float = 2.0
    cache_screen_pos: bool = True
    escalation_prompts: tuple[str, ...] = (
        "Hey, are you still with me? Don't forget to focus!",
        "I noticed you're distracted. Take a breath and get back to it!",
        "Time to refocus — you're almost there. You've got this!",
    )
    initial_self_recovery_wait_s: float = 15.0
    confused_sound_followup_wait_s: float = 15.0
    head_redirect_followup_wait_s: float = 120.0
    voice_prompt_followup_wait_s: float = 15.0
    fatigue_offer_wait_s: float = 20.0
    fatigue_default_mode: str = "para"
    fatigue_offer_enabled: bool = True
    confused_sound_name: str = "s_DisorientedConfused.wav"


def load_config(participant_id: str) -> Config:
    env_loader = EnvLoader()
    ip = (env_loader.get_ip() or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or env_loader.values.get("OPENAI_API_KEY") or "").strip()
    if not ip:
        raise EnvironmentError("MISTY_IP_ADDRESS not set in .env")
    if not openai_key:
        raise EnvironmentError("OPENAI_API_KEY not set in .env")
    return Config(ip=ip, openai_api_key=openai_key, participant_id=participant_id)
