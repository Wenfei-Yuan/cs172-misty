from __future__ import annotations

import unittest
from types import SimpleNamespace

import utils.head_control as head_control
from utils.head_control import (
    acknowledge_gaze_recovery,
    cue_screen_with_left_arm,
    redirect_attention_to_screen,
    shake_head_only,
    swing_arms_only,
)


class _FakeMisty:
    def __init__(self, ip: str = "10.0.0.5") -> None:
        self.actions = []
        self.ip = ip

    def perform_action(self, name: str, payload: dict) -> None:
        self.actions.append((name, payload))


class _FakeStopEvent:
    def __init__(self, wait_results: list[bool]) -> None:
        self.wait_results = list(wait_results)
        self.wait_calls = []

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return self.wait_results.pop(0)


class HeadControlTests(unittest.TestCase):
    def test_redirect_attention_nods_then_turns_to_screen_before_two_arm_cues(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(
            redirect_nod_action_name="head-down-up-nod",
            redirect_nod_action_wait_s=3.0,
            redirect_action_timeout_s=4.0,
            redirect_head_velocity=88,
            redirect_screen_focus_pause_s=2.5,
            redirect_left_arm_up_deg=-65,
            redirect_left_arm_down_deg=80,
            redirect_left_arm_velocity=70,
            redirect_left_arm_hold_s=0.6,
            redirect_left_arm_pause_s=0.4,
            redirect_left_arm_repetitions=2,
            redirect_settle_s=0.1,
        )
        screen_pos = SimpleNamespace(yaw=24.0, pitch=8.0)
        sleep_calls = []
        original_time = head_control.time
        original_requests = head_control.requests
        request_calls = []

        try:
            head_control.time = SimpleNamespace(sleep=lambda seconds: sleep_calls.append(seconds))
            head_control.requests = SimpleNamespace(
                post=lambda url, json, timeout: request_calls.append((url, json, timeout)) or SimpleNamespace(
                    raise_for_status=lambda: None
                )
            )
            redirect_attention_to_screen(misty, cfg, screen_pos)
        finally:
            head_control.time = original_time
            head_control.requests = original_requests

        self.assertEqual(
            misty.actions,
            [
                ("head_move", {"Yaw": 24.0, "Pitch": 8.0, "Velocity": 88}),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -65,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 70,
                        "RightArmVelocity": 70,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 70,
                        "RightArmVelocity": 70,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -65,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 70,
                        "RightArmVelocity": 70,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 70,
                        "RightArmVelocity": 70,
                    },
                ),
            ],
        )
        self.assertEqual(
            request_calls,
            [
                (
                    "http://10.0.0.5/api/actions/start",
                    {"Name": "head-down-up-nod"},
                    4.0,
                ),
            ],
        )
        self.assertEqual(
            sleep_calls,
            [3.0, 2.5, 0.6, 0.4, 0.6, 0.4, 0.1],
        )

    def test_acknowledge_gaze_recovery_nods_before_return(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(
            acknowledgement_pitch_deg=1.0,
            acknowledgement_nod_down_pitch=14.0,
            acknowledgement_head_velocity=90,
            acknowledgement_nod_velocity=82,
            acknowledgement_nod_hold_s=0.0,
        )

        acknowledge_gaze_recovery(misty, cfg, current_yaw=12.0)

        self.assertEqual(
            misty.actions,
            [
                ("head_move", {"Yaw": 12.0, "Pitch": 1.0, "Velocity": 90}),
                ("head_move", {"Yaw": 12.0, "Pitch": 14.0, "Velocity": 82}),
                ("head_move", {"Yaw": 12.0, "Pitch": 1.0, "Velocity": 82}),
            ],
        )

    def test_shake_head_only_pauses_at_both_endpoints(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(shake_amplitude_deg=60, shake_period_s=0.6, shake_pause_s=0.5)
        stop_event = _FakeStopEvent([False, False, False, False, False, False, False, True])

        shake_head_only(misty, cfg, stop_event)

        self.assertEqual(
            misty.actions,
            [
                ("head_move", {"Yaw": 60, "Velocity": 80}),
                ("head_move", {"Yaw": 0.0, "Velocity": 80}),
                ("head_move", {"Yaw": -60, "Velocity": 80}),
                ("head_move", {"Yaw": 0.0, "Velocity": 80}),
            ],
        )
        self.assertEqual(stop_event.wait_calls, [0.6, 0.5, 0.3, 0.175, 0.6, 0.5, 0.3, 0.175])

    def test_swing_arms_only_pauses_at_both_endpoints(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(
            arm_swing_up_deg=-80,
            arm_swing_down_deg=80,
            arm_swing_velocity=55,
            arm_swing_period_s=0.5,
            arm_swing_pause_s=0.2,
        )
        stop_event = _FakeStopEvent([False, False, False, True])

        swing_arms_only(misty, cfg, stop_event)

        self.assertEqual(
            misty.actions,
            [
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -80,
                        "RightArmPosition": -80,
                        "LeftArmVelocity": 55,
                        "RightArmVelocity": 55,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 55,
                        "RightArmVelocity": 55,
                    },
                ),
            ],
        )
        self.assertEqual(stop_event.wait_calls, [0.5, 0.2, 0.5, 0.2])

    def test_cue_screen_with_left_arm_only_moves_left_arm_up_twice(self) -> None:
        misty = _FakeMisty()
        cfg = SimpleNamespace(
            redirect_left_arm_up_deg=-65,
            redirect_left_arm_down_deg=80,
            redirect_left_arm_velocity=65,
            redirect_left_arm_hold_s=0.0,
            redirect_left_arm_pause_s=0.0,
        )

        cue_screen_with_left_arm(misty, cfg, repetitions=2)

        self.assertEqual(
            misty.actions,
            [
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -65,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 65,
                        "RightArmVelocity": 65,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 65,
                        "RightArmVelocity": 65,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": -65,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 65,
                        "RightArmVelocity": 65,
                    },
                ),
                (
                    "arms_move",
                    {
                        "LeftArmPosition": 80,
                        "RightArmPosition": 80,
                        "LeftArmVelocity": 65,
                        "RightArmVelocity": 65,
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
