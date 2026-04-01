# cs172-misty-3 Refactor Suggestions — Correctness & Code Quality Assessment

> **Date**: April 1, 2026  
> **Target**: IMPROVE CORRECTNESS AND CODE QUALITY  
> **Scope**: Assessment and suggestions only — NO CODE CHANGES  
> **Methodology**: 5 parallel analysis subagents (architecture, redundancy, robustness, free-mode, senior code review) → principal engineer review → devil's advocate challenge → consolidated final plan

---

## Executive Summary

The cs172-misty-3 codebase implements the Misty II social robot focus companion pipeline as described in `key_idea.md`. After thorough line-by-line review of all 17 source files and 8 test files, **5 critical correctness bugs**, **8 major robustness gaps**, **6 confirmed redundancies**, and **4 code quality improvements** were identified.

The pipeline's core flow (bootup → screen watch → wait for signal → distraction → recovery/escalation → summary) is architecturally sound and matches the key_idea.md specification. However, several logic errors and missing safeguards prevent the pipeline from operating reliably in edge cases.

---

## Phase 1: CRITICAL CORRECTNESS FIXES (Must-Do)

### 1.1 Missing `max_attempts` Enforcement in Pipeline Loop

- **File**: `pipeline.py`, lines 75–76
- **Severity**: CRITICAL
- **Issue**: The `attempt` counter is incremented on each timeout escalation but never checked against `cfg.max_attempts` (defined as 3 in `config.py`, line 22). The `while True` loop can run indefinitely through escalation cycles without ever breaking.
- **Current code**:
  ```python
  attempt += 1
  run_no_response(misty, cfg, log, attempt)
  # No check: if attempt >= cfg.max_attempts: break
  ```
- **Impact**: Robot may escalate voice prompts infinitely, never reaching the summary stage.
- **Suggested fix**: Add `if attempt >= cfg.max_attempts: break` BEFORE the `run_no_response()` call so the final escalation attempt still fires but the loop exits afterward. Alternatively, place the check AFTER `run_no_response()` if the intent is to allow all max_attempts escalations before exiting.
- **Devil's advocate note**: The placement matters — placing the check AFTER `run_no_response()` means attempt=4 would call `run_no_response` before breaking (one extra call). Placing it BEFORE prevents that. Decide based on intended UX.

---

### 1.2 Escalation Prompt Always Uses Index 0

- **File**: `stages/no_response_prompt.py`, line 9
- **Severity**: CRITICAL
- **Issue**: Hardcoded `cfg.escalation_prompts[0]` ignores the `attempt` parameter entirely. The config defines 3 distinct escalation prompts but only the first is ever used.
- **Current code**:
  ```python
  prompt = cfg.escalation_prompts[0]
  ```
- **Impact**: Users hear the same escalation message every time, defeating the purpose of the graduated prompts.
- **Suggested fix**: Use `cfg.escalation_prompts[min(attempt - 1, len(cfg.escalation_prompts) - 1)]` to cycle through prompts with safe bounds clamping. Alternatively, use modular indexing `(attempt - 1) % len(cfg.escalation_prompts)` if cycling is desired beyond max_attempts.
- **Devil's advocate note**: Ensure `len(cfg.escalation_prompts)` is never 0; add a guard or config validation.

---

### 1.3 Infinite Screen Search Loop

- **File**: `pipeline.py`, lines 30–33
- **Severity**: CRITICAL
- **Issue**: When `run_screen_watch()` returns `None` (screen not found), pipeline loops with `continue` and no attempt limit, backoff, or fallback. The robot retries screen detection indefinitely.
- **Current code**:
  ```python
  screen_pos = run_screen_watch(misty, cfg, log, screen_pos)
  if screen_pos is None:
      log.record("screen_retry_pending")
      continue  # No max retries, no backoff, no fallback
  ```
