# QA Report: Eye Gaze Augmentation for Mind Wandering Detection

**Date**: 2025-04-04  
**File under test**: `participant_client.py`  
**Scope**: Full pipeline validation + new eye gaze functionality

---

## Summary

| Category | Pass | Fail | Warn |
|----------|------|------|------|
| ENABLE_EYE_GAZE = False | 6/6 | 0 | 0 |
| ENABLE_EYE_GAZE = True | 12/14 | 1 | 1 |
| Edge Cases | 4/5 | 1 | 0 |
| **Total** | **22/25** | **2** | **1** |

---

## A. When ENABLE_EYE_GAZE = False

### A1. analyze_frame() skips gaze computation — PASS
Lines 205–207: `gaze = None` is the default; `get_eye_gaze()` is only called inside `if ENABLE_EYE_GAZE:`. Gaze is `None` in all return paths.

### A2. Gaze state variables properly initialized to None/False — PASS
Lines 411–416: On every WebSocket (re)connect, all gaze state is reset:
```python
smoothed_gaze_yaw = None
smoothed_gaze_pitch = None
gaze_mw_start_time = None
gaze_mw_break_start_time = None
gaze_mind_wandering = False
```
The `else` branch (line 636) also resets these each frame since the guard `ENABLE_EYE_GAZE and detection_armed and not disengaged` is False.

### A3. State machine (is_away, away_duration, reengage) identical to original — PASS
Lines 547–600: Head-pose state machine logic is unmodified. Gaze MW section (lines 601–640) is guarded by `ENABLE_EYE_GAZE and ...`, so when disabled it falls to the `else` branch which is a harmless no-op on already-None/False variables.

### A4. Debug overlay identical (no extra line) — PASS
Lines 683–692: Gaze overlay block is guarded by `if ENABLE_EYE_GAZE:`. When False, only the original 4 lines are drawn.

### A5. WebSocket events backward compatible — PASS
Lines 705–711: New fields are present but evaluate to `None`:
- `gaze_yaw_ratio: None`, `gaze_pitch_ratio: None`
- `gaze_yaw_deviation: None`, `gaze_pitch_deviation: None`
- `gaze_mind_wandering: None`

These are additive fields. Backward compatible if the server ignores unknown keys.

### A6. Calibration works identically — PASS
- Head-pose calibration (mean of yaw/pitch, ≥10 samples) is unchanged.
- Gaze sample collection is guarded by `ENABLE_EYE_GAZE` (line 295).
- Reference gaze defaults to `(0.5, 0.5)` when disabled (line 328).
- Return dict now always includes `reference_gaze_yaw`/`reference_gaze_pitch` — consumed correctly by `run_client()` (lines 358–359).

---

## B. When ENABLE_EYE_GAZE = True

### B1. get_eye_gaze() handles degenerate eyes — PASS
Lines 160–161: `if eye_w < 5.0 or eye_h < 3.0: return None, None`. Both per-eye ratios fail safely to `(None, None)`.

### B2. get_eye_gaze() handles one-eye failure — PASS
Lines 173–182: Three-branch fallback: both eyes → left only → right only → `None`. Correct.

### B3. analyze_frame() returns gaze even when pose fails — PASS
Lines 215–220: When `pose is None`, the return dict includes `"gaze": gaze` (which may be non-None). Gaze is computed independently of head pose (lines 205–209).

### B4. Calibration collects gaze samples and uses median — PASS
- Line 295–297: Gaze samples collected when `ENABLE_EYE_GAZE and result["gaze"] is not None`.
- Line 325: `np.median(gaze_yaw_samples)` used (more robust than mean for ratio data).
- Requires ≥10 gaze samples; defaults to 0.5 otherwise (line 328).

### B5. Gaze EMA smoothing works correctly — PASS (with note)
Lines 461–465: Uses `smooth_angle()` with `GAZE_SMOOTHING_ALPHA = 0.15`.

**Design note**: When `gaze is None` (e.g., eyes closed for one frame), the entire smoothed state is reset to `None` (line 465). This destroys EMA history. Consistent with head-pose smoothing pattern but more aggressive than a grace-period approach. A single blink frame wipes the EMA buffer.

### B6. Parallax guard (yaw_deviation < 15°) — PASS
Lines 607–608: `if yaw_deviation is not None and yaw_deviation < GAZE_HEAD_YAW_LIMIT:` correctly gates gaze evaluation. When head is turned (yaw_dev ≥ 15°) or yaw_deviation is None, `gaze_looking_away` remains False. Prevents false positives from parallax-shifted iris.

