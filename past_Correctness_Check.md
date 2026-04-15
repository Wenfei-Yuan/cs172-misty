{=============================Correctness Check: 1===============================}
Incorrect: Webcam head-pose disengagement does not honor the documented 2.0 second away-duration threshold, so short pose deviations can trigger a distraction start immediately.
Potential Cause:
- `DISENGAGE_THRESHOLD = 2.0` is declared in `webcam/participant_client.py` but is never used as a gate before setting `disengaged = True`.
- In the active error branch, `away_start_time` and `away_duration` are tracked for display/telemetry, but the code flips to disengaged on the first confirmed `looking_away`, `face_missing`, or `pose_estimation_failed` condition.
- This makes the non-gaze start path much more eager than the stated design, which can produce brief start-then-stop cycles when pose deviation is transient.

{=============================Correctness Check: 2===============================}
Incorrect: Recovery notification to the extension is documented and previously reported as fixed, but the current bridge implementation never sends `AttentionResumed` after webcam recovery.
Potential Cause:
- `server.py` only implements `notify_extension_posture_disengagement(...)` for the start-side `ROBOT_REDIRECT` path; there is no stop-side notifier.
- `tests/test_server.py` now asserts that an extension receives only `ROBOT_REDIRECT` even across a full webcam start->stop cycle, so the regression is baked into the test suite.
- `README.md` and `update_logs.md` still describe `AttentionResumed` as part of the live contract, so code, tests, and docs are out of sync and can hide extension-state recovery failures.