- **Impact**: Robot hangs permanently if screen is not detectable (e.g., camera obstructed, lighting issue).
- **Suggested fix**: Track `screen_search_attempts` as a local variable, increment on each failure, and break or use a fallback after 2–3 failures. Reset counter after any successful distraction cycle.
- **Devil's advocate warnings**:
  1. **State variable scope**: Must survive across loop iterations but reset appropriately (e.g., reset on successful gaze detection).
  2. **Fallback strategy risk**: Using `default_screen_position()` (yaw=-40°, pitch=-10°) as fallback means gaze detection may look in the wrong direction. Consider breaking to summary instead if screen truly cannot be found.
  3. **Dependency on Item 3.6**: If `default_screen_position()` is deleted (Phase 3 suggestion), the fallback must inline the logic. Coordinate these changes.

---

### 1.4 Duplicate "stop" Outcome Handling in Pipeline

- **File**: `pipeline.py`, lines 59 and 71–72
- **Severity**: MEDIUM
- **Issue**: `distraction.outcome == "stop"` is checked twice in separate if-blocks. The first logs the distraction end (line 59–60), the second resets the attempt counter (line 71–72). Between them, the "gaze" and "shutdown" checks run unnecessarily.
- **Current code**:
  ```python
  if distraction.outcome == "stop":
      log.record_distraction_end()
  # ... gaze check, shutdown check ...
  if distraction.outcome == "stop":
      attempt = 0
      continue
  ```
- **Impact**: Not a bug per se, but duplicated condition checks make control flow harder to follow and maintain.
- **Suggested fix**: Combine into a single block:
  ```python
  if distraction.outcome == "stop":
      log.record_distraction_end()
      attempt = 0
      continue
  ```

---

### 1.5 SessionLog Not Thread-Safe

- **File**: `utils/session_log.py`, entire class
- **Severity**: CRITICAL
- **Issue**: `SessionLog` has mutable state (`events`, `distraction_events`, `total_voice_prompts`, `screen_observations`) accessed without any synchronization. The pipeline's main thread calls `log.record()` and other methods, while the HTTP server thread (in `ExternalSignalReceiver`) processes events concurrently. Future modifications could easily introduce logging from the shake daemon thread.
- **Impact**: Potential data corruption: lost event records, partially written distraction event dicts, inconsistent counters.
- **Suggested fix**: Add `self._lock = threading.Lock()` in `__init__`, wrap all mutating methods (`record()`, `record_distraction_start()`, `record_distraction_result()`, `record_distraction_end()`, `record_voice_prompt()`, `record_screen_observation()`, `save_to_file()`) with `with self._lock:`.
- **Devil's advocate note**: Current code is single-threaded for log writes in practice (pipeline runs sequentially), so this is primarily defensive. However, it's low-cost and prevents future regressions.

---

### 1.6 `record_voice_prompt()` Overwrites Count Instead of Tracking It

- **File**: `utils/session_log.py`, `record_voice_prompt()` method
- **Severity**: MEDIUM
- **Issue**: `current["voice_prompt_count"] = int(attempt)` sets the count to the attempt number rather than incrementing a counter. If `attempt=3`, `voice_prompt_count` becomes 3 regardless of how many prompts were actually used in this distraction event.
- **Impact**: Session JSON has incorrect `voice_prompt_count` values in distraction event records.
- **Suggested fix**: Either increment (`current["voice_prompt_count"] += 1`) or rename to `current_attempt` to clarify semantics.

---

## Phase 2: ROBUSTNESS IMPROVEMENTS (High Priority)

### 2.1 Mixed Clock Sources in Gaze Polling

- **File**: `stages/distraction.py`, lines 40, 56, 71
- **Severity**: HIGH
- **Issue**: Uses `time.time()` for `poll_start` and elapsed calculation, but `time.monotonic()` for sleep interval calculation. If the system clock adjusts (NTP sync, daylight saving), elapsed time can jump or go negative, causing premature timeout or infinite polling.
- **Current code**:
  ```python
  poll_start = time.time()             # Wall clock
  # ...
  elapsed = time.time() - poll_start   # Wall clock delta
  # ...
  loop_started = time.monotonic()      # Monotonic clock
  remaining_sleep = max(0.0, poll_interval_s - (time.monotonic() - loop_started))  # Monotonic delta
  ```
