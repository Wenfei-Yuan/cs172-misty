# Misty Working Pipeline Plan

> **Status:** Planning only — no code changes made yet.  
> **Purpose:** Architecture plan for the full Misty II companion behavior pipeline.  
> Generated via multi-agent code workflow (focus, broad, free modes + senior staff review + devil's advocate).

---

## 1. Pipeline Overview

The Misty robot operates through the following six-stage pipeline loop:

| Stage | Trigger | Misty Behavior |
|---|---|---|
| **1. Boot-up** | Program launch | Speak greeting + face expression (joy) + arm gesture; generate session ID |
| **2. Screen Monitor** | Post-boot / post-recovery | Turn head to cached screen position → capture image → analyze with Vision API → respond with speech (reading face expression) |
| **3. Distraction Response** | **External signal** from distraction measurement tool (HTTP POST) | Head-only shake (no arms); start periodic VLM gaze analysis loop; log distraction start time |
| **4. Gaze Recovery** | VLM analysis confirms participant is gazing at robot | Stop shaking → turn head to screen → optional speech (speaking face) → re-run screen monitor; log gaze latency |
| **5. No-Response Escalation** | Gaze timeout expires | Speak prompt to user; track attempt count; log voice prompt used |
| **6. Session Close** | External end signal OR max attempts | Speak summary + encouragement; save session JSON log |

Stages 2 → 3 → 4 (or 5) form a **repeating loop** until end signal or `max_attempts` is exhausted.

> **No LED actions anywhere in the pipeline.** All light-based feedback is removed.
>
> **Screen position is calibrated once** (Stage 2, first run) and cached; subsequent turns to screen use the cached angle directly without re-calibration.

---

## 2. Full Pipeline Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                  main.py                                    │
│  Misty(ip) ──────────────────────────────────► pipeline.run(my_misty, cfg) │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          pipeline.py  (orchestrator)                        │
│  cfg = config.load_config()                                                 │
│  session_id = generate_session_id()   log = SessionLog(session_id, cfg)    │
│  validate: cfg.ip reachable, cfg.openai_api_key present                    │
│  screen_pos = None   ← cached after first calibration                      │
└──┬──────────────────────────────────────────────────────────────────────────┘
   │
   │  STAGE 1
   ▼
┌──────────────────────────────────────────────────────────┐
│  stages/bootup.py  run_bootup(misty, cfg, log)           │
│                                                          │
│  show_image(misty, "e_Joy.jpg")  [joy face — greeting]   │
│  arm_gesture(misty, "wave")      perform_action(         │
│                                    "arms_move")          │
│  speak(misty, "Hello! Let's work") basic_skills.speak()  │
│  show_image(misty, "e_Content...")[neutral/content face] │
│  log.record("bootup_complete")                           │
└───────────────────────────────┬──────────────────────────┘
                                │
                                │ loop start (attempt counter reset)
                                ▼
┌──────────────────────────────────────────────────────────┐
│  stages/screen_watch.py  run_screen_watch(misty,cfg,log, │
│                                           screen_pos)    │
│                                                          │
│  [1] show_image(misty, READING_FACE)                     │
│       e.g. "e_EyesWide.jpg"  ← reading/focused face     │
│  [2] if screen_pos is None:                              │
│       screen_pos = calibrate_screen(misty, cfg)          │
│         → head_move(Yaw, Pitch) + manual confirm         │
│       else:                                              │
│       head_move(Yaw=screen_pos.yaw,                      │
│                 Pitch=screen_pos.pitch, Velocity=50)     │
│  [3] capture_screen(misty)                               │
│       get_info("picture_rgb") → base64 JPEG              │
│  [4] analyze_screen(b64, cfg)                            │
│       openai.OpenAI().chat.completions.create(           │
│         model="gpt-4o", image=b64_data_uri)              │
│       try/except: APIError → fallback_str                │
│  [5] speak(misty, response_text)                         │
│       show_image(misty, SPEAKING_FACE) before speak      │
│       show_image(misty, READING_FACE)  after speak       │
│  log.record("screen_observed", analysis)                 │
│  return screen_pos                                       │
└───────────────────────────────┬──────────────────────────┘
                                │
                                │  wait for distraction start signal
                                ▼
         ┌──────────────────────────────────────────────────┐
         │  triggers.py — ExternalSignalReceiver            │
         │  HTTP server (localhost:PORT) listening for:      │
         │    POST /distraction/start  → start event        │
         │    POST /distraction/stop   → end event          │
         │  wait_for_start() blocks until start POST arrives│
         │  Returns True (distraction) or False (shutdown)  │
         └─────────────────────┬────────────────────────────┘
                               │ start signal received
                               │
                               │  STAGE 3
                               ▼
┌──────────────────────────────────────────────────────────┐
│  stages/distraction.py  run_distraction(misty, cfg, log, │
│                                          screen_pos)     │
│                                                          │
│  [1] log.record_distraction_start()  ← timestamp now    │
│  [2] show_image(misty, DISTRACTION_FACE)                 │
│       e.g. "e_Concerned.jpg"                             │
│  [3] stop_shake = threading.Event()                      │
│       shake_thread = Thread(target=shake_head_only,      │
│                    args=(misty, cfg, stop_shake))        │
│       shake_thread.start()   ← HEAD ONLY, no arms        │
│  [4] VLM gaze polling loop (main thread):                │
│       poll_start = time.time()                           │
│       gaze_seen = False                                  │
│       while not gaze_seen:                               │
│         if time.time()-poll_start > cfg.gaze_timeout_s:  │
│           break                                          │
│         b64 = capture_frame(misty)                       │
│         gaze_seen = vlm_is_gazing(b64, cfg)              │
│           → GPT-4o: "Is person looking at camera/robot?"│
│           → returns bool                                 │
│         time.sleep(cfg.gaze_poll_interval_s)             │
│  [5] stop_shake.set(); shake_thread.join()               │
│  [6] # NO center_head — go directly to screen pos        │
│  [7] log.record_distraction_gaze(gaze_seen, elapsed_s)   │
│                                                          │
│  Returns: gaze_seen (bool)                               │
└──────────────────┬───────────────────┬───────────────────┘
                   │                   │
          gaze     │                   │  gaze timeout
          seen     │                   │  (no response)
                   ▼                   ▼
   ┌───────────────────────┐  ┌────────────────────────────────┐
   │  STAGE 4 (RECOVERY)   │  │  STAGE 5 (ESCALATION)          │
   │  [inline in pipeline] │  │  stages/no_response_prompt.py  │
   │                       │  │                                │
   │  show_image(SPEAKING) │  │  attempt += 1                  │
   │  speak(misty, opt.)   │  │  show_image(SPEAKING_FACE)     │
   │  show_image(READING)  │  │  speak(misty, prompt_text)     │
   │  → head_move to       │  │  log.record_voice_prompt(      │
   │    screen_pos         │  │             attempt)           │
   │  → screen_watch()     │  │                                │
   │  attempt = 0          │  │  if attempt >= cfg.max_attempts│
   │  log.record(          │  │    → EXIT LOOP                 │
   │   "gaze_recovered")   │  │                                │
   └────────┬──────────────┘  └────────────┬───────────────────┘
            │                              │
            │                              │
            └──────────────────────────────┘
                          │
          ┌───────────────┴─────────────────┐
          │  ExternalSignalReceiver also     │
          │  listens for /distraction/stop   │
          │  → log.record_distraction_end()  │
          │  → compute duration_s            │
          └───────────────┬─────────────────┘
                          │  loop back to screen_watch (or exit)
                          │
                          │  STAGE 6
                          ▼
┌──────────────────────────────────────────────────────────┐
│  stages/summary.py  run_summary(misty, cfg, log)         │
│                                                          │
│  summary_text = log.generate_summary()                   │
│  show_image(misty, SPEAKING_FACE)                        │
│  speak(misty, summary_text + encouragement)              │
│  show_image(misty, "e_Joy.jpg")                          │
│  log.save_to_file()  → sessions/<session_id>.json        │
└──────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FACE EXPRESSION CONSTANTS

  READING_FACE    = "e_EyesWide.jpg"       ← screen-watch mode
  SPEAKING_FACE   = "e_ContentDefault.jpg" ← when speaking to participant
  DISTRACTION_FACE= "e_Concerned.jpg"      ← during distraction/shake
  BOOT_FACE       = "e_Joy.jpg"            ← greeting at startup
  CLOSE_FACE      = "e_Joy.jpg"            ← session close encouragement

SHARED UTILITIES (called by stages)

  utils/expressions.py  → show_image(), arm_gesture()  [NO led()]
  utils/head_control.py → look_at_screen(screen_pos), shake_head_only(stop_event)
  utils/vision.py       → capture_screen(misty), analyze_screen(b64, cfg) → str
                          vlm_is_gazing(b64, cfg) → bool  ← new
  utils/session_log.py  → SessionLog: record_distraction_start/gaze/end/voice_prompt,
                                       generate_summary(), save_to_file()
  utils/triggers.py     → ExternalSignalReceiver (HTTP), StubTrigger

THREAD DIAGRAM

  Main thread:
    pipeline.run() → sequential stage calls
    distraction: main thread polls VLM gaze analysis in loop

  Daemon thread A (during shake):
    shake_head_only → head_move(Yaw=±N) × loop | stopped via stop_shake.Event
    NO arm movements

  Background thread B (ExternalSignalReceiver):
    HTTP server → listens for /distraction/start and /distraction/stop POSTs
    sets start_event / stop_event respectively
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 3. New Scripts / Modules Required

### Root Level

| File | Status | Role |
|---|---|---|
| `main.py` | **EXISTING** (extend by +2 lines only) | Load env + Misty client; call `pipeline.run(my_misty, cfg)` |
| `config.py` | **NEW** | `@dataclass(frozen=True)` Config; `load_config()` from `.env` |
| `pipeline.py` | **NEW** | Top-level orchestrator; owns the stage loop |

### `stages/` Directory (NEW)

| File | Function | Role |
|---|---|---|
| `stages/__init__.py` | — | Package marker |
| `stages/bootup.py` | `run_bootup(misty, cfg, log)` | Greeting speech + face expression (joy) + arm wave; no LED |
| `stages/screen_watch.py` | `run_screen_watch(misty, cfg, log, screen_pos) → ScreenPos` | Head to cached screen pos (or calibrate on first call) → reading face → capture → analyze → speak (speaking face) → return to reading face |
| `stages/distraction.py` | `run_distraction(misty, cfg, log, screen_pos) → bool` | Head-shake only (threaded); VLM gaze polling on main thread; log timings |
| `stages/no_response_prompt.py` | `run_no_response(misty, cfg, log, attempt)` | Speaking face + speak escalation prompt; log voice_prompt_used |
| `stages/summary.py` | `run_summary(misty, cfg, log)` | Speaking face + speak summary + encouragement; save session JSON |

### `utils/` Directory (NEW)

| File | Contents | Role |
|---|---|---|
| `utils/__init__.py` | — | Package marker |
| `utils/expressions.py` | `show_image(misty, filename)`, `arm_gesture(misty, preset)` | Typed wrappers for face expression display and arms; **no LED** |
| `utils/head_control.py` | `look_at_screen(misty, screen_pos)`, `shake_head_only(misty, cfg, stop_event)` | Head movement helpers only; shake HEAD ONLY, no arms; no `center_head` |
| `utils/vision.py` | `capture_frame(misty) → b64`, `analyze_screen(b64, cfg) → str`, `vlm_is_gazing(b64, cfg) → bool` | Camera capture; GPT-4o screen analysis; GPT-4o gaze detection ("Is person looking at robot?") |
| `utils/session_log.py` | `SessionLog(session_id, cfg)`: `record_distraction_start()`, `record_distraction_gaze(gaze_seen, latency_s)`, `record_distraction_end()`, `record_voice_prompt(attempt)`, `generate_summary() → str`, `save_to_file()` | Full structured session log; saves to `sessions/<session_id>.json` |
| `utils/triggers.py` | `ExternalSignalReceiver` (HTTP server), `StubTrigger` | HTTP trigger for external distraction tool; stub for testing |
| `utils/session_id.py` | `generate_session_id() → str` | Generates UUID-based session ID (e.g., `session_20260321_abc123`) |

---

## 4. misty2py API Mapping (Per Stage)

> **No LED calls anywhere.** All `perform_action("led", ...)` are removed from every stage.

### Stage 1 — Boot-up
```python
from misty2py.basic_skills.speak import speak

# Joy face at greeting
misty.perform_action("image_show", {"FileName": "e_Joy.jpg"})
# Arm wave (arms only used here at boot-up)
misty.perform_action("arms_move",  {"LeftArmPosition": -80, "RightArmPosition": -80,
                                    "LeftArmVelocity": 50,  "RightArmVelocity": 50})
speak(misty, "Hello! I'm Misty. Let's focus today!")
# Settle into neutral/content face after greeting
misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})
```

### Stage 2 — Screen Watch
```python
# 1. Switch to reading/focused face expression
misty.perform_action("image_show", {"FileName": "e_EyesWide.jpg"})

# 2. Turn head toward screen using cached position
#    screen_pos is a ScreenPos(yaw, pitch) namedtuple
#    On first call: interactively calibrate; on subsequent calls: use cached values
misty.perform_action("head_move", {"Yaw": screen_pos.yaw,
                                   "Pitch": screen_pos.pitch,
                                   "Velocity": 50})

# 3. Capture image from Misty camera
resp = misty.get_info("picture_rgb")
b64  = resp.parse_to_dict()["result"]["base64"]   # verify key path on real robot

# 4. Analyze screen content (OpenAI Vision — sync client)
import openai
client = openai.OpenAI(api_key=cfg.openai_api_key, timeout=30)
try:
    result = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": [
            {"type": "text",
             "text": "Describe what you see on this computer screen in one sentence."},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]}]
    )
    analysis = result.choices[0].message.content
except Exception:
    analysis = "I couldn't analyze the screen right now."

# 5. Respond with speaking face, then return to reading face
misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})  # speaking
speak(misty, analysis)
misty.perform_action("image_show", {"FileName": "e_EyesWide.jpg"})         # back to reading
```

### Stage 3 — Distraction
```python
import threading, time

# 1. Log distraction start timestamp
log.record_distraction_start()

# 2. Switch to distraction/concerned face
misty.perform_action("image_show", {"FileName": "e_Concerned.jpg"})

# 3. Start head-shake in background thread — HEAD ONLY, no arm movements
stop_shake = threading.Event()
shake_thread = threading.Thread(
    target=shake_head_only, args=(misty, cfg, stop_shake), daemon=True)
shake_thread.start()

# 4. VLM gaze polling loop on main thread
client = openai.OpenAI(api_key=cfg.openai_api_key, timeout=20)
poll_start = time.time()
gaze_seen = False
gaze_latency = None
while not gaze_seen:
    elapsed = time.time() - poll_start
    if elapsed > cfg.gaze_timeout_s:
        break
    try:
        resp = misty.get_info("picture_rgb")
        b64  = resp.parse_to_dict()["result"]["base64"]
        result = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text",
                 "text": "Is the person in this image looking directly at the camera "
                         "(i.e., making eye contact)? Answer only yes or no."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]}]
        )
        answer = result.choices[0].message.content.strip().lower()
        if answer.startswith("yes"):
            gaze_seen = True
            gaze_latency = time.time() - poll_start
    except Exception:
        pass   # skip failed frame, continue polling
    time.sleep(cfg.gaze_poll_interval_s)

# 5. Stop shake thread
stop_shake.set()
shake_thread.join()

# 6. Turn head directly to screen (no center_head — skip to screen position)
misty.perform_action("head_move", {"Yaw": screen_pos.yaw,
                                   "Pitch": screen_pos.pitch,
                                   "Velocity": 50})

# 7. Log result
log.record_distraction_gaze(gaze_seen, gaze_latency)
```

### `shake_head_only()` — head movement only, no arms
```python
def shake_head_only(misty, cfg, stop_event):
    while not stop_event.is_set():
        misty.perform_action("head_move",
            {"Yaw": cfg.shake_amplitude_deg, "Velocity": 80})
        time.sleep(cfg.shake_period_s)
        if stop_event.is_set(): break
        time.sleep(cfg.shake_pause_s)
        if stop_event.is_set(): break
        misty.perform_action("head_move",
            {"Yaw": -cfg.shake_amplitude_deg, "Velocity": 80})
        time.sleep(cfg.shake_period_s)
        if stop_event.is_set(): break
        time.sleep(cfg.shake_pause_s)
```

### Stage 4 — Recovery (inline in pipeline.py)
```python
if gaze_seen:
    misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})  # speaking
    speak(misty, "I see you! Let me check what you were working on.")
    log.record("gaze_recovered")
    attempt = 0
    # head is already pointing at screen — run screen_watch directly
    screen_pos = run_screen_watch(misty, cfg, log, screen_pos)
```

### Stage 5 — No Response
```python
misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})  # speaking
speak(misty, escalation_prompts[attempt % len(escalation_prompts)])
log.record_voice_prompt(attempt)
```

### Stage 6 — Summary
```python
misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})  # speaking
summary_text = log.generate_summary()
speak(misty, summary_text)
misty.perform_action("image_show", {"FileName": "e_Joy.jpg"})             # close on joy
log.save_to_file()   # writes sessions/<session_id>.json
```

---

## 5. Configuration (`config.py`)

```python
from dataclasses import dataclass
from misty2py.utils.env_loader import EnvLoader

@dataclass(frozen=True)
class Config:
    ip: str
    openai_api_key: str
    participant_id: str       # set per experiment run (e.g., "P01")

    # Timing
    gaze_timeout_s: float = 60.0       # seconds of VLM polling before escalating
    gaze_poll_interval_s: float = 2.0  # seconds between VLM gaze frames

    # Behavior
    max_attempts: int = 3              # max no-response attempts before session close
    shake_amplitude_deg: int = 75      # degrees left/right for head shake (head only)
    shake_period_s: float = 0.6        # seconds used to reach each end position
    shake_pause_s: float = 0.5         # seconds to hold at the left/right endpoints

    # External signal receiver
    signal_host: str = "127.0.0.1"
    signal_port: int = 5050            # ExternalSignalReceiver listens on this port

    # Escalation speech pool
    escalation_prompts: tuple = (
        "Hey, are you still with me? Don't forget to focus!",
        "I noticed you're distracted. Take a breath and get back to it!",
        "Time to refocus — you're almost there. You've got this!",
    )

def load_config(participant_id: str) -> "Config":
    e = EnvLoader()
    ip  = e.get_ip()
    key = e.values.get("OPENAI_API_KEY", "")
    return Config(ip=ip, openai_api_key=key, participant_id=participant_id)
```

`.env` must contain:
```
MISTY_IP_ADDRESS=<robot IP>
OPENAI_API_KEY=<your key>
```

`participant_id` is passed as a command-line argument when launching `main.py`:
```bash
python main.py --participant P01
```

---

## 6. `main.py` Extension (Non-Breaking)

The existing 9 lines remain **completely untouched**. Lines are appended for argument parsing and pipeline launch:

```python
# ---- existing lines (unchanged) ----
from misty2py.robot import Misty
from misty2py.utils.env_loader import EnvLoader

env_loader = EnvLoader()
my_misty = Misty(env_loader.get_ip())

# Example: get device info to verify connection
#response = my_misty.get_info("device")
#print(response.parse_to_dict())
# -------------------------------------

import argparse                        # ← new
from config import load_config         # ← new
from pipeline import run               # ← new

parser = argparse.ArgumentParser()
parser.add_argument("--participant", required=True,
                    help="Participant ID, e.g. P01")
args = parser.parse_args()             # ← new

run(my_misty, load_config(args.participant))   # ← new
```

---

## 7. `pipeline.py` Pseudocode

```python
from utils.session_id import generate_session_id

def run(misty: Misty, cfg: Config):
    # Startup validation
    if not cfg.ip:
        raise EnvironmentError("MISTY_IP_ADDRESS not set in .env")
    if not cfg.openai_api_key:
        raise EnvironmentError("OPENAI_API_KEY not set in .env")

    session_id = generate_session_id(cfg.participant_id)
    log = SessionLog(session_id, cfg)

    # External distraction signal receiver (HTTP)
    signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)
    signal_rx.start()   # starts background HTTP server thread

    run_bootup(misty, cfg, log)

    screen_pos = None   # will be set on first screen_watch call
    attempt = 0

    while True:
        screen_pos = run_screen_watch(misty, cfg, log, screen_pos)

        # Wait for distraction START signal from external tool
        signal = signal_rx.wait_for_start()
        if not signal:
            break  # shutdown command received

        gaze_seen = run_distraction(misty, cfg, log, screen_pos)

        # Also listen for distraction END signal (may arrive concurrently)
        if signal_rx.end_received():
            log.record_distraction_end()

        if gaze_seen:
            misty.perform_action("image_show", {"FileName": "e_ContentDefault.jpg"})
            speak(misty, "I see you! Let me check what you were working on.")
            log.record("gaze_recovered")
            attempt = 0
            # head is already at screen_pos — loop back to screen_watch
        else:
            attempt += 1
            run_no_response(misty, cfg, log, attempt)
            if attempt >= cfg.max_attempts:
                break  # session over

    signal_rx.stop()
    run_summary(misty, cfg, log)
```

---

## 8. New Dependencies Required

| Package | Version | Purpose |
|---|---|---|
| `misty2py` | 5.0.3 (installed) | Robot SDK |
| `python-dotenv` | latest | `.env` loading (already used by misty2py) |
| `openai` | >=1.0.0 | GPT-4o Vision API for screen analysis AND gaze detection (sync client) |
| `requests` | (already installed) | HTTP (already used by misty2py) |
| `websocket-client` | (already installed) | WebSocket (already used by misty2py) |

> **Removed:** `pyee` is no longer needed — the new gaze detection approach uses VLM polling on the main thread instead of WebSocket event emitters.

Install command:
```bash
pip install openai
```

---

## 9. Known Risks and Implementation Notes

These issues were identified during planning (devil's advocate review). Updated after requirement changes:

| # | Issue | Severity | Resolution |
|---|---|---|---|
| **H1** | VLM gaze polling adds ~2–5s latency per frame; participant may look away between polls | **HIGH** | Tune `cfg.gaze_poll_interval_s` (default 2s). If too slow, reduce interval or cache camera frames |
| **H2** | `shake_head_only` runs on daemon thread; must not issue `arms_move` or any non-head action | **HIGH** | Strictly limit `shake_head_only()` to only `head_move` calls — no arm actions anywhere in Stage 3 |
| **H3** | OpenAI Vision API called twice per distraction cycle (screen analyze + gaze polling) → cost/rate limits | **HIGH** | Gaze polling uses separate client with short timeout; add retry cap; log API errors without crashing |
| **H4** | Screen position calibration must happen exactly once; cached value must persist across loop cycles | **HIGH** | `screen_pos` is a module-level variable in `pipeline.py`; `run_screen_watch` returns updated value only on first call |
| **M1** | ExternalSignalReceiver HTTP server may miss /stop if it arrives during VLM polling | **MEDIUM** | Signal receiver buffers events in a queue; `end_received()` checks the queue after gaze loop |
| **M2** | GPT-4o "yes/no" gaze answer may include surrounding text; string parsing must be robust | **MEDIUM** | Check `.startswith("yes")` on lowercased, stripped response; add fallback to False on parse failure |
| **M3** | Missing `OPENAI_API_KEY` crashes mid-session | **MEDIUM** | Validate at startup in `pipeline.run()` before any stage runs |
| **M4** | `participant_id` not provided → `argparse` will exit with clear error message | **MEDIUM** | `required=True` in argparse handles this cleanly |
| **L1** | Head absolute angle drift over many cycles (robot firmware issue) | **LOW** | Use absolute `Yaw`/`Pitch` values always; add Velocity parameter to ensure smooth motion |
| **L2** | `sessions/` directory may not exist on first run | **LOW** | `session_log.save_to_file()` calls `os.makedirs("sessions", exist_ok=True)` before writing |

---

## 10. Gaze Detection: VLM-Based Analysis

**Decision: Use GPT-4o Vision to determine if participant is gazing at the robot.**

`FaceDetection` (misty2py WebSocket stream) only detects whether a face is *present* in frame — it cannot determine whether the participant is actually *looking at* the robot. This is insufficient for the experimental requirement.

### Approach

During Stage 3 (distraction), Misty's camera periodically captures a frame. The frame is sent to GPT-4o with the prompt:

> *"Is the person in this image looking directly at the camera (i.e., making eye contact)? Answer only yes or no."*

If the answer is `"yes"`, gaze is confirmed and Stage 4 (recovery) begins.

| Approach | FaceDetection (rejected) | VLM Gaze Analysis (chosen) |
|---|---|---|
| Detects face presence | ✓ | ✓ |
| Detects actual gaze direction | ✗ | ✓ |
| Latency | ~200ms (event-driven) | ~2–5s (API round trip) |
| Dependencies | `pyee` + WebSocket subscription | `openai` (already needed) |
| Accuracy for gaze direction | Poor (not designed for it) | High (VLM spatial reasoning) |
| Setup required | None | None (same API as screen analysis) |

### Implementation in `utils/vision.py`

```python
def vlm_is_gazing(b64: str, cfg: Config) -> bool:
    """Returns True if GPT-4o determines the person is looking at the camera."""
    client = openai.OpenAI(api_key=cfg.openai_api_key, timeout=20)
    try:
        result = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text",
                 "text": "Is the person in this image looking directly at the camera "
                         "(i.e., making eye contact with the camera)? "
                         "Answer only: yes or no."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]}]
        )
        answer = result.choices[0].message.content.strip().lower()
        return answer.startswith("yes")
    except Exception:
        return False   # fail-safe: assume not gazing
```

---

## 11. Screen Capture Strategy

Misty's **built-in RGB camera** (`get_info("picture_rgb")`) is used for screen capture. The camera must be physically aimed at the computer screen — controlled via `head_move` in `look_at_screen()`.

**Physical calibration needed:** `cfg.screen_head_yaw` and `cfg.screen_head_pitch` must be tuned to the specific desk layout and robot placement. Start with `Yaw = -40` (left), `Pitch = -10` (down).

**Vision API:** GPT-4o Vision (sync `openai.OpenAI()` client). Base64 image from `picture_rgb` is passed as a `data:image/jpeg;base64,...` URI. Tesseract is **not recommended** (OCR only, fails on UI/diagram/code content).

---

## 12. Distraction Signal Design

**Implementation: `ExternalSignalReceiver` (HTTP server)**

Misty is a pure **executor** — it only reacts to signals sent by an external distraction measurement tool. The robot does not determine when distraction starts or ends; it is told.

The external tool (e.g., eye-tracker software, EEG, or research platform) sends HTTP POST requests to a local server running inside the pipeline:

```
POST http://127.0.0.1:5050/distraction/start  → distraction begins
POST http://127.0.0.1:5050/distraction/stop   → distraction ends
POST http://127.0.0.1:5050/shutdown           → end session
```

```
ExternalSignalReceiver
    └── lightweight HTTP server (http.server or Flask) in background thread
        listens on cfg.signal_port (default 5050)
        queues events: START, STOP, SHUTDOWN

StubTrigger   ← testing only: auto-fires START after N seconds, STOP N seconds later
```

### Both start AND stop signals are logged

The `distraction_duration_s` metric is computed from the delta between the START and STOP timestamps, regardless of when gaze is detected. The STOP signal is recorded even if it arrives during gaze polling — `ExternalSignalReceiver` buffers it and `pipeline.py` checks after the distraction stage completes.

---

## 13. File/Directory Final Structure

```
cs172-misty/
├── main.py                       ← EXISTING (+argparse + pipeline call)
├── config.py                     ← NEW
├── pipeline.py                   ← NEW
├── stages/
│   ├── __init__.py               ← NEW
│   ├── bootup.py                 ← NEW
│   ├── screen_watch.py           ← NEW (returns screen_pos; caches after first call)
│   ├── distraction.py            ← NEW (VLM gaze loop; head-only shake)
│   ├── no_response_prompt.py     ← NEW
│   └── summary.py                ← NEW (saves session JSON)
├── utils/
│   ├── __init__.py               ← NEW
│   ├── expressions.py            ← NEW (show_image, arm_gesture — NO led)
│   ├── head_control.py           ← NEW (look_at_screen, shake_head_only — no center_head)
│   ├── vision.py                 ← NEW (capture_frame, analyze_screen, vlm_is_gazing)
│   ├── session_log.py            ← NEW (structured log + save_to_file)
│   ├── session_id.py             ← NEW (generate_session_id)
│   └── triggers.py               ← NEW (ExternalSignalReceiver HTTP, StubTrigger)
├── sessions/                     ← AUTO-CREATED at runtime
│   └── <session_id>.json         ← one file per experiment run
├── .env                          ← EXISTING (add OPENAI_API_KEY)
├── README.md                     ← EXISTING
└── misty_working_pipeline_plan.md ← THIS FILE
```

Total new files: **15** (1 config, 1 pipeline, 5 stages, 7 utils, 1 sessions dir auto-created)

---

## 14. Session Log Schema (`sessions/<session_id>.json`)

Each experiment run produces one JSON file. All timestamps are ISO-8601 UTC.

```json
{
  "session_id": "session_20260321_P01_abc123",
  "participant_id": "P01",
  "start_time": "2026-03-21T14:00:00Z",
  "end_time": "2026-03-21T14:43:17Z",
  "screen_observations": [
    {"timestamp": "...", "analysis": "User is writing Python code in VS Code."}
  ],
  "distraction_events": [
    {
      "event_index": 1,
      "distraction_start_time": "2026-03-21T14:12:05Z",
      "distraction_end_time": "2026-03-21T14:12:48Z",
      "distraction_end_signal_received": true,
      "distraction_duration_s": 43.1,
      "gaze_detected": true,
      "gaze_latency_s": 12.4,
      "voice_prompt_used": false,
      "voice_prompt_count": 0
    },
    {
      "event_index": 2,
      "distraction_start_time": "2026-03-21T14:28:30Z",
      "distraction_end_time": null,
      "distraction_end_signal_received": false,
      "distraction_duration_s": null,
      "gaze_detected": false,
      "gaze_latency_s": null,
      "voice_prompt_used": true,
      "voice_prompt_count": 2
    }
  ],
  "total_distraction_count": 2,
  "total_voice_prompts": 2
}
```

### Fields Explained

| Field | Type | Description |
|---|---|---|
| `session_id` | str | Unique ID: `session_<date>_<participant_id>_<uuid6>` |
| `participant_id` | str | From `--participant` CLI arg |
| `distraction_start_time` | ISO timestamp | When `/distraction/start` POST received |
| `distraction_end_time` | ISO timestamp or null | When `/distraction/stop` POST received; null if never received |
| `distraction_end_signal_received` | bool | Whether an explicit stop signal was received |
| `distraction_duration_s` | float or null | `end_time - start_time`; null if no end signal |
| `gaze_detected` | bool | Whether VLM confirmed participant looking at robot |
| `gaze_latency_s` | float or null | Seconds from distraction start to gaze confirmation; null if not detected |
| `voice_prompt_used` | bool | Whether any escalation prompt was spoken |
| `voice_prompt_count` | int | Number of voice prompts issued for this distraction event |
| `total_distraction_count` | int | Total number of distraction START signals received in session |

---

## 15. Recommended Implementation Order

1. `config.py` + `utils/session_id.py` — pure Python, no hardware needed
2. `utils/expressions.py` + `utils/head_control.py` — verify on real robot (no LED, no center_head)
3. `stages/bootup.py` — first live robot test
4. `utils/vision.py` — verify `picture_rgb` base64 key path + `analyze_screen` + `vlm_is_gazing`
5. `stages/screen_watch.py` — screen calibration cache + reading/speaking face switching
6. `utils/session_log.py` — JSON log structure + `save_to_file()`
7. `utils/triggers.py` (`StubTrigger` first) — offline pipeline testing
8. `stages/distraction.py` — VLM gaze loop + head-only shake (no arms, no center_head)
9. `stages/no_response_prompt.py` + `stages/summary.py`
10. `pipeline.py` — wire all stages; integrate `screen_pos` caching
11. `utils/triggers.py` (`ExternalSignalReceiver`) — replace stub for real experiment

---

*Plan updated: 2026-03-21 (revision 2)*  
*Changes from revision 1: removed all LED, added session ID/participant tracking, differentiated face expressions, switched distraction to external HTTP signal, replaced FaceDetection with VLM gaze analysis, removed arm movements from distraction stage, removed center_head, cached screen position, restructured SessionLog for experiment data.*
