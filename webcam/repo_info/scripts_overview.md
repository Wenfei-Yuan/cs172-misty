# Scripts Overview

## File: participant_client.py

### High-Level Summary
A real-time webcam-based participant engagement tracking client that captures video frames, uses MediaPipe Face Mesh for face detection, estimates head pose via the PnP algorithm, **optionally tracks eye gaze via iris position ratios (IPR)** for mind wandering detection, implements a state machine to determine engagement/disengagement status, and transmits engagement events over WebSocket to a backend server. The pipeline follows: frame capture → face detection → head pose estimation + [optional] eye gaze estimation → pose/gaze smoothing → engagement state determination → event transmission.

### Dependencies

**Standard Library:**
- `asyncio` — Asynchronous event loop management for concurrent I/O operations
- `json` — Serialization of WebSocket messages to JSON format
- `time` — Timestamp generation and duration tracking

**Third-Party:**
- `cv2` (OpenCV) — Video capture, frame processing, PnP solver, and on-screen visualization
- `numpy` — Numerical operations, matrix transformations, and array computations
- `mediapipe` — Face Mesh model for detecting 468 facial keypoints in real time
- `websockets` — WebSocket client for sending engagement state events to a remote server

---

### Global Configuration Constants

The module defines configuration constants at the top level controlling:
- **Server connection**: `SERVER_IP` ("10.5.15.160"), `SERVER_PORT` (8765), `RECONNECT_DELAY` (3.0s)
- **Engagement thresholds**: `DISENGAGE_THRESHOLD` (3.0s), `REENGAGE_THRESHOLD` (1.0s)
- **Angle thresholds**: `YAW_DEVIATION_THRESHOLD` (34°), `PITCH_DEVIATION_THRESHOLD` (18°), `REENGAGE_YAW_DEVIATION_THRESHOLD` (50°), `REENGAGE_PITCH_DEVIATION_THRESHOLD` (30°)
- **Hysteresis**: `YAW_HYSTERESIS_MARGIN` (10°), `PITCH_HYSTERESIS_MARGIN` (7°)
- **Smoothing**: `POSE_SMOOTHING_ALPHA` (0.2)
- **Grace periods**: `FACE_MISSING_GRACE_PERIOD` (0.8s), `POSE_INVALID_GRACE_PERIOD` (0.8s)
- **Break tolerances**: `AWAY_BREAK_TOLERANCE` (0.25s), `REENGAGE_BREAK_TOLERANCE` (0.35s)
- **Arming**: `MIN_VALID_FACE_FRAMES` (10), `CALIBRATION_DURATION` (3.0s)
- **Event types**: `EVENT_CALIBRATION_COMPLETE` ("calibration_complete"), `EVENT_POSTURE` ("posture")
- **Eye gaze augmentation**: `ENABLE_EYE_GAZE` (True), `GAZE_SMOOTHING_ALPHA` (0.15), `GAZE_YAW_DEVIATION_THRESHOLD` (0.10), `GAZE_PITCH_DEVIATION_THRESHOLD` (0.12), `GAZE_YAW_HYSTERESIS` (0.05), `GAZE_PITCH_HYSTERESIS` (0.04), `GAZE_MIND_WANDERING_DURATION` (2.0s), `GAZE_MW_BREAK_TOLERANCE` (0.5s), `GAZE_HEAD_YAW_LIMIT` (15.0°)

### Global Data Structures

**`LANDMARK_IDS` (dict)**
- Maps facial landmark names (nose_tip, chin, left_eye_outer, right_eye_outer, left_mouth, right_mouth) to MediaPipe landmark indices (1, 152, 33, 263, 61, 291).
- Enables consistent identification of the 6 key facial points used for PnP head pose estimation.

**Iris/Eye Landmark Constants**
- `LEFT_IRIS_CENTER` (468), `RIGHT_IRIS_CENTER` (473) — iris center landmarks for gaze estimation.
- `LEFT_EYE_INNER` (133), `LEFT_EYE_OUTER` (33), `RIGHT_EYE_INNER` (362), `RIGHT_EYE_OUTER` (263) — eye corner landmarks.
- `LEFT_EYE_TOP` (159), `LEFT_EYE_BOTTOM` (145), `RIGHT_EYE_TOP` (386), `RIGHT_EYE_BOTTOM` (374) — eye lid landmarks.
- Used by `get_eye_gaze()` for iris position ratio computation. Available when `refine_landmarks=True`.