- **Impact**: Spurious timeouts or extended sessions if clock adjusts.
- **Suggested fix**: Use `time.monotonic()` exclusively for all elapsed time and interval calculations. `gaze_latency` in the result will be monotonic-based but internally consistent.

---

### 2.2 Shake Thread Silent Failure

- **File**: `utils/head_control.py`, `shake_head_only()` function
- **Severity**: MEDIUM
- **Issue**: The `while` loop calls `misty.perform_action()` repeatedly with no exception handling. If the robot connection drops, the daemon thread crashes silently.
- **Impact**: Head shake stops without any log entry. Distraction stage continues but with reduced stimulus.
- **Suggested fix**: Wrap the loop body in try/except catching specific exceptions (e.g., `requests.ConnectionError`, `TimeoutError`). Track consecutive failures; after 3, break the loop. Avoid bare `except Exception` which would catch `KeyboardInterrupt` and `SystemExit`.
- **Devil's advocate note**: Catch SPECIFIC exceptions only. Document what happens when exceptions are caught (head stops at last position).

---

### 2.3 Shake Thread Join Timeout Not Logged

- **File**: `stages/distraction.py`, line 63
- **Severity**: LOW
- **Issue**: `shake_thread.join(timeout=2)` silently ignores timeout. If the thread is still alive, subsequent `look_at_screen()` may conflict with the still-running shake.
- **Suggested fix**: After join, check `if shake_thread.is_alive(): log.record("shake_thread_join_timeout")`.

---

### 2.4 Unbounded Event Queue in ExternalSignalReceiver

- **File**: `utils/triggers.py`, lines 22–23
- **Severity**: MEDIUM
- **Issue**: `self._events = deque()` and `self._current_texts = deque()` have no size limit. An adversarial or malfunctioning client flooding signals could cause unbounded memory growth.
- **Suggested fix**: Use `deque(maxlen=500)`. The deque automatically drops oldest events when full.
- **Devil's advocate note**: In extreme edge cases, a "shutdown" event could be pushed out by 500+ subsequent events. However, this requires sustained adversarial flooding which is unlikely in the research lab context.

---

### 2.5 Camera Capture Timeout

- **File**: `utils/vision.py`, line 113
- **Severity**: MEDIUM
- **Issue**: `requests.get(_camera_url(misty), timeout=5)` uses a 5-second timeout. During gaze polling (every 0.3–2.0s), a single timeout wastes 5 seconds, significantly extending the actual timeout window beyond `cfg.gaze_timeout_s`.
- **Suggested fix**: Reduce to 1.5–2.0s, or make configurable via `cfg.camera_timeout_s`.
- **Devil's advocate note**: Without empirical data on actual camera response times, reducing too aggressively may increase false failure rates. Recommend measuring P95 camera latency before choosing a final value.

---

### 2.6 No OpenAI API Retry Logic

- **File**: `utils/vision.py`, `analyze_screen_capture()` and `analyze_gaze_capture()`
- **Severity**: MEDIUM
- **Issue**: Both functions catch all exceptions generically with no retry. Transient failures (rate limits, timeouts, connection resets) cause immediate VLM failure and are not distinguished from permanent failures (auth errors).
- **Suggested fix**: Add 1–2 retries with exponential backoff for transient errors (`openai.RateLimitError`, `openai.Timeout`, `openai.APIConnectionError`). Fail immediately on permanent errors (`openai.AuthenticationError`).
- **Devil's advocate warning**: backoff retries during gaze polling can compound: 3 retries × 2s backoff × 20s timeout = 67s for one poll iteration, exceeding `gaze_timeout_s=60s`. **Cap total retry duration to stay within timeout budget** (e.g., max 1 retry during gaze polling, more during screen search).

