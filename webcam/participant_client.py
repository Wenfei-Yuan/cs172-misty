import asyncio
import json
import time
import cv2
import numpy as np
import mediapipe as mp
import websockets

SERVER_IP = "10.0.0.162"   # 改成你的电脑IP
SERVER_PORT = 8765
WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}"
RECONNECT_DELAY = 3.0

DISENGAGE_THRESHOLD = 3.0      # 偏离持续超过3秒 -> disengaged
REENGAGE_THRESHOLD = 1.0       # 恢复朝向屏幕后持续1秒 -> re-engaged

EVENT_CALIBRATION_COMPLETE = "calibration_complete"
EVENT_POSTURE = "posture"

CALIBRATION_DURATION = 3.0     # 启动后前3秒自动校准
YAW_DEVIATION_THRESHOLD = 34.0
PITCH_DEVIATION_THRESHOLD = 18.0
REENGAGE_YAW_DEVIATION_THRESHOLD = 50.0
REENGAGE_PITCH_DEVIATION_THRESHOLD = 30.0
YAW_HYSTERESIS_MARGIN = 10.0
PITCH_HYSTERESIS_MARGIN = 7.0
POSE_SMOOTHING_ALPHA = 0.2
REENGAGE_BREAK_TOLERANCE = 0.35
AWAY_BREAK_TOLERANCE = 0.25
MIN_VALID_FACE_FRAMES = 10     # 连续有效人脸/姿态帧达到后才开始判定
FACE_MISSING_GRACE_PERIOD = 0.8
POSE_INVALID_GRACE_PERIOD = 0.8

# ─── Eye Gaze Augmentation ───
ENABLE_EYE_GAZE = True                        # Set to False to disable gaze augmentation entirely
GAZE_SMOOTHING_ALPHA = 0.15                    # EMA alpha for gaze (lower than head pose for noise)
GAZE_YAW_DEVIATION_THRESHOLD = 0.10           # Stricter: 0.15 → 0.10
GAZE_PITCH_DEVIATION_THRESHOLD = 0.06          # Stricter: 0.18 → 0.12
GAZE_YAW_HYSTERESIS = 0.03                     # Hysteresis margin for horizontal gaze
GAZE_PITCH_HYSTERESIS = 0.02                   # Hysteresis margin for vertical gaze
GAZE_MIND_WANDERING_DURATION = 2.0             # Seconds of sustained gaze deviation before MW
GAZE_MW_BREAK_TOLERANCE = 0.5                  # Brief gaze-returns don't reset MW timer
GAZE_HEAD_YAW_LIMIT = 50                    # Only evaluate gaze when head yaw_dev < this (parallax guard)

mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)

LANDMARK_IDS = {
    "nose_tip": 1,
    "chin": 152,
    "left_eye_outer": 33,
    "right_eye_outer": 263,
    "left_mouth": 61,
    "right_mouth": 291,
}

# Iris and eye corner landmarks (available when refine_landmarks=True)
LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473
LEFT_EYE_INNER = 133
LEFT_EYE_OUTER = 33
RIGHT_EYE_INNER = 362
RIGHT_EYE_OUTER = 263
LEFT_EYE_TOP = 159
LEFT_EYE_BOTTOM = 145
RIGHT_EYE_TOP = 386
RIGHT_EYE_BOTTOM = 374

MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),          # nose tip
    (0.0, -63.6, -12.5),      # chin
    (-43.3, 32.7, -26.0),     # left eye outer
    (43.3, 32.7, -26.0),      # right eye outer
    (-28.9, -28.9, -24.1),    # left mouth
    (28.9, -28.9, -24.1),     # right mouth
], dtype=np.float64)


def rotation_matrix_to_euler_angles(R):
    sy = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6

    if not singular:
        x = np.arctan2(R[2, 1], R[2, 2])
        y = np.arctan2(-R[2, 0], sy)
        z = np.arctan2(R[1, 0], R[0, 0])
    else:
        x = np.arctan2(-R[1, 2], R[1, 1])
        y = np.arctan2(-R[2, 0], sy)
        z = 0

    return np.degrees([x, y, z])  # pitch, yaw, roll


