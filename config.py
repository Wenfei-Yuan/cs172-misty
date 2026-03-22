from __future__ import annotations

import os
from dataclasses import dataclass

from misty2py.utils.env_loader import EnvLoader


@dataclass(frozen=True)
class Config:
    ip: str
    openai_api_key: str
    participant_id: str
    speech_volume: int = 90
    gaze_timeout_s: float = 60.0
    gaze_poll_interval_s: float = 2.0
    fast_gaze_poll_interval_s: float = 0.6
    fast_gaze_poll_window_s: float = 8.0
    max_attempts: int = 3
    shake_amplitude_deg: int = 60
    shake_period_s: float = 0.6
    signal_host: str = "127.0.0.1"
    signal_port: int = 5050
    default_screen_yaw: float = -40.0
    default_screen_pitch: float = -10.0
    screen_init_left_front_yaw: float = 42.0
    screen_init_left_front_pitch: float = 7.0
    screen_search_yaw_offsets: tuple[float, ...] = (0.0, -10.0, 10.0, -20.0, 20.0, -30.0, 30.0)
    screen_search_pitch_offsets: tuple[float, ...] = (0.0, -8.0, 8.0, -15.0, 15.0)
    screen_settle_s: float = 0.7
    escalation_prompts: tuple[str, ...] = (
        "Hey, are you still with me? Don't forget to focus!",
        "I noticed you're distracted. Take a breath and get back to it!",
        "Time to refocus — you're almost there. You've got this!",
    )


def load_config(participant_id: str) -> Config:
    env_loader = EnvLoader()
    ip = (env_loader.get_ip() or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or env_loader.values.get("OPENAI_API_KEY") or "").strip()
    if not ip:
        raise EnvironmentError("MISTY_IP_ADDRESS not set in .env")
    if not openai_key:
        raise EnvironmentError("OPENAI_API_KEY not set in .env")
    return Config(ip=ip, openai_api_key=openai_key, participant_id=participant_id)