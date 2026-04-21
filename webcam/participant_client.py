import asyncio
import json
import os
import time
from datetime import datetime, timezone
import cv2
import numpy as np
import mediapipe as mp
import websockets

try:
    from .posture_logic import compute_full_recovery, compute_reengage_threshold, compute_screen_facing
except ImportError:
    from posture_logic import compute_full_recovery, compute_reengage_threshold, compute_screen_facing

SERVER_IP = "10.5.15.160"   # 改成你的电脑IP
SERVER_PORT = 8765
WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}"
RECONNECT_DELAY = 3.0

# ── Video recording ───────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.normpath(os.path.join(_dir, "..", "recordings"))
REC_NOMINAL_FPS = 15

_rec_active = False
_rec_writer: cv2.VideoWriter | None = None
_rec_start_ts: str = ""
_rec_frame_count: int = 0
_rec_filename: str = ""
_rec_username: str = ""
_rec_resolution: tuple = (0, 0)
_rec_pending_start: dict | None = None  # set to {"username": ...} to start on next frame
_rec_pending_stop: bool = False


def _do_start_recording(username: str, frame: np.ndarray) -> None:
    global _rec_writer, _rec_active, _rec_start_ts, _rec_frame_count
    global _rec_filename, _rec_username, _rec_resolution

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()
    _rec_start_ts = now.isoformat()
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    _rec_filename = f"recording_{ts_str}_{username}.avi"
    _rec_username = username
    _rec_frame_count = 0

    h, w = frame.shape[:2]
    _rec_resolution = (w, h)
    path = os.path.join(RECORDINGS_DIR, _rec_filename)
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    _rec_writer = cv2.VideoWriter(path, fourcc, REC_NOMINAL_FPS, (w, h))
    if _rec_writer.isOpened():
        _rec_active = True
        print(f"[Recording] Started: {_rec_filename} ({w}x{h} @ {REC_NOMINAL_FPS}fps)")
    else:
        print(f"[Recording] ERROR: failed to open VideoWriter for {path}")
        _rec_writer = None


def _do_stop_recording() -> None:
    global _rec_writer, _rec_active
    if _rec_writer is None:
        _rec_active = False
        return
    _rec_writer.release()
    _rec_writer = None
    _rec_active = False

    end_ts = datetime.now(timezone.utc).astimezone().isoformat()
    meta = {
        "video_file": _rec_filename,
        "username": _rec_username,
        "video_start_ts": _rec_start_ts,
        "video_end_ts": end_ts,
        "total_frames": _rec_frame_count,
        "nominal_fps": REC_NOMINAL_FPS,
        "resolution": list(_rec_resolution),
    }
    meta_path = os.path.join(RECORDINGS_DIR, _rec_filename.replace(".avi", ".json"))
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"[Recording] Stopped: {_rec_filename} ({_rec_frame_count} frames)")
    print(f"[Recording] Metadata: {meta_path}")


def _handle_recording_frame(frame: np.ndarray) -> None:
    """Call every captured frame to handle pending start/stop and write."""
    global _rec_pending_start, _rec_pending_stop, _rec_frame_count

    start_cmd = _rec_pending_start
    stop_cmd = _rec_pending_stop
    _rec_pending_start = None
    _rec_pending_stop = False

    if stop_cmd and _rec_active:
        _do_stop_recording()

    if start_cmd is not None and not _rec_active:
        _do_start_recording(start_cmd["username"], frame)

    if _rec_active and _rec_writer is not None:
        _rec_writer.write(frame)
        _rec_frame_count += 1

DISENGAGE_THRESHOLD = 2.0     # 更严格：偏离持续超过2.0秒 -> disengaged
REENGAGE_THRESHOLD = 1.0  # 恢复朝向屏幕后持续1.5秒 -> re-engaged
GAZE_REENGAGE_THRESHOLD = REENGAGE_THRESHOLD  # 与普通恢复保持一致，差异只留在恢复判据而不是等待时长

EVENT_CALIBRATION_COMPLETE = "calibration_complete"
EVENT_POSTURE = "posture"

CALIBRATION_DURATION = 3.0     # 启动后前3秒自动校准
YAW_DEVIATION_THRESHOLD = 32.0
PITCH_DEVIATION_THRESHOLD = 14.0
REENGAGE_YAW_DEVIATION_THRESHOLD = 42.0
REENGAGE_PITCH_DEVIATION_THRESHOLD = 24.0
YAW_HYSTERESIS_MARGIN = 8.0
PITCH_HYSTERESIS_MARGIN = 5.0
POSE_SMOOTHING_ALPHA = 0.2
REENGAGE_BREAK_TOLERANCE = 0.35
AWAY_BREAK_TOLERANCE = 0.4
MIN_VALID_FACE_FRAMES = 10     # 连续有效人脸/姿态帧达到后才开始判定
FACE_MISSING_GRACE_PERIOD = 0.5
POSE_INVALID_GRACE_PERIOD = 0.5

