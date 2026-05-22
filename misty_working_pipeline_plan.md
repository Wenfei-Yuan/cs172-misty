# Misty Working Pipeline Plan

> **Status:** Fully implemented.
> **Purpose:** Architecture reference for the Misty II companion behavior pipeline.
> Last updated to match actual codebase (2026-05-20).

---

## 1. Pipeline Overview

The Misty robot operates through the following stage loop:

| Stage | Trigger | Misty Behavior |
|---|---|---|
| **1. Boot-up** | Program launch | Reset arms down → speak greeting → VLM screen search → speak result; returns `screen_pos` |
| **2. Screen Watch** | Post-boot / post-recovery | Turn head to cached `screen_pos` (or re-search if `None`) → VLM alignment check → speak status (reading face) |
| **3. Wait for Signal** | After screen watch | Block on `signal_rx.wait_for_start()`; HTTP POST `/distraction/start` unblocks with optional `trigger_reason` |
| **4. Distraction Sequence** | Start signal received | Run `perform_distraction_start_sequence` in daemon thread: turn head to user → left-arm cue → return to screen; main thread polls `consume_interrupt()` for stop/shutdown |
| **5. Post-Sequence Decision** | Sequence outcome | `"stop"` → log end, loop; `"sequence_complete"` → wait `redirect_confirmation_wait_s` for stop; `"timeout"` → no-response prompt |
| **6. No-Response Prompt** | Timeout / post-sequence quiet wait | LLM generates focus reminder from `current_text`; speak + left-arm cue; track `attempt` |
| **7. Session Close** | Shutdown signal OR `max_attempts` exhausted | Speak summary + wave; save session JSON; regenerate CSVs |

> **No LED actions anywhere.** All `perform_action("led", ...)` are absent from the codebase.
>
> **Screen position is found once at boot** via VLM grid search and cached; subsequent screen-watch cycles reuse the cached `ScreenPos` directly.

---

## 2. Full Pipeline Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                  main.py                                     │
│  args = parse_args()  →  username = resolve_username(args)                   │
│  misty = _build_misty()  →  run(misty, load_config(username))                │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          pipeline.py  (orchestrator)                         │
│  session_id = generate_session_id(cfg.participant_id)                        │
│  log = SessionLog(session_id, cfg)                                           │
│  signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)        │
│  signal_rx.start()                                                           │
│  screen_pos = None   attempt = 0   screen_search_retries = 0                │
│  skip_screen_watch_once = False                                              │
│  ensure_audio_ready(misty, cfg)                                              │
│  screen_pos = run_bootup(misty, cfg, log)   ← STAGE 1                       │
│  bootup_screen_pos = screen_pos                                              │
└──┬───────────────────────────────────────────────────────────────────────────┘
   │
   │  ┌─────────────────────────────────────────────────────────────────────┐
   │  │  main loop  (while True)                                            │
   │  │                                                                     │
   │  │  if has_shutdown_event() → break                                    │
   │  │                                                                     │
   │  │  ── STAGE 2 ─────────────────────────────────────────────────      │
   │  │  unless skip_screen_watch_once:                                     │
   │  │    screen_pos = run_screen_watch(misty, cfg, log, screen_pos)       │
   │  │    if screen_pos is None:                                           │
   │  │      retries += 1                                                   │
   │  │      if retries >= 3: use default_screen_position(); retries = 0   │
   │  │      else: continue (retry)                                         │
   │  │                                                                     │
   │  │  ── STAGE 3 (wait) ────────────────────────────────────────────    │
   │  │  start_signal = signal_rx.wait_for_start()                          │
   │  │  if start_signal.event == "shutdown" → break                        │
   │  │  signal_rx.clear_redirect_stop()                                    │
   │  │                                                                     │
   │  │  ── STAGE 4 (distraction sequence) ─────────────────────────────   │
   │  │  distraction = run_distraction(misty, cfg, log, screen_pos,         │
   │  │                  consume_interrupt=signal_rx.consume_interrupt,     │
   │  │                  trigger_reason=start_signal.trigger_reason)        │
   │  │                                                                     │
   │  │  ── STAGE 5 (post-sequence decisions) ─────────────────────────    │
   │  │  if outcome == "stop":                                              │
   │  │    clear_redirect_stop → return_to_waiting → log end               │
   │  │    attempt=0, skip_screen_watch_once=True, continue                 │
   │  │  if outcome == "shutdown": break                                    │
   │  │  if outcome == "sequence_complete":                                 │
   │  │    check pending stop/shutdown first                                │
   │  │    wait up to redirect_confirmation_wait_s for stop                 │
   │  │      stop → log end, attempt=0, skip, continue                     │
   │  │      timeout → run_no_response + quiet_wait + continue              │
   │  │  if outcome == "timeout":                                           │
   │  │    attempt += 1                                                     │
   │  │    run_no_response(misty, cfg, log, attempt,                        │
   │  │                    current_text=signal_rx.current_text(),           │
   │  │                    stop_event=signal_rx.redirect_stop_event)        │
   │  │    if attempt >= cfg.max_attempts: break                            │
   │  └─────────────────────────────────────────────────────────────────────┘
   │
   │  finally:
   │    signal_rx.stop()
   │    run_summary(misty, cfg, log)   ← STAGE 7
   │
   ▼
  end