def get_head_pose(frame, face_landmarks):
    h, w, _ = frame.shape

    image_points = []
    for key in ["nose_tip", "chin", "left_eye_outer", "right_eye_outer", "left_mouth", "right_mouth"]:
        lm = face_landmarks.landmark[LANDMARK_IDS[key]]
        x, y = int(lm.x * w), int(lm.y * h)
        image_points.append((x, y))

    image_points = np.array(image_points, dtype=np.float64)

    focal_length = w
    center = (w / 2, h / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float64)

    dist_coeffs = np.zeros((4, 1))

    success, rotation_vector, translation_vector = cv2.solvePnP(
        MODEL_POINTS,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )

    if not success:
        return None

    rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
    pitch, yaw, roll = rotation_matrix_to_euler_angles(rotation_matrix)

    # MODEL_POINTS uses Y-up/Z-toward-camera convention; OpenCV uses Y-down/Z-into-scene.
    # solvePnP returns Rx(180°) for forward-facing, giving raw pitch≈±180°.
    # Subtract 180° to recentre pitch at 0° for neutral forward-facing pose.
    pitch -= 180.0
    if pitch <= -180.0:
        pitch += 360.0
    elif pitch > 180.0:  # defensive; unreachable given arctan2 output range (-180°, 180°]
        pitch -= 360.0

    return {
        "pitch": float(pitch),
        "yaw": float(yaw),
        "roll": float(roll),
    }


def get_eye_gaze(face_landmarks, frame_width, frame_height):
    """Compute iris position ratios (IPR) for gaze estimation."""
    def eye_ratio(iris_idx, outer_idx, inner_idx, top_idx, bottom_idx):
        iris = face_landmarks.landmark[iris_idx]
        outer = face_landmarks.landmark[outer_idx]
        inner = face_landmarks.landmark[inner_idx]
        top = face_landmarks.landmark[top_idx]
        bottom = face_landmarks.landmark[bottom_idx]

        ix, iy = iris.x * frame_width, iris.y * frame_height
        ox, oy = outer.x * frame_width, outer.y * frame_height
        inx, iny = inner.x * frame_width, inner.y * frame_height
        tx, ty = top.x * frame_width, top.y * frame_height
        bx, by = bottom.x * frame_width, bottom.y * frame_height

        eye_w = abs(inx - ox)
        eye_h = abs(by - ty)
        if eye_w < 5.0 or eye_h < 3.0:
            return None, None

        h_ratio = (ix - ox) / (inx - ox) if abs(inx - ox) > 1e-6 else 0.5
        v_ratio = (iy - ty) / (by - ty) if abs(by - ty) > 1e-6 else 0.5

        h_ratio = max(0.0, min(1.0, h_ratio))
        v_ratio = max(0.0, min(1.0, v_ratio))
        return h_ratio, v_ratio

    left_h, left_v = eye_ratio(LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER,
                               LEFT_EYE_TOP, LEFT_EYE_BOTTOM)
    right_h, right_v = eye_ratio(RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER,
                                 RIGHT_EYE_TOP, RIGHT_EYE_BOTTOM)

    # Flip right eye horizontal ratio (opposite polarity due to mirrored eye corners)
    if right_h is not None:
        right_h = 1.0 - right_h

    if left_h is not None and right_h is not None:
        gaze_yaw = (left_h + right_h) / 2.0
        gaze_pitch = (left_v + right_v) / 2.0
    elif left_h is not None:
        gaze_yaw, gaze_pitch = left_h, left_v
    elif right_h is not None:
        gaze_yaw, gaze_pitch = right_h, right_v
    else:
        return None

    return {"gaze_yaw_ratio": gaze_yaw, "gaze_pitch_ratio": gaze_pitch}


