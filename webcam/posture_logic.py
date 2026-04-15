def compute_screen_facing(
    yaw_deviation,
    pitch_deviation,
    disengaged,
    yaw_threshold,
    pitch_threshold,
    reengage_yaw_threshold,
    reengage_pitch_threshold,
    yaw_hysteresis_margin,
    pitch_hysteresis_margin,
):
    engaged_yaw_exit_threshold = yaw_threshold + yaw_hysteresis_margin
    engaged_pitch_exit_threshold = pitch_threshold + pitch_hysteresis_margin

    if disengaged:
        screen_facing = (
            yaw_deviation <= reengage_yaw_threshold and
            pitch_deviation <= reengage_pitch_threshold
        )
        return screen_facing, reengage_yaw_threshold, reengage_pitch_threshold

    outside_yaw = yaw_deviation > engaged_yaw_exit_threshold
    outside_pitch = pitch_deviation > engaged_pitch_exit_threshold

    if yaw_deviation <= yaw_threshold and pitch_deviation <= pitch_threshold:
        screen_facing = True
    elif outside_yaw or outside_pitch:
        screen_facing = False
    else:
        screen_facing = True

    return screen_facing, yaw_threshold, pitch_threshold


def compute_reengage_threshold(disengage_reason, default_threshold_s, gaze_threshold_s):
    if disengage_reason == "gaze_mind_wandering":
        return gaze_threshold_s
    return default_threshold_s


def compute_full_recovery(
    *,
    enable_eye_gaze,
    raw_face_present,
    yaw,
    pitch,
    screen_facing,
    smoothed_gaze_yaw,
    smoothed_gaze_pitch,
    gaze_looking_away,
    gaze_error_confirmed,
):
    # In eye-gaze mode, recovery is driven entirely by gaze: the timer was
    # triggered by gaze deviation, so gaze returning to normal is sufficient.
    # Head pose (screen_facing) is NOT required here; an independent
    # looking_away_confirmed check still guards head-pose errors separately.
    if enable_eye_gaze:
        return (
            smoothed_gaze_yaw is not None and
            smoothed_gaze_pitch is not None and
            (not gaze_looking_away) and
            (not gaze_error_confirmed)
        )

    # Head-pose-only mode: require face + valid pose + facing screen.
    return (
        raw_face_present and
        yaw is not None and
        pitch is not None and
        screen_facing
    )