**`MODEL_POINTS` (numpy array, 6×3)**
- Contains 3D model coordinates in millimeters for six facial landmarks on a canonical head model (nose tip at origin, chin, left/right eye outer corners, left/right mouth corners).
- Paired with 2D image coordinates in `cv2.solvePnP` to estimate 3D head rotation.

**`face_mesh` (MediaPipe FaceMesh instance)**
- Configured with `static_image_mode=False`, `max_num_faces=1`, `refine_landmarks=True`, `min_detection_confidence=0.5`, `min_tracking_confidence=0.5`.
- Global singleton used throughout frame processing.

---

### Functions

#### 1. `rotation_matrix_to_euler_angles(R)`
- **Parameters:** `R` (3×3 numpy rotation matrix) | **Returns:** numpy array of [pitch, yaw, roll] in degrees.
- Converts a rotation matrix to Euler angles using arctan2-based decomposition, with special handling for singular (gimbal lock) cases where the determinant is near zero.

#### 2. `get_head_pose(frame, face_landmarks)`
- **Parameters:** `frame` (BGR video frame), `face_landmarks` (MediaPipe face landmarks object) | **Returns:** dict with "pitch", "yaw", "roll" (floats in degrees), or `None` if PnP solver fails.
- Extracts 2D image coordinates of 6 facial landmarks, constructs a camera intrinsics matrix (focal_length = frame width), and calls `cv2.solvePnP(SOLVEPNP_ITERATIVE)` to compute 3D head rotation; converts the rotation matrix to Euler angles. Applies a 180° pitch correction (`pitch -= 180.0` + normalization to (−180°, 180°]) to recentre pitch at 0° for a neutral forward-facing pose, correcting for the Y-up/Z-toward-camera convention of `MODEL_POINTS` vs. OpenCV's Y-down/Z-into-scene camera convention.

#### 3. `get_eye_gaze(face_landmarks, frame_width, frame_height)`
- **Parameters:** `face_landmarks` (MediaPipe face landmarks object), `frame_width` (int), `frame_height` (int) | **Returns:** dict with "gaze_yaw_ratio" and "gaze_pitch_ratio" (floats 0–1, center≈0.5), or `None` if both eyes have degenerate geometry.
- Computes iris position ratios (IPR) for each eye: horizontal ratio = (iris_x - outer_x) / (inner_x - outer_x), vertical ratio = (iris_y - top_y) / (bottom_y - top_y). Right eye horizontal ratio is flipped (1.0 - ratio) to match left eye polarity. Averages both eyes when available, falls back to single eye when one has degenerate geometry (width < 5px or height < 3px). All ratios clamped to [0.0, 1.0].

#### 4. `analyze_frame(frame)`
- **Parameters:** `frame` (BGR video frame) | **Returns:** dict with "face_present" (bool), "yaw"/"pitch" (float or None), "reason" (string: "face_missing", "pose_estimation_failed", or "pose_ok").
- Converts the frame to RGB, processes it with MediaPipe FaceMesh, and calls `get_head_pose` if a face is detected; returns a structured result indicating detection and pose estimation status.

#### 4. `analyze_frame(frame)`
- **Parameters:** `frame` (BGR video frame) | **Returns:** dict with "face_present" (bool), "yaw"/"pitch" (float or None), "gaze" (dict or None), "reason" (string: "face_missing", "pose_estimation_failed", or "pose_ok").
- Converts the frame to RGB, processes it with MediaPipe FaceMesh, calls `get_head_pose` and optionally `get_eye_gaze` (when `ENABLE_EYE_GAZE=True`) if a face is detected; returns a structured result. Gaze is computed independently from head pose — gaze may succeed even when PnP solver fails.

#### 5. `smooth_angle(previous_value, current_value, alpha)`
- **Parameters:** `previous_value` (float or None), `current_value` (float or None), `alpha` (float, smoothing constant 0–1) | **Returns:** smoothed angle (float or None).
- Applies exponential moving average: `previous + alpha * (current - previous)`. Returns `previous` if `current` is None; returns `current` if `previous` is None.

#### 5. `smooth_angle(previous_value, current_value, alpha)`
- **Parameters:** `previous_value` (float or None), `current_value` (float or None), `alpha` (float, smoothing constant 0–1) | **Returns:** smoothed angle (float or None).
- Applies exponential moving average: `previous + alpha * (current - previous)`. Returns `previous` if `current` is None; returns `current` if `previous` is None. Used for both head pose (alpha=0.2) and gaze ratios (alpha=0.15).