def analyze_frame(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb)

    if not results.multi_face_landmarks:
        return {
            "face_present": False,
            "yaw": None,
            "pitch": None,
            "gaze": None,
            "reason": "face_missing"
        }

    face_landmarks = results.multi_face_landmarks[0]
    h, w, _ = frame.shape

    # Compute gaze independently from pose
    gaze = None
    if ENABLE_EYE_GAZE:
        gaze = get_eye_gaze(face_landmarks, w, h)

    pose = get_head_pose(frame, face_landmarks)

    if pose is None:
        return {
            "face_present": True,
            "yaw": None,
            "pitch": None,
            "gaze": gaze,
            "reason": "pose_estimation_failed"
        }

    return {
        "face_present": True,
        "yaw": pose["yaw"],
        "pitch": pose["pitch"],
        "gaze": gaze,
        "reason": "pose_ok"
    }


def smooth_angle(previous_value, current_value, alpha):
    if current_value is None:
        return previous_value
    if previous_value is None:
        return current_value
    return previous_value + alpha * (current_value - previous_value)


async def calibrate_reference_pose(cap):
    print("开始校准：请自然看着屏幕，保持约 3 秒")

    yaw_samples = []
    pitch_samples = []
    gaze_yaw_samples = []
    gaze_pitch_samples = []
    start_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            await asyncio.sleep(0.05)
            continue

        now = time.time()
        elapsed = now - start_time
        remaining = max(0.0, CALIBRATION_DURATION - elapsed)

        result = analyze_frame(frame)

        if result["face_present"] and result["yaw"] is not None and result["pitch"] is not None:
            yaw_samples.append(result["yaw"])
            pitch_samples.append(result["pitch"])
            status = "Detecting face, sampling..."
            color = (0, 255, 0)
        else:
            status = "Please face the screen naturally"
            color = (0, 0, 255)

        if ENABLE_EYE_GAZE and result["gaze"] is not None:
            gaze_yaw_samples.append(result["gaze"]["gaze_yaw_ratio"])
            gaze_pitch_samples.append(result["gaze"]["gaze_pitch_ratio"])

        cv2.putText(
            frame,
            "Calibration: look at the screen naturally",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 0),
            2
        )
        cv2.putText(
            frame,
            f"{status} | remaining={remaining:.1f}s",
            (20, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2
        )

        cv2.imshow("Participant Webcam Client", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            return None

        if elapsed >= CALIBRATION_DURATION:
            break

        await asyncio.sleep(0.01)

    if len(yaw_samples) < 10 or len(pitch_samples) < 10:
        print("校准失败：有效采样太少")
        return None

    reference_yaw = float(np.mean(yaw_samples))
    reference_pitch = float(np.mean(pitch_samples))

    # Compute reference gaze using median (more robust than mean for ratios)
    if ENABLE_EYE_GAZE and len(gaze_yaw_samples) >= 10:
        reference_gaze_yaw = float(np.median(gaze_yaw_samples))
        reference_gaze_pitch = float(np.median(gaze_pitch_samples))
    else:
        reference_gaze_yaw = 0.5
        reference_gaze_pitch = 0.5

    print("=== 校准完成：已建立屏幕朝向参考 ===")
    print(f"reference_yaw={reference_yaw:.2f}, reference_pitch={reference_pitch:.2f}")
    if ENABLE_EYE_GAZE:
        print(f"reference_gaze_yaw={reference_gaze_yaw:.4f}, reference_gaze_pitch={reference_gaze_pitch:.4f}")

    # 在窗口上短暂显示校准完成
    display_start = time.time()
    while time.time() - display_start < 1.5:
        ret, frame = cap.read()
        if not ret:
            continue

        cv2.putText(
            frame,
            "Calibration complete",
            (50, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            3
        )
        cv2.putText(
            frame,
            "You can start reading",
            (50, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )
        cv2.imshow("Participant Webcam Client", frame)
        cv2.waitKey(1)

    return {
        "reference_yaw": reference_yaw,
        "reference_pitch": reference_pitch,
        "reference_gaze_yaw": reference_gaze_yaw,
        "reference_gaze_pitch": reference_gaze_pitch,
    }


async def run_client():
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("无法打开摄像头")
        return

    try:
        calibration = await calibrate_reference_pose(cap)
        if calibration is None:
            print("程序结束：未完成校准")
            return

        reference_yaw = calibration["reference_yaw"]
        reference_pitch = calibration["reference_pitch"]
        reference_gaze_yaw = calibration["reference_gaze_yaw"]
        reference_gaze_pitch = calibration["reference_gaze_pitch"]

        while True:
            print(f"正在连接 server: {WS_URL}")

            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    ping_timeout=60,
                ) as websocket:
                    print("已连接到 server")

                    calibration_message = {
                        "source": "webcam",
                        "type": EVENT_CALIBRATION_COMPLETE,
                        "reference_yaw": round(reference_yaw, 2),
                        "reference_pitch": round(reference_pitch, 2),
                        "enable_eye_gaze": ENABLE_EYE_GAZE,
                        "reference_gaze_yaw": round(reference_gaze_yaw, 4) if ENABLE_EYE_GAZE else None,
                        "reference_gaze_pitch": round(reference_gaze_pitch, 4) if ENABLE_EYE_GAZE else None,
                        "timestamp": time.time()
                    }
                    await websocket.send(json.dumps(calibration_message))
                    print("已发送:", calibration_message)

                    disengaged = False
                    away_start_time = None
                    away_break_start_time = None
                    reengage_start_time = None
                    reengage_break_start_time = None
                    detection_armed = False
                    valid_face_streak = 0
                    smoothed_yaw = None
                    smoothed_pitch = None
                    face_missing_start_time = None
                    invalid_pose_start_time = None
                    last_stable_screen_facing = None
                    smoothed_gaze_yaw = None
                    smoothed_gaze_pitch = None
                    gaze_mw_start_time = None
                    gaze_mw_break_start_time = None
                    gaze_mind_wandering = False

                    while True:
                        ret, frame = cap.read()
                        if not ret:
                            print("读取摄像头画面失败")
                            await asyncio.sleep(0.1)
                            continue

                        now = time.time()
                        result = analyze_frame(frame)

                        raw_face_present = result["face_present"]
                        yaw = result["yaw"]
                        pitch = result["pitch"]
                        gaze = result["gaze"]
                        has_valid_pose = raw_face_present and yaw is not None and pitch is not None

                        if raw_face_present:
                            face_missing_start_time = None
                        elif face_missing_start_time is None:
                            face_missing_start_time = now

                        if raw_face_present and yaw is None:
                            if invalid_pose_start_time is None:
                                invalid_pose_start_time = now
                        else:
                            invalid_pose_start_time = None

                        face_missing_duration = 0.0 if face_missing_start_time is None else (now - face_missing_start_time)
                        invalid_pose_duration = 0.0 if invalid_pose_start_time is None else (now - invalid_pose_start_time)

                        if has_valid_pose:
                            smoothed_yaw = smooth_angle(smoothed_yaw, yaw, POSE_SMOOTHING_ALPHA)
                            smoothed_pitch = smooth_angle(smoothed_pitch, pitch, POSE_SMOOTHING_ALPHA)
                        else:
                            smoothed_yaw = None
                            smoothed_pitch = None

                        effective_yaw = smoothed_yaw if smoothed_yaw is not None else yaw
                        effective_pitch = smoothed_pitch if smoothed_pitch is not None else pitch

                        # ─── Eye Gaze Smoothing ───
                        if ENABLE_EYE_GAZE and gaze is not None:
                            smoothed_gaze_yaw = smooth_angle(smoothed_gaze_yaw, gaze["gaze_yaw_ratio"], GAZE_SMOOTHING_ALPHA)
                            smoothed_gaze_pitch = smooth_angle(smoothed_gaze_pitch, gaze["gaze_pitch_ratio"], GAZE_SMOOTHING_ALPHA)
                        else:
                            smoothed_gaze_yaw = None
                            smoothed_gaze_pitch = None

                        if not raw_face_present:
                            screen_facing = False
                            yaw_deviation = None
                            pitch_deviation = None
                            yaw_threshold = YAW_DEVIATION_THRESHOLD
                            pitch_threshold = PITCH_DEVIATION_THRESHOLD
                            if detection_armed and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
                                reason = "face_missing_pending"
                            else:
                                reason = "face_missing"
                        elif yaw is None or pitch is None:
                            screen_facing = False
                            yaw_deviation = None
                            pitch_deviation = None
                            yaw_threshold = YAW_DEVIATION_THRESHOLD
                            pitch_threshold = PITCH_DEVIATION_THRESHOLD
                            if detection_armed and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
                                reason = "pose_estimation_pending"
                            else:
                                reason = "pose_estimation_failed"
                        else:
                            yaw_deviation = abs(effective_yaw - reference_yaw)
                            pitch_deviation = abs(effective_pitch - reference_pitch)

                            yaw_threshold = (
                                REENGAGE_YAW_DEVIATION_THRESHOLD
                                if disengaged else
                                YAW_DEVIATION_THRESHOLD
                            )
                            pitch_threshold = (
                                REENGAGE_PITCH_DEVIATION_THRESHOLD
                                if disengaged else
                                PITCH_DEVIATION_THRESHOLD
                            )

                            if disengaged:
                                screen_facing = (
                                    yaw_deviation <= yaw_threshold and
                                    pitch_deviation <= pitch_threshold
                                )
                            else:
                                outside_yaw = yaw_deviation > (yaw_threshold + YAW_HYSTERESIS_MARGIN)
                                outside_pitch = pitch_deviation > (pitch_threshold + PITCH_HYSTERESIS_MARGIN)

                                if yaw_deviation <= yaw_threshold and pitch_deviation <= pitch_threshold:
                                    screen_facing = True
                                elif outside_yaw or outside_pitch:
                                    screen_facing = False
                                else:
                                    screen_facing = True

                            reason = "screen_facing" if screen_facing else "looking_away"
                            last_stable_screen_facing = screen_facing

                        face_present = raw_face_present
                        reported_screen_facing = screen_facing

                        if detection_armed and (not raw_face_present) and face_missing_duration < FACE_MISSING_GRACE_PERIOD:
                            face_present = True
                            if last_stable_screen_facing is not None:
                                reported_screen_facing = last_stable_screen_facing
                        elif detection_armed and raw_face_present and (yaw is None or pitch is None) and invalid_pose_duration < POSE_INVALID_GRACE_PERIOD:
                            if last_stable_screen_facing is not None:
                                reported_screen_facing = last_stable_screen_facing

                        face_missing_confirmed = (
                            detection_armed and
                            (not raw_face_present) and
                            face_missing_duration >= FACE_MISSING_GRACE_PERIOD
                        )
                        invalid_pose_confirmed = (
                            detection_armed and
                            raw_face_present and
                            (yaw is None or pitch is None) and
                            invalid_pose_duration >= POSE_INVALID_GRACE_PERIOD
                        )
                        looking_away_confirmed = raw_face_present and yaw is not None and pitch is not None and (not screen_facing)

                        is_away = face_missing_confirmed or invalid_pose_confirmed or looking_away_confirmed
                        state_changed = False

                        # Compute current-frame gaze deviation before recovery logic uses it.
                        gaze_yaw_dev = None
                        gaze_pitch_dev = None
                        gaze_looking_away = False

                        if ENABLE_EYE_GAZE and smoothed_gaze_yaw is not None:
                            gaze_yaw_dev = abs(smoothed_gaze_yaw - reference_gaze_yaw)
                            gaze_pitch_dev = abs(smoothed_gaze_pitch - reference_gaze_pitch)

                            # Parallax guard: only evaluate gaze when head is roughly forward
                            if yaw_deviation is not None and yaw_deviation < GAZE_HEAD_YAW_LIMIT:
                                if gaze_mind_wandering:
                                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD - GAZE_YAW_HYSTERESIS
                                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD - GAZE_PITCH_HYSTERESIS
                                else:
                                    gy_thresh = GAZE_YAW_DEVIATION_THRESHOLD + GAZE_YAW_HYSTERESIS
                                    gp_thresh = GAZE_PITCH_DEVIATION_THRESHOLD + GAZE_PITCH_HYSTERESIS
                                gaze_looking_away = gaze_yaw_dev > gy_thresh or gaze_pitch_dev > gp_thresh

                        if not detection_armed:
                            if has_valid_pose:
                                valid_face_streak += 1
                            else:
                                valid_face_streak = 0

                            away_start_time = None
                            away_break_start_time = None
                            reengage_start_time = None
                            reengage_break_start_time = None
                            away_duration = 0.0
                            reengage_duration = 0.0

                            if valid_face_streak >= MIN_VALID_FACE_FRAMES:
                                detection_armed = True
                        elif is_away:
                            if away_start_time is None:
                                away_start_time = now
                            away_break_start_time = None
                            away_duration = now - away_start_time

                            if reengage_start_time is not None:
                                if reengage_break_start_time is None:
                                    reengage_break_start_time = now
                                elif now - reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                                    reengage_start_time = None
                                    reengage_duration = 0.0
                            else:
                                reengage_duration = 0.0

                            if (not disengaged) and away_duration >= DISENGAGE_THRESHOLD:
                                disengaged = True
                                state_changed = True
                                if ENABLE_EYE_GAZE:
                                    gaze_mind_wandering = True
                        else:
                            reengage_break_start_time = None

                            if away_start_time is not None:
                                if away_break_start_time is None:
                                    away_break_start_time = now
                                elif now - away_break_start_time >= AWAY_BREAK_TOLERANCE:
                                    away_start_time = None
                                    away_duration = 0.0
                            else:
                                away_duration = 0.0

                            if reengage_start_time is None:
                                reengage_start_time = now
                            reengage_duration = now - reengage_start_time

                            if disengaged and reengage_duration >= REENGAGE_THRESHOLD and not (ENABLE_EYE_GAZE and gaze_looking_away):
                                disengaged = False
                                state_changed = True
                                gaze_mind_wandering = False

                        # Gaze mind wandering timer (independent from away_duration)
                        if ENABLE_EYE_GAZE and detection_armed and (not disengaged or gaze_mind_wandering):
                            if gaze_looking_away:
                                if gaze_mw_start_time is None:
                                    gaze_mw_start_time = now
                                gaze_mw_break_start_time = None
                                if (now - gaze_mw_start_time) >= GAZE_MIND_WANDERING_DURATION:
                                    if not gaze_mind_wandering:
                                        state_changed = True
                                        reason = "gaze_mind_wandering"
                                    gaze_mind_wandering = True
                                    disengaged = True
                            else:
                                # Gaze back to normal - apply break tolerance
                                if gaze_mw_start_time is not None:
                                    if gaze_mw_break_start_time is None:
                                        gaze_mw_break_start_time = now
                                    elif now - gaze_mw_break_start_time >= GAZE_MW_BREAK_TOLERANCE:
                                        gaze_mw_start_time = None
                                        gaze_mw_break_start_time = None
                                        gaze_mind_wandering = False
                        else:
                            # Reset gaze tracking when not armed or (disengaged by head pose, not gaze)
                            if not detection_armed or (disengaged and not gaze_mind_wandering):
                                gaze_mw_start_time = None
                                gaze_mw_break_start_time = None
                                gaze_mind_wandering = False

                        gaze_mw_duration = (now - gaze_mw_start_time) if gaze_mw_start_time is not None else 0.0

                        line1 = f"face={face_present}"
                        if yaw is not None:
                            line1 += f" | yaw={yaw:.1f}"
                        else:
                            line1 += " | yaw=None"

                        if pitch is not None:
                            line1 += f" | pitch={pitch:.1f}"
                        else:
                            line1 += " | pitch=None"

                        if effective_yaw is not None and effective_pitch is not None:
                            line1 += f" | smooth=({effective_yaw:.1f},{effective_pitch:.1f})"

                        line2 = f"ref_yaw={reference_yaw:.1f} | ref_pitch={reference_pitch:.1f}"

                        line3 = "yaw_dev=None"
                        if yaw_deviation is not None:
                            line3 = f"yaw_dev={yaw_deviation:.1f}"

                        if pitch_deviation is not None:
                            line3 += f" | pitch_dev={pitch_deviation:.1f}"
                        else:
                            line3 += " | pitch_dev=None"

                        if yaw is not None and pitch is not None:
                            line3 += f" | th=({yaw_threshold:.1f},{pitch_threshold:.1f})"

                        line4 = (
                            f"reason={reason} | facing={reported_screen_facing} | away={away_duration:.1f}s"
                            f" | back={reengage_duration:.1f}s | disengaged={disengaged}"
                        )

                        if not detection_armed:
                            line4 += f" | arming={valid_face_streak}/{MIN_VALID_FACE_FRAMES}"

                        color = (0, 255, 0) if not disengaged else (0, 0, 255)

                        cv2.putText(frame, line1, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                        cv2.putText(frame, line2, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
                        cv2.putText(frame, line3, (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
                        cv2.putText(frame, line4, (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

                        if ENABLE_EYE_GAZE:
                            if smoothed_gaze_yaw is not None and smoothed_gaze_pitch is not None:
                                gaze_dev_str = ""
                                if gaze_yaw_dev is not None:
                                    gaze_dev_str = f" | dev=({gaze_yaw_dev:.2f},{gaze_pitch_dev:.2f})"
                                line5 = f"gaze=({smoothed_gaze_yaw:.2f},{smoothed_gaze_pitch:.2f}){gaze_dev_str} | mw={gaze_mw_duration:.1f}s"
                                gaze_color = (0, 165, 255) if gaze_looking_away else (0, 255, 0)
                                cv2.putText(frame, line5, (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.55, gaze_color, 2)
                            else:
                                cv2.putText(frame, "gaze=N/A", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (128, 128, 128), 2)

                        cv2.imshow("Participant Webcam Client", frame)

                        if state_changed:
                            message = {
                                "client": "webcam",
                                "source": "webcam",
                                "type": EVENT_POSTURE,
                                "disengage": disengaged,
                                "face_present": face_present,
                                "screen_facing": reported_screen_facing,
                                "reason": reason,
                                "yaw": None if yaw is None else round(yaw, 2),
                                "pitch": None if pitch is None else round(pitch, 2),
                                "reference_yaw": round(reference_yaw, 2),
                                "reference_pitch": round(reference_pitch, 2),
                                "yaw_deviation": None if yaw_deviation is None else round(yaw_deviation, 2),
                                "pitch_deviation": None if pitch_deviation is None else round(pitch_deviation, 2),
                                "away_duration": round(away_duration, 2),
                                "reengage_duration": round(reengage_duration, 2),
                                "gaze_yaw_ratio": round(smoothed_gaze_yaw, 4) if smoothed_gaze_yaw is not None else None,
                                "gaze_pitch_ratio": round(smoothed_gaze_pitch, 4) if smoothed_gaze_pitch is not None else None,
                                "gaze_yaw_deviation": round(gaze_yaw_dev, 4) if gaze_yaw_dev is not None else None,
                                "gaze_pitch_deviation": round(gaze_pitch_dev, 4) if gaze_pitch_dev is not None else None,
                                "gaze_mind_wandering": gaze_mind_wandering if ENABLE_EYE_GAZE else None,
                                "timestamp": now
                            }

                            await websocket.send(json.dumps(message))
                            print("已发送:", message)

                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q"):
                            print("用户主动退出")
                            return

                        await asyncio.sleep(0.01)
            except Exception as e:
                print("连接中断:", e)
                print(f"{RECONNECT_DELAY:.1f} 秒后自动重连...")
                await asyncio.sleep(RECONNECT_DELAY)

    except Exception as e:
        print("运行出错:", e)

    finally:
        cap.release()
        cv2.destroyAllWindows()
        face_mesh.close()


if __name__ == "__main__":
    asyncio.run(run_client())