```

---

## 3. Modules and Files

### Root Level

| File | Role |
|---|---|
| `main.py` | Parse `--username` / `--participant` args; build Misty connection; call `pipeline.run()` |
| `config.py` | `@dataclass(frozen=True) Config`; `load_config(participant_id)` reads `.env` |
| `pipeline.py` | Top-level orchestrator; owns the main loop and all stage transitions |
| `generate_csv.py` | Regenerated after each session close |

### `stages/`

| File | Signature | Role |
|---|---|---|
| `bootup.py` | `run_bootup(misty, cfg, log) -> ScreenPos or None` | Reset arms down; speak greeting; VLM screen search; return `screen_pos` |
| `screen_watch.py` | `run_screen_watch(misty, cfg, log, screen_pos) -> ScreenPos or None` | Head to cached pos (or re-search); VLM alignment check; speak status; return updated `ScreenPos` |
| `distraction.py` | `run_distraction(misty, cfg, log, screen_pos, consume_interrupt, trigger_reason) -> DistractionResult` | Show `DISTRACTION_FACE`; run `perform_distraction_start_sequence` in daemon thread; poll `consume_interrupt()`; return `DistractionResult(outcome, sequence_completed, completion_latency_s)` |
| `no_response_prompt.py` | `run_no_response(misty, cfg, log, attempt, current_text, stop_event)` | LLM generates focus reminder from `current_text`; speak + left-arm cue; log event |
| `summary.py` | `run_summary(misty, cfg, log)` | Speak summary; arm wave; `log.save_to_file()`; `generate_csv.main()` |

### `utils/`

| File | Key Exports | Role |
|---|---|---|
| `audio.py` | `ensure_audio_ready()`, `speak_text()` | Thread-wrapped `speak()`; handles `stop_event` interrupt; returns `{interrupted, timed_out, ...}` |
| `expressions.py` | `BOOT/READING/SPEAKING/DISTRACTION/CLOSE_FACE`, `show_image()`, `arm_gesture()` | Face expression constants and typed wrappers; no LED |
| `focus_prompt.py` | `generate_focus_reminder_from_text(reading_text, cfg) -> FocusPromptResult` | GPT call (text model) → one gentle reminder sentence from reading context |
| `head_control.py` | `look_at_screen()`, `reset_arms_down()`, `acknowledge_gaze_recovery()`, `perform_distraction_start_sequence()`, `redirect_attention_to_screen()`, `cue_screen_with_left_arm()` | All head and arm motion helpers; every function is stoppable via `stop_event` |
| `openai_client.py` | `get_openai_client(cfg)` | Shared OpenAI client factory |
| `session_id.py` | `generate_session_id(participant_id) -> str` | e.g. `baseline_20260520_alice_ab12cd` |
| `session_log.py` | `SessionLog` | Structured event log; `record_distraction_start/result/end/voice_prompt`, `generate_summary()`, `save_to_file()` -> `sessions/<id>.json` |
| `triggers.py` | `ExternalSignalReceiver`, `StartSignalResult` | `ThreadingHTTPServer` on `signal_host:signal_port`; handles `/distraction/start`, `/distraction/stop`, `/shutdown`, `/current_text`; exposes `redirect_stop_event` for interrupting in-flight speech/motion |
| `vision.py` | `capture_frame_result()`, `analyze_screen_capture()`, `FrameCaptureResult`, `VisionCheckResult` | Camera capture via `GET /api/cameras/rgb`; resize/compress; GPT-4o vision call; returns `status` in `{aligned, visible, not_aligned, error}` |

---

## 4. Stage Details

### Stage 1 — Boot-up (`stages/bootup.py`)

```python
show_image(BOOT_FACE)                              # e_Joy.jpg
reset_arms_down(misty, cfg)
speak_text("Hello! I'm Misty. Let's focus today!")
speak_text("I'm looking for your screen now.")
log.record("screen_search_started")

