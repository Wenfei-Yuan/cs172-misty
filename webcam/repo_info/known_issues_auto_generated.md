# Known Issues (Auto-Generated)

---

## 1. State Reset on WebSocket Reconnection

**Problem Description:** All engagement state variables (disengaged, away_duration, reengage_duration, timing variables) are re-initialized to default values each time the WebSocket connection is re-established, with no state synchronization message sent to the server.

**Root Causes:**
- State variables are declared inside the `async with websockets.connect(...)` block in `run_client()`, so they are reset on every new connection.
- No state persistence mechanism or reconnection-state-sync message type is implemented.

**Consequences:**
- If a user is genuinely disengaged and the WebSocket drops, upon reconnection the client assumes they are engaged, causing a state mismatch with the server.
- Server-side analytics will record incorrect engagement data across reconnection boundaries.
- Unless engagement state changes again after reconnection, the server never receives the corrected state.

---

## 2. Hardcoded Configuration Values

**Problem Description:** All 20+ configuration constants (server IP/port, angle thresholds, timing parameters, camera device ID) are hardcoded directly in the source file with no external configuration mechanism.

**Root Causes:**
- `SERVER_IP`, `SERVER_PORT`, all threshold and timing constants, and camera device (`cv2.VideoCapture(0)`) are defined as module-level constants.
- No configuration file, environment variable, or command-line argument parsing is implemented.

**Consequences:**
- Every parameter change requires modifying source code and restarting the application.
- Cannot deploy to different environments (different server, different camera) without code changes.
- On multi-camera systems, may capture from the wrong device.
- No validation that configuration values are consistent (e.g., DISENGAGE_THRESHOLD > REENGAGE_THRESHOLD).

---

## 3. Unencrypted WebSocket Without Authentication

**Problem Description:** The client connects to the server using unencrypted `ws://` protocol and sends data without any authentication token or mutual TLS.

**Root Causes:**
- WebSocket URL is constructed as `ws://` instead of `wss://`.
- No authentication layer (tokens, keys, certificates) is implemented in the connection or message protocol.

**Consequences:**
- Engagement data (participant behavior) is transmitted in plaintext, vulnerable to interception.
- Any client can connect to the server and inject false engagement data.
- No audit trail of which participant sent which data.

---

## 4. Inconsistent Angle Reporting: Smoothed vs Raw

**Problem Description:** The engagement state machine uses smoothed (EMA-filtered) yaw/pitch angles for threshold decisions, but sends raw (unsmoothed) angles in WebSocket posture events to the server.

**Root Causes:**
- `effective_yaw` (smoothed) is used for `yaw_deviation` calculation and screen-facing determination.
- The posture event message uses raw `yaw` and `pitch` values from `analyze_frame()` output.

**Consequences:**
- Server-side analytics see noisy angle values that don't match the engagement decisions made by the client.
- If the server implements its own engagement logic using raw values, results will not replicate client behavior.
- Debugging state transitions is difficult when server data doesn't match client decisions.

---

## 5. No Outlier Filtering During Calibration

**Problem Description:** The 3-second calibration phase collects all valid yaw/pitch samples and computes a simple mean without any outlier detection or noise filtering.

**Root Causes:**
- `calibrate_reference_pose()` appends every valid sample to `yaw_samples`/`pitch_samples` without quality checks.
- Reference is computed as `np.mean(yaw_samples)` with no statistical filtering (median, IQR, Z-score).

**Consequences:**
- If the user moves their head or looks away during calibration, the reference pose is biased.
- Biased reference causes all subsequent deviation calculations to be systematically off.
- Engagement thresholds become misaligned with the user's actual screen-facing pose.

---

## 6. Wall-Clock Time Used for Duration Calculations

**Problem Description:** All timing calculations use `time.time()` (wall-clock time) instead of `time.monotonic()`, which is susceptible to clock adjustments.

**Root Causes:**
- `now = time.time()` is used throughout `run_client()` for `away_start_time`, `reengage_start_time`, `face_missing_start_time`, etc.
- Duration calculations like `now - away_start_time` can become negative or jump if the system clock is adjusted.

**Consequences:**
- NTP synchronization or manual clock changes during a session could cause duration calculations to go negative or spike.
- State machine could get stuck (e.g., `away_duration` goes negative, never reaching threshold) or trigger prematurely.
- Timestamp values in WebSocket messages could be out of order.

---

## 7. Blocking OpenCV Calls in Async Event Loop

