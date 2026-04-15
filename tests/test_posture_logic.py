import unittest

from webcam.posture_logic import compute_full_recovery, compute_reengage_threshold, compute_screen_facing


class ComputeScreenFacingTests(unittest.TestCase):
    def test_disengaged_recovery_uses_dedicated_reengage_thresholds(self) -> None:
        screen_facing, yaw_threshold, pitch_threshold = compute_screen_facing(
            yaw_deviation=20.0,
            pitch_deviation=20.2,
            disengaged=True,
            yaw_threshold=30.0,
            pitch_threshold=14.0,
            reengage_yaw_threshold=42.0,
            reengage_pitch_threshold=24.0,
            yaw_hysteresis_margin=8.0,
            pitch_hysteresis_margin=5.0,
        )

        self.assertTrue(screen_facing)
        self.assertEqual(yaw_threshold, 42.0)
        self.assertEqual(pitch_threshold, 24.0)

    def test_engaged_thresholds_keep_pose_inside_hysteresis_band(self) -> None:
        screen_facing, yaw_threshold, pitch_threshold = compute_screen_facing(
            yaw_deviation=19.7,
            pitch_deviation=18.5,
            disengaged=False,
            yaw_threshold=30.0,
            pitch_threshold=14.0,
            reengage_yaw_threshold=42.0,
            reengage_pitch_threshold=24.0,
            yaw_hysteresis_margin=8.0,
            pitch_hysteresis_margin=5.0,
        )

        self.assertTrue(screen_facing)
        self.assertEqual(yaw_threshold, 30.0)
        self.assertEqual(pitch_threshold, 14.0)


class RecoveryLogicTests(unittest.TestCase):
    def test_gaze_mind_wandering_recovery_does_not_require_screen_facing(self) -> None:
        """gaze_mind_wandering disengagement: head facing slightly away is OK as long as gaze is back."""
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=True,
            disengage_reason="gaze_mind_wandering",
            raw_face_present=True,
            yaw=5.0,
            pitch=2.0,
            screen_facing=False,  # head not squarely forward
            smoothed_gaze_yaw=0.49,
            smoothed_gaze_pitch=0.51,
            gaze_looking_away=False,
            gaze_error_confirmed=False,
        )

        self.assertTrue(fully_recovered)

    def test_looking_away_recovery_requires_screen_facing(self) -> None:
        """looking_away disengagement: gaze alone is not enough; head must return to screen too."""
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=True,
            disengage_reason="looking_away",
            raw_face_present=True,
            yaw=5.0,
            pitch=2.0,
            screen_facing=False,  # still slightly away
            smoothed_gaze_yaw=0.49,
            smoothed_gaze_pitch=0.51,
            gaze_looking_away=False,
            gaze_error_confirmed=False,
        )

        self.assertFalse(fully_recovered)

    def test_looking_away_recovery_allowed_when_screen_facing_and_gaze_ok(self) -> None:
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=True,
            disengage_reason="looking_away",
            raw_face_present=True,
            yaw=5.0,
            pitch=2.0,
            screen_facing=True,
            smoothed_gaze_yaw=0.49,
            smoothed_gaze_pitch=0.51,
            gaze_looking_away=False,
            gaze_error_confirmed=False,
        )

        self.assertTrue(fully_recovered)

    def test_recovery_blocked_when_pose_invalid(self) -> None:
        """No recovery when yaw/pitch are None (pose estimation failed)."""
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=True,
            disengage_reason="looking_away",
            raw_face_present=True,
            yaw=None,
            pitch=2.0,
            screen_facing=True,
            smoothed_gaze_yaw=0.49,
            smoothed_gaze_pitch=0.51,
            gaze_looking_away=False,
            gaze_error_confirmed=False,
        )

        self.assertFalse(fully_recovered)

    def test_gaze_recovery_not_allowed_while_error_still_confirmed(self) -> None:
        """Recovery must not fire while gaze_error_confirmed is still True."""
        fully_recovered = compute_full_recovery(
            enable_eye_gaze=True,
            disengage_reason="gaze_mind_wandering",
            raw_face_present=True,
            yaw=5.0,
            pitch=2.0,
            screen_facing=True,
            smoothed_gaze_yaw=0.49,
            smoothed_gaze_pitch=0.51,
            gaze_looking_away=False,
            gaze_error_confirmed=True,  # timer still active
        )

        self.assertFalse(fully_recovered)

    def test_gaze_trigger_uses_default_reengage_threshold(self) -> None:
        threshold_s = compute_reengage_threshold(
            disengage_reason="gaze_mind_wandering",
            default_threshold_s=1.5,
            gaze_threshold_s=3.0,
        )

        self.assertEqual(threshold_s, 1.5)

    def test_non_gaze_trigger_uses_default_reengage_threshold(self) -> None:
        threshold_s = compute_reengage_threshold(
            disengage_reason="looking_away",
            default_threshold_s=1.5,
            gaze_threshold_s=3.0,
        )

        self.assertEqual(threshold_s, 1.5)


if __name__ == "__main__":
    unittest.main()