try:
    search = find_screen_position_with_vlm(misty, cfg, log)
    screen_pos = search.position
except Exception:
    screen_pos = None  # fallback to default_screen_position(cfg)

if screen_pos:
    look_at_screen(misty, screen_pos)
    speak_text("I've found the screen!")
    speak_text("Let's start reading. You can open the browser extension and start reading now.")
else:
    speak_text("I'm still looking for the screen.")

show_image(SPEAKING_FACE)
log.record("bootup_complete")
return screen_pos
```

### Stage 2 — Screen Watch (`stages/screen_watch.py`)

```python
if screen_pos and cfg.cache_screen_pos:           # fast path
    show_image(READING_FACE)
    sleep(return_to_screen_pause_s)
    look_at_screen(misty, screen_pos)
    sleep(screen_settle_s)
    log.record("screen_cache_hit")
    return screen_pos

if screen_pos is None:
    search = find_screen_position_with_vlm(...)   # coarse + fine grid search
    position = search.position
else:
    look_at_screen(misty, screen_pos)

result = _check_screen_alignment(misty, cfg, log) # VLM: aligned/visible/not_aligned
if result.status != "aligned":
    retry or reuse screen_pos as fallback

speak("I found the screen..." or "I'm still adjusting...")
show_image(READING_FACE)
log.record_screen_observation(...)
return position
```

**VLM grid search** (`find_screen_position_with_vlm`): starts from
`(screen_search_seed_yaw, screen_search_seed_pitch)`, sweeps
`screen_search_yaw_offsets x screen_search_pitch_offsets`; if no `aligned`
position found, does a fine search around the best `visible` candidate.
Confirms alignment with `screen_alignment_confirm_checks` repeated checks.

### Stage 3 — Wait for Signal (`pipeline.py`)

```python
start_signal = signal_rx.wait_for_start()
# Returns StartSignalResult(event, stale_stop_events_cleared, trigger_reason)
if start_signal.event == "shutdown":
    break
signal_rx.clear_redirect_stop()
```

`ExternalSignalReceiver` HTTP endpoints:

- `POST /distraction/start` — optional JSON body `{"reason": "..."}` sets `trigger_reason`
- `POST /distraction/stop`
- `POST /shutdown`
- `POST /current_text` — body `{"text": "..."}` updates `current_text` used by no-response prompt

### Stage 4 — Distraction Sequence (`stages/distraction.py`)

```python
log.record_distraction_start(trigger_reason)
show_image(DISTRACTION_FACE)                      # e_Concerned.jpg

motion_thread = Thread(target=perform_distraction_start_sequence, daemon=True)
motion_thread.start()

# Main thread polls for interrupts while motion runs:
while not motion_done:
    interrupt = consume_interrupt()
    if interrupt == "shutdown": outcome = "shutdown"; break
    if interrupt == "stop":     outcome = "stop";     break
    if elapsed >= gaze_timeout_s: outcome = "timeout"; break
    motion_done.wait(timeout=0.1)
else:
    outcome = "sequence_complete"

stop_motion.set(); motion_thread.join()
if outcome == "timeout":
    look_at_screen(misty, screen_pos)