### B7. Hysteresis applied correctly — PASS
Lines 609–614:
- **Not in MW** (`gaze_mind_wandering=False`): thresholds = base + margin → harder to trigger MW
- **In MW** (`gaze_mind_wandering=True`): thresholds = base − margin → stickier (harder to exit MW)

This is correct hysteresis preventing oscillation at the boundary.

### B8. MW timer accumulates correctly — PASS
Lines 620–627: Timer starts at first `gaze_looking_away=True` frame, accumulates until `>= GAZE_MIND_WANDERING_DURATION (2.0s)`, then fires.

### B9. MW break tolerance prevents premature reset — PASS
Lines 629–634: When gaze briefly returns, `gaze_mw_break_start_time` starts. Timer only resets after `GAZE_MW_BREAK_TOLERANCE (0.5s)` of sustained gaze-back. Brief returns are ignored.

### B10. MW triggers disengaged=True and state_changed=True — PASS
Lines 625–627: Correct.

### B11. reason set to "gaze_mind_wandering" — PASS
Line 628: `reason = "gaze_mind_wandering"` overrides the pose-based reason. Correct. This only fires when the head is facing the screen (parallax guard) but eyes wander.

### B12. gaze_mind_wandering reset on re-engagement — WARN ⚠️
**See BUG #1 below.** While `gaze_mind_wandering` IS technically reset (the `else` branch on line 636 fires every frame while `disengaged=True`), the re-engagement pathway itself has a design flaw — see B13.

### B13. Gaze MW re-engagement oscillation — FAIL ❌ (BUG #1)

**BUG: Head-pose re-engagement overrides gaze-based disengagement, causing oscillation.**

**Reproduction scenario**: User's head faces the screen, but eyes gaze to the side for >2 seconds.

**Expected**: System detects mind wandering and stays in disengaged state until gaze returns.

**Actual behavior**:
1. `t=0s`: Gaze MW timer starts (head facing screen, gaze deviated)
2. `t=2.0s`: `gaze_mind_wandering=True`, `disengaged=True`, `reason="gaze_mind_wandering"` → event sent
3. `t=2.0s+1frame`: `disengaged=True` → gaze MW `else` branch fires → `gaze_mind_wandering=False`, timers reset
4. `t=2.0s+1frame`: Head IS facing screen → `screen_facing=True` (using relaxed thresholds 50°/35°, easily met) → `is_away=False` → re-engagement timer starts
5. `t=3.0s`: `reengage_duration >= 1.0s` → `disengaged=False`, `state_changed=True` → re-engage event sent
6. `t=3.0s+1frame`: `not disengaged=True` → gaze MW timer starts fresh (eyes still wandering)
7. `t=5.0s`: MW fires again → cycle repeats

**Result**: Infinite oscillation — MW every ~3s (2s accumulation + 1s re-engage). Two WebSocket events per cycle.

**Root cause**: The re-engagement logic only considers head pose (`screen_facing`), not gaze state. When gaze MW fires and sets `disengaged=True`, the head-pose re-engage pathway immediately begins counting because the head is still facing the screen.

**Suggested fix**: Block head-pose re-engagement while `gaze_looking_away` is True, OR don't use `disengaged=True` for gaze MW (use a separate signal/event type), OR add `and not gaze_looking_away` to the re-engage condition.

### B14. Debug overlay shows gaze info — PASS
Lines 683–692: Shows smoothed gaze ratios, deviations, MW timer duration. Uses orange color when gaze deviates, green when normal. Shows "gaze=N/A" when gaze data unavailable.

### B15. WebSocket events include gaze fields — PASS
Lines 705–711: All gaze fields included with proper None-guarded rounding.

---

## C. Edge Cases

### C1. Face detected but gaze returns None (eyes closed) — PASS
- `analyze_frame()`: Returns `gaze=None` with valid face/pose data.
- Smoothed gaze reset to `None` (line 465).
- `gaze_looking_away` stays False (guard on line 605 fails).
- MW timer enters break-tolerance path or no-op. No false trigger.
- **Note**: EMA history is destroyed on a single blink frame (consistent with head-pose pattern).

### C2. Gaze triggers MW and head-pose triggers away simultaneously — PASS
**Scenario**: Head turned away AND gaze deviated on the same frame.
- Head-pose state machine runs first (lines 562–600).
- If `away_duration >= 3.0s`: `disengaged=True` → gaze MW guard `not disengaged` fails → gaze state reset. Head-pose wins.
- If `away_duration < 3.0s`: disengaged still False → gaze MW may fire first if its timer is ready.
- No double-trigger possible: once `disengaged=True`, the gaze guard blocks.
- Additionally: parallax guard (`yaw_deviation < 15°`) would prevent gaze evaluation when head is turned significantly, so temporal overlap is unlikely.