**Problem Description:** Synchronous blocking calls (`cv2.imshow()`, `cv2.waitKey(1)`, `cap.read()`) are executed inside the async event loop, which can stall WebSocket ping/pong and other async operations.

**Root Causes:**
- OpenCV's GUI functions are inherently synchronous and block the calling thread.
- These are called directly in the `run_client()` async function without offloading to a thread pool.

**Consequences:**
- If video rendering is slow (e.g., on systems with limited GPU), the async event loop is blocked.
- WebSocket ping/pong could miss its timeout, causing the connection to drop.
- Timing jitter in state machine calculations affects accuracy of duration-based thresholds.

---

## 8. No Recovery for Sustained Camera Failures

**Problem Description:** If the camera is disconnected or consistently fails to provide frames, the main loop retries indefinitely with no escalation, alert, or graceful shutdown.

**Root Causes:**
- `cap.read()` failure in the main loop prints a message and continues (`await asyncio.sleep(0.1); continue`).
- No counter for consecutive failures and no threshold to trigger alerts or shutdown.

**Consequences:**
- If camera hardware is disconnected, the program appears to keep running but produces no data.
- No indication to the user or server that the system is non-functional.
- After grace periods expire, user is marked as disengaged due to missing face, generating false events.

---

## 9. Camera Intrinsics Approximation

**Problem Description:** The camera matrix uses frame width as the focal length (`focal_length = w`), which is a rough approximation that does not reflect actual camera optics.

**Root Causes:**
- `get_head_pose()` sets `focal_length = w` without calibration against the actual camera.
- No camera calibration step (e.g., OpenCV `calibrateCamera`) is performed.

**Consequences:**
- Absolute yaw/pitch angle estimates have systematic scaling errors (potentially 20-30% off).
- The calibration step compensates for this by establishing a relative reference, so engagement detection still works, but absolute angle values sent to the server are inaccurate.
- If the server needs accurate real-world angles, the values will be unreliable.

---

## 10. No Calibration Recovery or Recalibration

**Problem Description:** If calibration fails (fewer than 10 valid samples) or if conditions change during a session (lighting, camera position, user posture), there is no way to recalibrate without restarting the application.

**Root Causes:**
- `calibrate_reference_pose()` is called once at startup; failure returns None and the program exits.
- No command or trigger exists to initiate recalibration during runtime.

**Consequences:**
- Environmental changes (lighting, camera bump, user shifting) make the reference pose stale.
- The user must fully restart the application to recalibrate.
- Engagement detection accuracy degrades silently over long sessions.

---

## 11. Infinite Reconnection Loop Without Backoff

**Problem Description:** Failed WebSocket connections retry every 3 seconds indefinitely with a constant delay, with no exponential backoff or maximum retry limit.

**Root Causes:**
- The reconnection loop uses `await asyncio.sleep(RECONNECT_DELAY)` with a fixed 3.0s delay.
- No counter, backoff multiplier, or maximum retry threshold is implemented.

**Consequences:**
- If the server is permanently unreachable, the client hammers it with connection attempts every 3 seconds forever.
- Wastes network resources and could be flagged by firewalls or intrusion detection systems.
- No alert to the user that the system cannot reach the server.

---

## 12. Euler Angle Gimbal Lock Near Extreme Head Poses

**Problem Description:** The `rotation_matrix_to_euler_angles()` function handles gimbal lock by defaulting roll to 0, but this can cause yaw/pitch discontinuities when the head is tilted near 90°.

**Root Causes:**
- Euler angle decomposition is inherently unstable near gimbal lock (pitch ≈ ±90°).
- The `singular` branch sets `z = 0` (roll), but yaw estimates can jump by up to 180° between frames.

**Consequences:**
- Near-vertical head poses can cause sudden large yaw deviations that exceed the hysteresis threshold.
- Could cause momentary false "looking away" detection even when the head hasn't meaningfully moved.
- Unlikely during normal use but possible with intentional head tilting.

---

## 13. face_mesh.close() Potential Compatibility Issue

**Problem Description:** The `finally` block calls `face_mesh.close()`, which may not exist in all MediaPipe versions.

**Root Causes:**
- `face_mesh.close()` is called in the cleanup block of `run_client()`.
- Older or different versions of MediaPipe may not expose this method.

**Consequences:**
- An `AttributeError` crash on application exit in incompatible MediaPipe versions.
- Graceful shutdown fails, potentially leaving camera resources unreleased.