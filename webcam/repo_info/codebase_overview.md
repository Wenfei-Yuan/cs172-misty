# Codebase Overview: Participant Engagement Monitoring Client

## Repository Description

This repository contains a **real-time webcam-based participant engagement monitoring client**. It tracks whether a participant is actively looking at the screen (engaged) or looking away (disengaged) during screen-based activities such as reading or online instruction. The client uses computer vision, head pose estimation, and **optional eye gaze tracking** to detect engagement state transitions and mind wandering, and communicates those events to a remote WebSocket server for logging and analysis.

**Core Problem**: Distinguish between active engagement (looking at screen) and disengagement (looking away/absent/mind wandering) in a robust, low-latency manner suitable for user studies or educational monitoring.

## File Structure

```
participant/
├── participant_client.py          (Python script - main client application)
└── repo_info/
    ├── codebase_overview.md       (this file)
    ├── scripts_overview.md        (detailed function-level documentation)
    ├── known_issues.md            (manually tracked issues)
    ├── known_issues_auto_generated.md (auto-generated issue analysis)
    ├── update_logs.md             (manual update logs)
    ├── update_logs_auto_generated.md  (git commit history)
    └── past_Q&A.md               (past questions and answers)
```

## Pipeline Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        INITIALIZATION PHASE                              │
│                                                                          │
│  ┌─────────────────┐    ┌──────────────────────────────────────────┐     │
│  │  Open Webcam     │───▶│  Calibration (3 sec)                    │     │
│  │  (cv2.VideoCapture)│  │  calibrate_reference_pose(cap)          │     │
│  └─────────────────┘    │  - Collect yaw/pitch samples             │     │
│                          │  - Compute reference_yaw/reference_pitch │     │
│                          │  - [OPT] Collect gaze ratio samples      │     │
│                          │  - [OPT] Compute ref gaze yaw/pitch      │     │
│                          │  - Require ≥10 valid samples             │     │
│                          └──────────────┬───────────────────────────┘     │
│                                         │                                 │
│                                         ▼                                 │
│                          ┌──────────────────────────────────────────┐     │
│                          │  Send calibration_complete event         │     │
│                          │  to WebSocket server                     │     │
│                          └──────────────┬───────────────────────────┘     │
└─────────────────────────────────────────┼────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     MAIN ENGAGEMENT LOOP (continuous)                     │
│                     run_client() — participant_client.py                  │
│                                                                          │
│  ┌─────────────────┐                                                     │
│  │ Frame Capture    │  cap.read() → BGR frame                            │
│  │ (OpenCV)         │                                                     │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ Face Detection   │  analyze_frame(frame) → MediaPipe FaceMesh         │
│  │ (MediaPipe)      │  Detects 468 facial landmarks + iris landmarks     │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ├────────────────────────────────────────────┐                   │
│           ▼                                            ▼                   │
│  ┌─────────────────┐                          ┌─────────────────┐         │
│  │ Head Pose Est.   │                          │ Eye Gaze Est.   │ [OPT]  │
│  │ (PnP Solver)     │                          │ (IPR Method)    │         │
│  │ 6 landmarks →    │                          │ Iris pos ratio  │         │
│  │ yaw, pitch, roll │                          │ → gaze_yaw/pitch│         │
│  └────────┬────────┘                          └────────┬────────┘         │
│           │                                            │                   │
│           ▼                                            ▼                   │
│  ┌─────────────────┐                          ┌─────────────────┐         │
│  │ Pose Smoothing   │  EMA (alpha=0.2)         │ Gaze Smoothing  │ [OPT]  │
│  │ (Exponential MA) │                          │ EMA (alpha=0.15)│         │
│  └────────┬────────┘                          └────────┬────────┘         │
│           │                                            │                   │
│           ▼                                            │                   │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ Deviation Calc   │  yaw_dev = |smooth_yaw - ref_yaw|                  │
│  │                   │  pitch_dev = |smooth_pitch - ref_pitch|            │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ Threshold Test   │  Adaptive thresholds with hysteresis:              │
│  │ (Hysteresis)     │  Engaged: yaw≤34°, pitch≤18° (+10°/7° margin)     │
│  │                   │  Disengaged: yaw≤50°, pitch≤30° (to re-engage)   │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ Grace Period     │  Face missing grace: 0.8s                          │
│  │ Filter           │  Pose invalid grace: 0.8s                          │
│  │                   │  Preserves last known state during brief failures │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ State Machine    │  Detection arming (10 valid frames)                │
│  │ (Engagement)     │  Away duration → DISENGAGE after 3s               │
│  │                   │  Reengage duration → ENGAGE after 1s              │
│  │                   │  Break tolerances (0.25s away, 0.35s reengage)   │
│  │                   │                                                   │
│  │                   │  [OPT] Gaze MW timer (independent, 2.0s)          │
│  │                   │  Gaze deviation → DISENGAGE (mind wandering)      │
│  │                   │  Head-pose disengagement → gaze_mind_wandering=True│
│  │                   │  Reengage blocked while gaze still deviated       │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           ▼                                                               │
│  ┌─────────────────┐    ┌─────────────────┐                              │
│  │ State Changed?   │───▶│ WebSocket Send   │  JSON posture event        │
│  │                   │YES │ (websockets)     │  to server at             │
│  └────────┬────────┘    └─────────────────┘  ws://10.5.15.160:8765      │
│           │NO                                                             │
│           ▼                                                               │
│  ┌─────────────────┐                                                     │
│  │ Display Overlay  │  Real-time debug info on video feed                │
│  │ (OpenCV imshow)  │  Color: green=engaged, red=disengaged              │
│  └────────┬────────┘                                                     │
│           │                                                               │
│           └────────────── Loop back to Frame Capture ◄───────────────    │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                     CONNECTION MANAGEMENT                                │
│  Auto-reconnect every 3.0s on WebSocket disconnect                       │
│  Ping/pong keepalive: interval=20s, timeout=60s                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## State Machine Detail