### C3. Arming phase (not detection_armed) — PASS
Lines 546–560: During arming, `away_duration=0.0`, `reengage_duration=0.0`. Gaze MW block: `detection_armed=False` → else branch → gaze state held at zero/None/False. No false triggers possible.

### C4. WebSocket reconnect — PASS
Lines 395–416: All state variables (including all gaze-specific ones) are reinitialized on every WebSocket reconnect. Calibration message is re-sent with `enable_eye_gaze` flag and gaze reference values. Clean restart.

### C5. NameError or uninitialized variable risk — FAIL ❌ (BUG #2)

**BUG: `away_duration` and `reengage_duration` may be referenced before assignment when `detection_armed=True`.**

Actually, on closer inspection this is NOT a new bug — tracing through:
- After arming (line 560), `away_duration` and `reengage_duration` were set to `0.0` on the previous frame (line 557–558).
- On the first armed frame, the `elif is_away:` or `else:` branches always assign these variables.
  
**Revised: PASS** — No NameError risk. All variables are initialized before use:
- `gaze_yaw_dev`, `gaze_pitch_dev`, `gaze_looking_away`: Initialized lines 601–603 before conditional blocks.
- `gaze_mw_duration`: Computed line 640 (always, regardless of enable flag).
- `away_duration`, `reengage_duration`: Initialized in arming phase (lines 557–558), assigned in all subsequent branches.

---

## D. Bug Summary

### BUG #1 (Severity: HIGH) — Gaze MW / Head-Pose Re-engagement Oscillation
**Location**: Lines 598–599 (re-engage logic) vs. lines 620–628 (gaze MW logic)  
**Impact**: When gaze MW fires while head faces screen, the system enters an infinite engage/disengage oscillation (period ~3s), flooding the server with spurious events.  
**Fix options**:
1. Add `and not gaze_looking_away` to the re-engagement condition on line 598:
   ```python
   if disengaged and reengage_duration >= REENGAGE_THRESHOLD and not gaze_looking_away:
   ```
2. Or: keep gaze MW from setting `disengaged=True` — emit a separate event type instead.
3. Or: when `reason` was `"gaze_mind_wandering"` for the disengage, require gaze to return before re-engaging.

### No other functional bugs found.

---

## E. Design Notes (Non-blocking)

| # | Note |
|---|------|
| 1 | **EMA reset on blink**: A single frame with `gaze=None` (eyes closed) wipes smoothed gaze history. Consistent with head-pose pattern but could cause jumpiness. A grace period (like `FACE_MISSING_GRACE_PERIOD`) could preserve continuity. |
| 2 | **Gaze calibration fallback**: When < 10 gaze samples during calibration, defaults to `(0.5, 0.5)`. Reasonable for most users but could be inaccurate for asymmetric eye structures. |
| 3 | **Additive WebSocket fields**: 5 new fields are always present in posture events (even when disabled, as `None`). Server must tolerate unknown keys for backward compatibility. |
| 4 | **MW event semantics**: After gaze MW fires, `gaze_mind_wandering` is reset to `False` on the very next frame (because `disengaged=True` triggers the else branch). The MW state is ephemeral — only True for a single frame. The server sees it as `True` in the disengagement event and `False` in the re-engagement event. |

---

## F. Test Matrix

| Scenario | Head Pose | Gaze | Expected Outcome | Code Path Verified |
|----------|-----------|------|------|------|
| Normal engagement | Facing | Centered | Engaged | ✅ |
| Head turn away | Turned | N/A (parallax) | Disengaged after 3s | ✅ |
| Gaze wander | Facing | Deviated | MW after 2s | ✅ (but oscillation bug) |
| Brief blink | Facing | None (1 frame) | No state change | ✅ |
| Eyes closed sustained | Facing | None (sustained) | Gaze=N/A, head-pose only | ✅ |
| Head turn + gaze | Turned | Deviated | Head-pose wins | ✅ |
| Calibration (gaze on) | Facing | Centered | Median gaze ref | ✅ |
| Calibration (gaze off) | Facing | N/A | Default 0.5 ref | ✅ |
| Reconnect | Any | Any | Full state reset | ✅ |
| Feature disabled | Any | Skipped | Original behavior | ✅ |

---

**Verdict**: Implementation is well-structured and the disabled path is fully backward compatible. **One high-severity bug** (oscillation) must be fixed before deployment. The fix is a one-line change to the re-engage condition.
