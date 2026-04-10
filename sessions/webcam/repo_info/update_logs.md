{=============================BUG FIX===============================}
{BUG Name: Head Pitch Initial Rotation Offset | Bug Id: 1}
{Bug description: When a person faces the camera with no head rotation, pitch read ~180° instead of the expected 0°. This also caused false disengagement events on any minor downward head nod because the neutral pitch value was parked exactly at arctan2's ±180° wrap boundary, producing massive spurious pitch deviations via EMA corruption.}
{Repo involved: participant (participant_client.py)}
{Implementation: Inserted a 7-line pitch correction block inside `get_head_pose()` in participant_client.py, immediately after `pitch, yaw, roll = rotation_matrix_to_euler_angles(rotation_matrix)` and before the return dict. The correction applies `pitch -= 180.0` then normalises to (−180°, 180°] using a lower guard (`if pitch <= -180.0: pitch += 360.0`) and a defensive-only upper guard (`elif pitch > 180.0: pitch -= 360.0`). No other functions or constants were changed.}
{Fixed: YES — neutral forward-facing pitch now reads 0°; yaw remains 0° at neutral (unaffected); EMA smoother now operates near 0°, eliminating wrap-around corruption and associated false disengagements.}

{=============================Function Update===============================}
{Eye Gaze Augmentation for Mind Wandering Detection — Functionality Id: 1}
{Optional eye gaze tracking using iris position ratios (IPR) to augment the existing head-pose-based engagement detection, enabling detection of mind wandering when users' eyes deviate while their head still faces the screen.}
{Repo involved: participant (local)}
{Implementation:
- Added `ENABLE_EYE_GAZE = True` toggle constant and 9 gaze-related configuration constants (thresholds, hysteresis, smoothing alpha, timer durations, parallax guard limit)
- Added 10 iris/eye landmark constants for MediaPipe FaceMesh iris tracking (468-477 range + eye corner/lid landmarks)
- Added `get_eye_gaze(face_landmarks, frame_width, frame_height)` function implementing IPR algorithm with binocular averaging, single-eye fallback, right-eye polarity correction, and degenerate eye guards
- Modified `analyze_frame()` to compute and return gaze data alongside head pose (gaze computed independently — succeeds even when PnP fails)
- Modified `calibrate_reference_pose()` to collect gaze ratio samples during calibration and compute reference gaze using median (with fallback to 0.5)
- Modified `run_client()` state machine: gaze EMA smoothing (alpha=0.15), parallax guard (head yaw < 15°), gaze hysteresis, independent mind wandering timer (2.0s) with break tolerance (0.5s), direct disengagement trigger, re-engagement blocked while gaze still deviated
- Added 5th debug overlay line for gaze info (ratios, deviations, MW timer) with color coding
- Added gaze fields to WebSocket calibration and posture events (additive, backward compatible)
}
{Achieved: Yes — both functionalities implemented: (1) eye gaze augmentation is toggleable via `ENABLE_EYE_GAZE` constant, (2) gaze-based mind wandering detection augments head-pose detection with independent timer and state integration}

{=============================Function Update===============================}
{Stricter Gaze and Head Pitch Thresholds with Unified Mind Wandering Detection — Functionality Id: 2}
{Tightened gaze deviation and head pitch rotation thresholds for earlier mind wandering detection, unified both detection paths so that either head rotation or gaze deviation alone triggers the `gaze_mind_wandering` flag, and fixed a pre-existing WebSocket message spam bug in the gaze MW timer.}
{Repo involved: participant (local)}
{Implementation:
- `PITCH_DEVIATION_THRESHOLD`: 23.0° → 18.0° (effective look-away entry now 25°, previously 30°)
- `REENGAGE_PITCH_DEVIATION_THRESHOLD`: 35.0° → 30.0° (maintains 5° re-engage gap above effective entry)
- `GAZE_YAW_DEVIATION_THRESHOLD`: 0.15 → 0.10 (effective gaze entry now 0.15, previously 0.20)
- `GAZE_PITCH_DEVIATION_THRESHOLD`: 0.18 → 0.12 (effective gaze pitch entry now 0.16, previously 0.22)
- In head-pose disengagement block: added `if ENABLE_EYE_GAZE: gaze_mind_wandering = True` — ensures head-rotation-triggered disengagement also sets `gaze_mind_wandering=True`, unifying both detection paths
- Fixed pre-existing WS spam bug: `state_changed = True` and `reason = "gaze_mind_wandering"` in gaze MW timer now guarded by `if not gaze_mind_wandering:` to prevent per-frame WebSocket message flood once MW fires
}
{Achieved: Yes — (1) gaze deviation threshold stricter: entry lowered from 0.20/0.22 to 0.15/0.16 ratio units; (2) head pitch threshold stricter: effective look-away entry lowered from 30° to 25°; (3) either head rotation or gaze deviation triggers gaze_mind_wandering=True; (4) WS spam bug fixed as part of implementation}