```
            ┌─────────────────────┐
            │   NOT ARMED          │
            │ (valid_face_streak   │
            │  < 10 frames)        │
            └──────────┬──────────┘
                       │ 10 consecutive valid frames
                       ▼
            ┌─────────────────────┐
            │   ENGAGED            │◄────────────────────────┐
            │ (disengaged=False)   │                          │
            └──────────┬──────────┘                          │
                       │ away_duration ≥ 3.0s                │ reengage_duration ≥ 1.0s
                       │ OR [OPT] gaze_mw ≥ 2.0s             │ AND [OPT] gaze not deviated
                       ▼                                      │
            ┌─────────────────────┐                          │
            │   DISENGAGED         │──────────────────────────┘
            │ (disengaged=True)    │
            │ Uses relaxed         │
            │ thresholds           │
            └─────────────────────┘
```

## Key Configuration Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `DISENGAGE_THRESHOLD` | 3.0s | Duration away before marking disengaged |
| `REENGAGE_THRESHOLD` | 1.0s | Duration facing screen before marking re-engaged |
| `YAW_DEVIATION_THRESHOLD` | 34.0° | Max yaw deviation when engaged |
| `PITCH_DEVIATION_THRESHOLD` | 23.0° | Max pitch deviation when engaged |
| `REENGAGE_YAW_DEVIATION_THRESHOLD` | 50.0° | Relaxed yaw threshold for re-engagement |
| `REENGAGE_PITCH_DEVIATION_THRESHOLD` | 35.0° | Relaxed pitch threshold for re-engagement |
| `YAW_HYSTERESIS_MARGIN` | 10.0° | Hysteresis band for yaw |
| `PITCH_HYSTERESIS_MARGIN` | 7.0° | Hysteresis band for pitch |
| `POSE_SMOOTHING_ALPHA` | 0.2 | EMA smoothing factor |
| `AWAY_BREAK_TOLERANCE` | 0.25s | Brief screen-looks during away are ignored |
| `REENGAGE_BREAK_TOLERANCE` | 0.35s | Brief look-aways during reengage are ignored |
| `MIN_VALID_FACE_FRAMES` | 10 | Consecutive valid frames to arm detection |
| `FACE_MISSING_GRACE_PERIOD` | 0.8s | Grace period for face detection loss |
| `POSE_INVALID_GRACE_PERIOD` | 0.8s | Grace period for pose estimation failure |
| `CALIBRATION_DURATION` | 3.0s | Time window for reference pose calibration |
| `ENABLE_EYE_GAZE` | True | Toggle eye gaze augmentation on/off |
| `GAZE_SMOOTHING_ALPHA` | 0.15 | EMA smoothing for gaze ratios |
| `GAZE_YAW_DEVIATION_THRESHOLD` | 0.15 | Horizontal gaze deviation threshold |
| `GAZE_PITCH_DEVIATION_THRESHOLD` | 0.18 | Vertical gaze deviation threshold |
| `GAZE_YAW_HYSTERESIS` | 0.05 | Hysteresis margin for horizontal gaze |
| `GAZE_PITCH_HYSTERESIS` | 0.04 | Hysteresis margin for vertical gaze |
| `GAZE_MIND_WANDERING_DURATION` | 2.0s | Sustained gaze deviation before MW |
| `GAZE_MW_BREAK_TOLERANCE` | 0.5s | Brief gaze-returns don't reset MW timer |
| `GAZE_HEAD_YAW_LIMIT` | 15.0° | Only evaluate gaze when head yaw < this |

## WebSocket Protocol

**Server**: `ws://10.5.15.160:8765` (configurable via `SERVER_IP`, `SERVER_PORT`)

**Message 1 — Calibration Complete** (sent once at startup):
```json
{
  "source": "webcam",
  "type": "calibration_complete",
  "reference_yaw": <float>,
  "reference_pitch": <float>,
  "enable_eye_gaze": <bool>,
  "reference_gaze_yaw": <float|null>,
  "reference_gaze_pitch": <float|null>,
  "timestamp": <float>
}
```

**Message 2 — Posture State Change** (sent only on engagement transitions):
```json
{
  "client": "webcam",
  "source": "webcam",
  "type": "posture",
  "disengage": <bool>,
  "face_present": <bool>,
  "screen_facing": <bool>,
  "reason": "<string>",
  "yaw": <float|null>,
  "pitch": <float|null>,
  "reference_yaw": <float>,
  "reference_pitch": <float>,
  "yaw_deviation": <float|null>,
  "pitch_deviation": <float|null>,
  "away_duration": <float>,
  "reengage_duration": <float>,
  "gaze_yaw_ratio": <float|null>,
  "gaze_pitch_ratio": <float|null>,
  "gaze_yaw_deviation": <float|null>,
  "gaze_pitch_deviation": <float|null>,
  "gaze_mind_wandering": <bool|null>,
  "timestamp": <float>
}
```