---

### 2.7 Misty SDK Calls Lack Exception Handling

- **File**: `utils/expressions.py` (`show_image`, `arm_gesture`), `utils/head_control.py` (`look_at_screen`, `shake_head_only`)
- **Severity**: HIGH
- **Issue**: All `misty.perform_action()` calls are unprotected. A single network error (robot disconnects, Wi-Fi drops) crashes the entire pipeline with an unhandled exception.
- **Suggested fix**: Add targeted exception handling. For non-critical actions (face display, arm wave), log and continue. For critical actions (head positioning), log and propagate or retry.
- **Devil's advocate note**: Do NOT silently swallow all exceptions. If `look_at_screen()` fails, the gaze detection stage must be aware the head isn't in the right position. The exception handling strategy must be explicit about fallback behavior for each action type.

---

### 2.8 Busy-Wait Polling in `wait_for_start()`

- **File**: `utils/triggers.py`, lines 87–108
- **Severity**: LOW
- **Issue**: Uses `time.sleep(0.1)` busy-wait loop consuming ~10 CPU wakeups/sec per polling thread.
- **Suggested fix**: Replace with `threading.Condition` + `notify()` on event arrival. Alternatively, accept as-is — the CPU cost is negligible for a single polling thread and the simplicity benefit is real.

---

## Phase 3: REDUNDANCY ELIMINATION (Safe Cleanup)

### 3.1 Unused Vision Wrapper Functions

- **File**: `utils/vision.py`, end of file
- **Functions**: `vlm_is_facing_screen()`, `vlm_is_gazing()`, `capture_frame()`
- **Issue**: These 3 convenience functions are never called anywhere in the codebase (verified by grep). The pipeline uses the structured `FrameCaptureResult`/`VisionCheckResult` API directly.
- **Suggested action**: Delete all 3 functions. They are dead code with no callers.
- **Devil's advocate note**: Verify no external notebooks (`.ipynb`) or scripts outside this workspace import them.

---

### 3.2 Unused Trigger Methods

- **File**: `utils/triggers.py`
- **Methods**: `end_received()`, `has_end_event()`, `shutdown_received()`
- **Issue**: Never called in production code. `has_shutdown_event()` and `consume_interrupt()` are the active API.
- **Suggested action**: Delete all 3 methods. They add maintenance burden and confusing API surface.
- **Devil's advocate note**: `shutdown_received()` has a safety-critical name — confirm it's truly unused before deletion.

---

### 3.3 StubTrigger in Production Code

- **File**: `utils/triggers.py`, lines 171+
- **Issue**: `StubTrigger` is a test-only stub class living in production utility code.
- **Suggested action**: Move to `tests/` directory (e.g., `tests/conftest.py` or `tests/stubs.py`).

---

### 3.4 Unused `default_screen_position()` Function

- **File**: `stages/screen_watch.py`, line 24
- **Issue**: Function exists but is never called. Returns `ScreenPos` from config defaults.
- **Suggested action**: Delete if Item 1.3 (screen search fallback) doesn't need it. If Item 1.3 DOES use it as a fallback, KEEP it and remove from this list.
- **Cross-dependency**: Coordinate with Item 1.3 — if fallback uses default position, this function should be retained.

---

### 3.5 Redundant Audio Re-initialization

- **File**: `utils/audio.py`, `speak_text()` function
- **Issue**: `ensure_audio_ready()` is called on EVERY `speak_text()` invocation (4+ per session), re-enabling audio hardware and setting volume each time.
- **Suggested action**: Cache the result after first successful setup. Add a flag `_audio_ready` to skip subsequent calls unless a failure occurs. Reset flag on audio failure.
- **Devil's advocate note**: If the Misty robot loses audio state mid-session (e.g., after a crash/reboot), the cache would be stale. Add a reset mechanism that triggers on audio failure.