#### 6. `calibrate_reference_pose(cap)` [async]
- **Parameters:** `cap` (cv2.VideoCapture object) | **Returns:** dict with "reference_yaw", "reference_pitch", "reference_gaze_yaw", "reference_gaze_pitch" (floats), or `None` if calibration fails or user presses 'q'.
- Runs a 3-second calibration phase prompting the user to look at the screen naturally. Collects yaw/pitch samples from valid face detections (requires ≥10 valid samples, computes mean). When `ENABLE_EYE_GAZE=True`, also collects gaze ratio samples and computes median as gaze reference (fallback to 0.5 if <10 samples). Displays a brief "calibration complete" message.

#### 7. `run_client()` [async]
- **Parameters:** none | **Returns:** none (runs indefinitely until user presses 'q' or unrecoverable error).
- Main entry point orchestrating the full pipeline:
  1. Opens webcam (device 0)
  2. Calls `calibrate_reference_pose()` to establish baseline head pose
  3. Enters a WebSocket reconnection loop connecting to `ws://SERVER_IP:SERVER_PORT`
  4. On connection, sends `calibration_complete` event and initializes state machine variables
  5. Continuously reads frames, calls `analyze_frame()`, applies smoothing, computes deviations from reference, and runs the engagement state machine
  6. On state transitions (engaged ↔ disengaged), sends `posture` event over WebSocket
  7. Renders real-time debug overlay (4 lines of metrics + optional 5th line for gaze) on the video feed
  8. Handles connection failures with automatic 3s retry

##### State Machine Components (within `run_client()`):

- **Detection Arming**: Requires `MIN_VALID_FACE_FRAMES` (10) consecutive valid pose frames before enabling engagement tracking. Resets counter on invalid frames.

- **Pose Smoothing**: Applies exponential moving average (alpha=0.2) to raw yaw/pitch values. Resets to None on invalid poses.

- **Screen-Facing Determination**: When engaged, uses strict thresholds (34°/18°) with hysteresis margins (10°/7°); effective look-away entry is (44°/25°) due to hysteresis. When disengaged, uses relaxed thresholds (50°/30°) to facilitate re-engagement.

- **Grace Period Handling**: Tolerates brief face detection loss (0.8s) and brief pose estimation failure (0.8s) by preserving last known screen-facing state.

- **Away Tracking**: Accumulates `away_duration` when participant is confirmed looking away/missing. Triggers disengagement when `away_duration ≥ 3.0s`. Includes break tolerance (0.25s) — brief looks back don't reset the away timer.

- **Reengage Tracking**: Accumulates `reengage_duration` when participant returns to facing screen. Triggers re-engagement when `reengage_duration ≥ 1.0s` AND (when eye gaze is enabled) gaze is not deviated. Includes break tolerance (0.35s) — brief glances away don't reset the reengage timer.

- **[Optional] Eye Gaze Mind Wandering Detection**: When `ENABLE_EYE_GAZE=True`, computes gaze deviation from calibrated reference using iris position ratios (entry threshold: yaw>0.15, pitch>0.16 with hysteresis). Only evaluated when head yaw deviation < 15° (parallax guard). Has its own hysteresis margins (0.05/0.04). Independent MW timer: sustained gaze deviation ≥ 2.0s directly triggers disengagement (reason="gaze_mind_wandering"). Has break tolerance (0.5s) — brief gaze-returns don't reset timer. **Unified MW detection**: when head-pose disengagement fires (away_duration ≥ 3.0s), `gaze_mind_wandering` is also set True — ensuring either head rotation or gaze deviation both result in `gaze_mind_wandering=True` in WebSocket events. When disabled (`ENABLE_EYE_GAZE=False`), all gaze paths produce None/False with zero behavioral change.

- **Event Transmission**: On state change, constructs a JSON posture event with all relevant metrics (face status, angles, deviations, durations, timestamps) and sends over WebSocket.

- **Visual Debug Output**: Renders 4 lines on video feed: Line 1 (pose data, colored green/red), Line 2 (reference angles, yellow), Line 3 (deviations and thresholds, yellow), Line 4 (state machine status, colored green/red). When `ENABLE_EYE_GAZE=True`, renders Line 5 (gaze ratios, deviations, MW timer, colored orange/green).

- **Connection Retry**: On WebSocket disconnect, waits `RECONNECT_DELAY` (3.0s) and retries indefinitely. Frame processing continues uninterrupted regardless of connection state.

#### 8. Entry Point
- `if __name__ == "__main__": asyncio.run(run_client())` — Launches the async event loop to start the client.