log.record_distraction_result(outcome, ...)
return DistractionResult(outcome, sequence_completed, completion_latency_s)
```

**`perform_distraction_start_sequence`** (`utils/head_control.py`):

1. Turn head to user (`distraction_user_turn_yaw_deg`, default -45 deg)
2. Hold for `distraction_user_focus_pause_s`
3. Turn head back to screen position
4. Left-arm cue (`distraction_left_arm_repetitions` reps: arm up -> hold -> arm down)
5. Settle `redirect_settle_s`

Each step checks `stop_event` via `_wait_or_stop()`; on early exit, `_recover()`
snaps head+arms back to screen position at maximum velocity.

### Stage 5 — Post-Sequence Decisions (`pipeline.py`)

| `outcome` | Action |
|---|---|
| `"stop"` | `clear_redirect_stop` -> `_return_to_waiting_position` -> `log.record_distraction_end()` -> `attempt = 0` -> `skip_screen_watch_once = True` -> `continue` |
| `"shutdown"` | `_close_active_distraction(log, "shutdown")` -> `break` |
| `"sequence_complete"` | Check pending stop/shutdown; then `wait_for_stop_or_shutdown_only(redirect_confirmation_wait_s)` -> stop: log end + loop; timeout: `run_no_response` + `quiet_wait` + loop |
| `"timeout"` | `attempt += 1` -> `run_no_response(...)` -> if `attempt >= max_attempts`: `break` |

### Stage 6 — No-Response Prompt (`stages/no_response_prompt.py`)

```python
show_image(SPEAKING_FACE)
dynamic = generate_focus_reminder_from_text(current_text, cfg)
# GPT call (text_model, temperature=0.5):
#   "Generate one gentle focus reminder based on this reading passage."
#   Falls back to generic reminder if current_text is empty.

speak_text(misty, cfg, dynamic.reminder, stop_event=stop_event)
cue_screen_with_left_arm(misty, cfg, repetitions=1, stop_event=stop_event)
log.record("no_response_prompt_generated", attempt, ...)
log.record_voice_prompt()
```

Each step checks `stop_event`; if set, returns early and logs `"no_response_skipped"`.

### Stage 7 — Session Close (`stages/summary.py`)

```python
summary_text = log.generate_summary()
show_image(SPEAKING_FACE)
speak_text(misty, cfg, summary_text)
show_image(CLOSE_FACE)           # e_Joy.jpg
arm_gesture(misty, "wave")
log.save_to_file()               # -> sessions/<session_id>.json
generate_csv.main()              # regenerate analysis CSVs (best-effort)
```

---

## 5. Thread Model

```
Main thread:
  pipeline.run() — sequential stage calls
  Stage 4: polls consume_interrupt() at 0.1s intervals
           while daemon motion thread runs

Daemon thread (per distraction episode):
  perform_distraction_start_sequence()
  head_move + arms_move choreography
  each step calls _wait_or_stop(stop_event, duration)

Background thread (ExternalSignalReceiver):
  ThreadingHTTPServer on cfg.signal_host:cfg.signal_port
  Queues events: "start" / "stop" / "shutdown"
  Updates current_text on /current_text POST
  Sets redirect_stop_event on "stop" / "shutdown"
```

---

## 6. Face Expression Constants

| Constant | File | Usage |
|---|---|---|
| `BOOT_FACE` | `e_Joy.jpg` | Startup greeting |
| `READING_FACE` | `e_EyesWide.jpg` | Screen-watch idle state |
| `SPEAKING_FACE` | `e_ContentDefault.jpg` | Whenever Misty is speaking |
| `DISTRACTION_FACE` | `e_Concerned.jpg` | During distraction sequence |
| `CLOSE_FACE` | `e_Joy.jpg` | Session close encouragement |

---

## 7. Configuration (`config.py`)

All fields are on `@dataclass(frozen=True) Config`. Selected fields:

```python
# Core
ip: str
openai_api_key: str
participant_id: str

# Models
vision_model: str = "gpt-4o"         # used by vision.py (screen alignment)
text_model: str = "gpt-4o-mini"      # used by focus_prompt.py

# Camera / Vision
camera_timeout_s: float = 1.5
vision_max_image_dim_px: int = 768
vision_jpeg_quality: int = 72
openai_timeout_s: float = 6.0
openai_max_retries: int = 0

# Audio
speech_volume: int = 30
speech_timeout_s: float = 8.0