---

### 3.6 Server Signal Parsing Code Duplication

- **File**: `server.py`, `parse_message_signal()` function, lines ~115–180
- **Issue**: Contains 6+ repetitive `if coerce_bool(payload.get("..."))` blocks checking different JSON key names that all perform the same boolean → signal mapping. Separate alias sets and if-chains handle 12+ variations of re-engagement signals and disengagement signals.
- **Suggested action**: Consolidate into a single lookup structure:
  ```python
  DISENGAGEMENT_KEYS = {"disengaged", "posture_disengaged", "disengage", "covert_disengagement", "covert_disengagemnt"}
  REENGAGEMENT_KEYS = {"re_engagement", "re-engagement", "reengagement", "re_engament", "re-engament", "reengament"}
  ```
  Then iterate once over the payload keys instead of 12+ separate if-blocks.
- **Devil's advocate note**: Typo aliases (e.g., `covert_disengagemnt`) are intentional for backward compatibility with legacy clients. Ensure all variants are preserved in the lookup. Test with existing test suite which covers these aliases.

---

## Phase 4: CODE QUALITY IMPROVEMENTS (Nice-to-Have)

### 4.1 Config Validation Missing

- **File**: `config.py`
- **Issue**: No `__post_init__` validation. Invalid values (negative timeouts, zero max_attempts, empty prompts tuple) are silently accepted and only fail at runtime.
- **Suggested action**: Add a `__post_init__` method validating:
  - `gaze_timeout_s > 0`
  - `max_attempts >= 1`
  - `len(escalation_prompts) > 0`
  - `speech_volume` in range 0–100
  - `vision_jpeg_quality` in range 1–95

---

### 4.2 Hardcoded Head Movement Velocities

- **File**: `utils/head_control.py`
- **Issue**: `look_at_screen()` uses velocity 90, `shake_head_only()` uses velocity 80. These are hardcoded magic numbers, not configurable.
- **Suggested action**: Add `head_normal_velocity: int = 90` and `head_shake_velocity: int = 80` to `Config`. Use `cfg.head_normal_velocity` in `look_at_screen()`.
- **Devil's advocate note**: The different velocities (90 vs 80) may be intentional (faster for positioning, slower for head shake stimulus). Confirm with team before making configurable.

---

### 4.3 Unsafe `getattr()` Usage for Known Config Fields

- **File**: `utils/head_control.py` line 24, `stages/distraction.py` lines 19–21
- **Issue**: Uses `float(getattr(cfg, "shake_pause_s", 0.5))` pattern instead of direct attribute access despite `shake_pause_s` being defined in the frozen `Config` dataclass. These defensive getattr calls with fallback defaults mask potential config errors and reduce type safety.
- **Suggested action**: Replace with direct `cfg.shake_pause_s` access. If a field might not exist, it should be added to the Config dataclass, not worked around with getattr.

---

### 4.4 Test Coverage Gaps

- **Files**: `tests/` directory
- **Missing test coverage**:
  1. No test for `max_attempts` exhaustion behavior in pipeline
  2. No test for screen search retry limit / infinite loop prevention
  3. No test for concurrent SessionLog access (thread safety)
  4. No test for escalation prompt cycling (only prompt[0] is ever tested)
  5. No test for OpenAI API failure retry behavior
  6. No integration tests combining multiple stages end-to-end
  7. No test for `run_summary()` (summary generation + file save)
  8. No test for `_prepare_vision_frame()` with corrupt/empty image data
  9. No test for `ExternalSignalReceiver` under high-volume event load
- **Suggested action**: After implementing Phase 1–2 fixes, add targeted tests for each new behavior.

---

## Priority-Ordered Implementation Roadmap