# ─── Eye Gaze Augmentation ───
ENABLE_EYE_GAZE = True                        # Set to False to disable gaze augmentation entirely
GAZE_SMOOTHING_ALPHA = 0.15                    # EMA alpha for gaze (lower than head pose for noise)
GAZE_YAW_DEVIATION_THRESHOLD = 0.07           # 更严格：更小偏移就判为横向分心
GAZE_PITCH_DEVIATION_THRESHOLD = 0.04         # 更严格：更小偏移就判为纵向分心
GAZE_YAW_HYSTERESIS = 0.025                   # Slightly tighter horizontal hysteresis
GAZE_PITCH_HYSTERESIS = 0.015                 # Slightly tighter vertical hysteresis
GAZE_MIND_WANDERING_DURATION = 1.5        # 持续1.5秒眼动偏离就触发
GAZE_MW_BREAK_TOLERANCE = 0.6                 # 更严格：短暂回正不轻易清空 MW 计时器
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

    return {
        "reference_yaw": reference_yaw,
        "reference_pitch": reference_pitch,
        "reference_gaze_yaw": reference_gaze_yaw,
        "reference_gaze_pitch": reference_gaze_pitch,
    }


async def run_client():
    cap = None
    camera_active = asyncio.Event()
    stop_camera_event = asyncio.Event()
    shutdown_event = asyncio.Event()

    async def _recv_commands(websocket):
        """Listen for remote commands from server (e.g. start_camera, stop_camera)."""
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                cmd_type = msg.get("type")
                if cmd_type == "start_camera":
                    print("[远程] 收到 start_camera 指令")
                    stop_camera_event.clear()
                    camera_active.set()
                elif cmd_type == "stop_camera":
                    print("[远程] 收到 stop_camera 指令")
                    camera_active.clear()
                    stop_camera_event.set()
                elif cmd_type == "shutdown":
                    print("[远程] 收到 shutdown 指令")
                    shutdown_event.set()
                    camera_active.set()  # unblock wait
                elif cmd_type == "recording_start":
                    username = msg.get("username", "participant")
                    global _rec_pending_start
                    _rec_pending_start = {"username": username}
                    print(f"[远程] 收到 recording_start 指令 (username={username})")
                elif cmd_type == "recording_stop":
                    global _rec_pending_stop
                    _rec_pending_stop = True
                    print("[远程] 收到 recording_stop 指令")
        except websockets.ConnectionClosed:
            pass

    try:
        while not shutdown_event.is_set():
            print(f"正在连接 server: {WS_URL}")

            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    ping_timeout=60,
                ) as websocket:
                    print("已连接到 server")

                    # Register as webcam client
                    await websocket.send(json.dumps({"client": "webcam"}))

                    # Start command listener
                    recv_task = asyncio.create_task(_recv_commands(websocket))

                    try:
                        while not shutdown_event.is_set():
                            # ── Wait for start_camera command ──
                            print("等待研究者发送 start_camera 指令...")
                            await camera_active.wait()
                            if shutdown_event.is_set():
                                break

                            # ── Open camera & calibrate ──
                            print("正在打开摄像头...")
                            cap = cv2.VideoCapture(0)
                            if not cap.isOpened():
                                print("无法打开摄像头，等待重试...")
                                camera_active.clear()
                                continue

                            calibration = await calibrate_reference_pose(cap)
                            if calibration is None:
                                print("校准失败，释放摄像头，等待下次指令...")
                                cap.release()
                                cap = None
                                camera_active.clear()
                                continue

                            reference_yaw = calibration["reference_yaw"]
                            reference_pitch = calibration["reference_pitch"]
                            reference_gaze_yaw = calibration["reference_gaze_yaw"]
                            reference_gaze_pitch = calibration["reference_gaze_pitch"]

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
                            print("已发送校准消息:", calibration_message)

                            disengaged = False
                            away_start_time = None
                            away_break_start_time = None
                            reengage_start_time = None
                            reengage_break_start_time = None
                            detection_armed = False
                            valid_face_streak = 0
                            disengage_reason_latched = None
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

                            # ── Detection loop ──
                            while camera_active.is_set() and not shutdown_event.is_set():
                                ret, frame = cap.read()
                                if not ret:
                                    print("读取摄像头画面失败")
                                    await asyncio.sleep(0.1)
                                    continue

                                _handle_recording_frame(frame)

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
        
                                    screen_facing, yaw_threshold, pitch_threshold = compute_screen_facing(
                                        yaw_deviation=yaw_deviation,
                                        pitch_deviation=pitch_deviation,
                                        disengaged=disengaged,
                                        yaw_threshold=YAW_DEVIATION_THRESHOLD,
                                        pitch_threshold=PITCH_DEVIATION_THRESHOLD,
                                        reengage_yaw_threshold=REENGAGE_YAW_DEVIATION_THRESHOLD,
                                        reengage_pitch_threshold=REENGAGE_PITCH_DEVIATION_THRESHOLD,
                                        yaw_hysteresis_margin=YAW_HYSTERESIS_MARGIN,
                                        pitch_hysteresis_margin=PITCH_HYSTERESIS_MARGIN,
                                    )
        
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
                                state_changed = False
                                active_error_reason = None
                                reengage_threshold_s = REENGAGE_THRESHOLD
        
                                # Compute current-frame gaze deviation before recovery logic uses it.
                                gaze_yaw_dev = None
                                gaze_pitch_dev = None
                                gaze_looking_away = False
                                gaze_error_confirmed = False
        
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
                                else:
                                    if ENABLE_EYE_GAZE:
                                        if gaze_looking_away:
                                            if gaze_mw_start_time is None:
                                                gaze_mw_start_time = now
                                            gaze_mw_break_start_time = None
                                        elif gaze_mw_start_time is not None:
                                            if gaze_mw_break_start_time is None:
                                                gaze_mw_break_start_time = now
                                            elif now - gaze_mw_break_start_time >= GAZE_MW_BREAK_TOLERANCE:
                                                gaze_mw_start_time = None
                                                gaze_mw_break_start_time = None
        
                                        gaze_error_confirmed = (
                                            gaze_mw_start_time is not None and
                                            (now - gaze_mw_start_time) >= GAZE_MIND_WANDERING_DURATION
                                        )
                                    else:
                                        gaze_mw_start_time = None
                                        gaze_mw_break_start_time = None
        
                                    gaze_mind_wandering = gaze_error_confirmed
        
                                    active_error_reason = None
                                    if face_missing_confirmed:
                                        active_error_reason = "face_missing"
                                    elif invalid_pose_confirmed:
                                        active_error_reason = "pose_estimation_failed"
                                    elif looking_away_confirmed:
                                        active_error_reason = "looking_away"
                                    elif gaze_error_confirmed:
                                        active_error_reason = "gaze_mind_wandering"
        
                                    fully_recovered = compute_full_recovery(
                                        enable_eye_gaze=ENABLE_EYE_GAZE,
                                        disengage_reason=disengage_reason_latched,
                                        raw_face_present=raw_face_present,
                                        yaw=yaw,
                                        pitch=pitch,
                                        screen_facing=screen_facing,
                                        smoothed_gaze_yaw=smoothed_gaze_yaw,
                                        smoothed_gaze_pitch=smoothed_gaze_pitch,
                                        gaze_looking_away=gaze_looking_away,
                                        gaze_error_confirmed=gaze_error_confirmed,
                                    )
        
                                    reengage_threshold_s = compute_reengage_threshold(
                                        disengage_reason=disengage_reason_latched,
                                        default_threshold_s=REENGAGE_THRESHOLD,
                                        gaze_threshold_s=GAZE_REENGAGE_THRESHOLD,
                                    )
        
                                    if active_error_reason is not None:
                                        reason = disengage_reason_latched or active_error_reason
        
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
        
                                        if not disengaged:
                                            # gaze_mind_wandering已通过GAZE_MIND_WANDERING_DURATION积累了2秒
                                            # 头姿/人脸类误差需要持续 DISENGAGE_THRESHOLD 才触发 start
                                            if active_error_reason == "gaze_mind_wandering" or away_duration >= DISENGAGE_THRESHOLD:
                                                disengaged = True
                                                disengage_reason_latched = active_error_reason
                                                reason = disengage_reason_latched
                                                state_changed = True
                                    elif fully_recovered:
                                        reason = "gaze_recovered" if ENABLE_EYE_GAZE else "screen_facing"
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
        
                                        if disengaged and reengage_duration >= reengage_threshold_s:
                                            disengaged = False
                                            disengage_reason_latched = None
                                            state_changed = True
                                    else:
                                        reason = disengage_reason_latched or "waiting_full_recovery"
                                        away_break_start_time = None
                                        away_duration = 0.0 if away_start_time is None else (now - away_start_time)
        
                                        if disengaged and reengage_start_time is not None:
                                            if reengage_break_start_time is None:
                                                reengage_break_start_time = now
                                            elif now - reengage_break_start_time >= REENGAGE_BREAK_TOLERANCE:
                                                reengage_start_time = None
                                                reengage_duration = 0.0
                                        else:
                                            reengage_duration = 0.0
        
                                gaze_mw_duration = (now - gaze_mw_start_time) if gaze_mw_start_time is not None else 0.0

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
        
                                await asyncio.sleep(0.01)

                            # ── Detection loop ended (stop_camera or shutdown) ──
                            if _rec_active:
                                _do_stop_recording()
                            print("检测循环结束，释放摄像头")
                            if cap is not None:
                                cap.release()
                                cap = None

                    finally:
                        recv_task.cancel()
                        try:
                            await recv_task
                        except asyncio.CancelledError:
                            pass

            except (websockets.ConnectionClosed, OSError) as e:
                print("连接中断:", e)
                print(f"{RECONNECT_DELAY:.1f} 秒后自动重连...")
                if cap is not None:
                    cap.release()
                    cap = None
                camera_active.clear()
                await asyncio.sleep(RECONNECT_DELAY)

    except Exception as e:
        print("运行出错:", e)

    finally:
        if cap is not None:
            cap.release()
        face_mesh.close()


if __name__ == "__main__":
    asyncio.run(run_client())