# Screen search
screen_search_seed_yaw: float = 0.0
screen_search_seed_pitch: float = 0.0
screen_search_yaw_offsets: tuple = (0.0, -10.0, 10.0, -20.0, 20.0, -30.0, 30.0)
screen_search_pitch_offsets: tuple = (0.0, -8.0, 8.0, -15.0, 15.0)
screen_search_fine_yaw_offsets: tuple = (0.0, -5.0, 5.0, -10.0, 10.0)
screen_search_fine_pitch_offsets: tuple = (0.0, -4.0, 4.0, -8.0, 8.0)
screen_alignment_confirm_checks: int = 2
screen_alignment_confirm_settle_s: float = 0.4
screen_settle_s: float = 0.7
cache_screen_pos: bool = True
return_to_screen_pause_s: float = 2.0

# Distraction sequence
gaze_timeout_s: float = 45.0            # max elapsed time before "timeout" outcome
distraction_user_turn_yaw_deg: float = -45.0
distraction_user_focus_pause_s: float = 2.0
distraction_left_arm_repetitions: int = 2
distraction_both_arms_down_deg: int = 80
distraction_both_arms_velocity: int = 110

# Redirect (post-sequence)
redirect_confirmation_wait_s: float = 60.0
redirect_left_arm_repetitions: int = 1
redirect_screen_focus_pause_s: float = 1.5
redirect_settle_s: float = 1.5
redirect_nod_action_name: str = "head-down-up-nod"
redirect_nod_action_wait_s: float = 1.2
redirect_action_timeout_s: float = 10.0

# Session
max_attempts: int = 3
signal_host: str = "127.0.0.1"
signal_port: int = 5050
```

`.env` must contain:
```
MISTY_IP_ADDRESS=<robot IP>
OPENAI_API_KEY=<your key>
```

---

## 8. Launch

```bash
# With --username (preferred)
python main.py --username alice

# Legacy alias
python main.py --participant alice
```

If no flag is passed, the program prompts interactively: `Enter username:`.

---

## 9. Known Risks and Notes

| # | Issue | Severity | Resolution |
|---|---|---|---|
| **H1** | VLM screen alignment check adds latency; screen may be mis-classified if lighting changes | HIGH | `screen_alignment_confirm_checks=2` re-verifies before accepting; fast path uses cache on repeat cycles |
| **H2** | `perform_distraction_start_sequence` must recover to screen position if `stop_event` fires mid-motion | HIGH | Every `_sleep()` call internally uses `_wait_or_stop()`; `_recover()` snaps back at max velocity |
| **H3** | OpenAI errors during screen search could stall boot | HIGH | `try/except` in `run_bootup` falls back to `default_screen_position(cfg)`; VLM errors return `VisionCheckResult(ok=False)` |
| **H4** | `redirect_confirmation_wait_s` (default 60s) means Misty waits up to 1 min after sequence | HIGH | Tunable in Config; browser extension sends `/distraction/stop` as soon as user refocuses |
| **M1** | `current_text` may be stale if `/current_text` POST has not arrived | MEDIUM | `generate_focus_reminder_from_text` falls back to a generic reminder when text is empty |
| **M2** | `sessions/` directory may not exist on first run | MEDIUM | `SessionLog.save_to_file()` calls `os.makedirs(..., exist_ok=True)` |
| **M3** | Stale `/distraction/stop` events from a prior episode could suppress a new distraction | MEDIUM | `wait_for_start()` drains stale stop events; count reported via `stale_stop_events_cleared` |
| **L1** | Head angle drift over many cycles (robot firmware) | LOW | All `head_move` calls use absolute `Yaw`/`Pitch`; never relative offsets |
| **L2** | `generate_csv.main()` at session end may fail if CSV schema changes | LOW | Wrapped in `try/except`; failure is logged and does not affect session JSON save |

---

*Plan updated: 2026-05-20 (revision 3)*
*Changes from revision 2: status -> fully implemented; Stage 2 now only checks VLM alignment (no screen content analysis); Stage 3 distraction replaced VLM gaze polling + head-shake with physical choreography (head turn to user + left-arm cue) in a daemon thread; outcomes are stop/shutdown/sequence_complete/timeout; added Stage 5 post-sequence decision table; Stage 6 uses LLM-generated context-aware prompt (focus_prompt.py) not static escalation_prompts; new utils: audio.py, focus_prompt.py, openai_client.py; ExternalSignalReceiver gains /shutdown and /current_text endpoints + redirect_stop_event; Config expanded with vision/screen-search/redirect params; removed VLM gaze detection section (no longer used).*