| Priority | Item | File(s) | Risk Level |
|----------|------|---------|------------|
| **P0** | 1.1 Max attempts enforcement | `pipeline.py` | LOW |
| **P0** | 1.2 Fix escalation prompt indexing | `stages/no_response_prompt.py` | LOW |
| **P0** | 1.3 Screen search exhaustion handling | `pipeline.py` | MEDIUM |
| **P0** | 1.4 Combine duplicate stop handling | `pipeline.py` | LOW |
| **P0** | 1.5 Thread-safe SessionLog | `utils/session_log.py` | LOW |
| **P0** | 1.6 Fix voice_prompt_count semantics | `utils/session_log.py` | LOW |
| **P1** | 2.1 Monotonic clock consistency | `stages/distraction.py` | LOW |
| **P1** | 2.2 Shake thread exception handling | `utils/head_control.py` | MEDIUM |
| **P1** | 2.7 Misty SDK exception handling | `utils/expressions.py`, `utils/head_control.py` | MEDIUM |
| **P1** | 2.4 Bound event queues | `utils/triggers.py` | LOW |
| **P1** | 2.6 OpenAI retry logic | `utils/vision.py` | MEDIUM |
| **P1** | 2.5 Camera timeout reduction | `utils/vision.py` | MEDIUM |
| **P1** | 2.3 Shake thread join timeout log | `stages/distraction.py` | LOW |
| **P2** | 3.1 Delete unused vision wrappers | `utils/vision.py` | LOW |
| **P2** | 3.2 Delete unused trigger methods | `utils/triggers.py` | LOW |
| **P2** | 3.3 Move StubTrigger to tests | `utils/triggers.py` | LOW |
| **P2** | 3.4 Delete/retain default_screen_position | `stages/screen_watch.py` | LOW |
| **P2** | 3.5 Cache audio setup | `utils/audio.py` | LOW |
| **P2** | 3.6 Consolidate signal parsing | `server.py` | MEDIUM |
| **P3** | 4.1 Config validation | `config.py` | LOW |
| **P3** | 4.2 Configurable head velocities | `config.py`, `utils/head_control.py` | LOW |
| **P3** | 4.3 Replace getattr with direct access | multiple | LOW |
| **P3** | 4.4 Add missing tests | `tests/` | LOW |
| **P3** | 2.8 Replace busy-wait with Condition | `utils/triggers.py` | MEDIUM |

---

## Cross-Cutting Risks Identified by Devil's Advocate

1. **Item 1.3 ↔ Item 3.4 dependency**: If screen search fallback uses `default_screen_position()`, do NOT delete that function in Phase 3. Coordinate these changes.
2. **Item 2.6 timeout budget**: OpenAI retry backoff during gaze polling must not exceed `gaze_timeout_s` (60s). Cap retry duration per poll iteration (e.g., max 1 retry with 2s backoff during active gaze polling; more retries allowed during screen search).
3. **Item 2.7 exception strategy**: Catching all SDK exceptions silently would create new bugs. Critical actions (head positioning) must propagate or explicitly handle failures. Non-critical actions (face display) can log-and-continue.
4. **Item 2.5 camera timeout**: Reducing from 5s without measuring actual P95 camera latency may increase false failure rates. Measure before finalizing timeout value.
5. **Item 3.6 typo preservation**: Signal parsing consolidation must preserve ALL typo aliases (`covert_disengagemnt`, `re_engament`, etc.) for backward compatibility with legacy clients. The existing test suite in `test_server.py` covers these and must continue to pass.

---

## Conclusion

The codebase is functionally implemented and the core pipeline flow matches the `key_idea.md` specification. The primary correctness issues are:
- **3 infinite/unbounded loop risks** (screen search, max_attempts, escalation)
- **1 hardcoded index bug** (escalation prompts)
- **1 thread-safety gap** (SessionLog)
- **1 clock consistency issue** (mixed time sources)

These 6 issues represent the highest-impact fixes. Addressing them would bring the pipeline from "works in the happy path" to "reliable under edge cases." The redundancy and quality items are lower priority but will improve long-term maintainability.

All suggested changes are backward-compatible and should not break existing tests when properly implemented.
