# cs172-misty — Current Workflow

> Generated: April 14, 2026

## System Overview

The cs172-misty project is a **Misty II social robot focus companion** designed to help users with ADHD stay focused while reading. The system consists of two independently running processes that communicate via HTTP.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────┐
│              BROWSER / EXTERNAL CLIENTS           │
│  ┌─────────────┐         ┌──────────────────┐    │
│  │  Webcam App  │         │ Browser Extension│    │
│  └──────┬──────┘         └────────┬─────────┘    │
│         │  WebSocket              │  WebSocket    │
└─────────┼─────────────────────────┼──────────────┘
          │                         │
          ▼                         ▼
┌───────────────────────────────────────────────────┐
│              server.py (WebSocket Bridge)          │
│  - Listens on ws://0.0.0.0:8765                   │
│  - Registers clients by role (webcam/extension)   │
│  - Parses signals: disengagement, re-engagement,  │
│    posture, shutdown, start, stop                  │
│  - DisengagementTracker: deduplicates state changes│
│  - Forwards events via HTTP POST to pipeline       │
│  - Forwards re-engagement/posture to extension     │
└───────────────────────┬───────────────────────────┘
                        │ HTTP POST
                        │ /distraction/start
                        │ /distraction/stop
                        │ /shutdown
                        │ /current_text
                        ▼
┌───────────────────────────────────────────────────┐
│      ExternalSignalReceiver (in pipeline process)  │
│      (utils/triggers.py)                           │
│  - HTTP server on 127.0.0.1:5050                   │
│  - Queues events for pipeline consumption          │
│  - Provides: wait_for_start(), consume_interrupt(),│
│    wait_for_stop_or_shutdown_only()                │
│  - Stores current reading text                     │
└───────────────────────┬───────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────┐
│            Pipeline (pipeline.py + main.py)        │
│  Entry: python main.py --username <name>           │
│                                                    │
│  1. BOOTUP (stages/bootup.py)                      │
│     ├─ Show boot face                              │
│     ├─ Lower arms, reset head to neutral, settle   │
│     ├─ Speak greeting                              │
│     ├─ Say "I'm looking for your screen now."      │
│     ├─ Wait bootup_intro_speech_wait_s for speech  │
│     ├─ Turn head to config screen_yaw/screen_pitch │
│     │  (+45 = left), wait screen_settle_s          │
│     ├─ Hold bootup_screen_hold_s                   │
│     ├─ Say "I've found the screen!" + start reading│
│     └─ Return screen_pos                           │
│                                                    │
│  2. MAIN LOOP (while True):                        │
│     │                                              │
│     ├─ Check for shutdown                          │
│     │                                              │
│     ├─ SCREEN WATCH (stages/screen_watch.py)       │
│     │   ├─ Turn head back to known screen_pos      │
│     │   └─ Skipped once immediately after a real   │
│     │      stop so the robot returns directly to   │
│     │      waiting                                 │
│     │                                              │
│     ├─ WAIT FOR START SIGNAL                       │
│     │   └─ Blocks until "start" from server.py     │
│     │                                              │
│     ├─ DISTRACTION (stages/distraction.py)         │
│     │   ├─ Show distraction face                   │
│     │   ├─ Start distraction motion thread         │
│     │   │   ├─ Turn head left toward user          │
│     │   │   ├─ Wave both arms once                 │
│     │   │   ├─ Return head to screen               │
│     │   │   └─ Cue left arm twice                  │
│     │   ├─ Poll stop/shutdown while motion runs    │
│     │   ├─ If motion finishes:                     │
│     │   │   └─ Return outcome="sequence_complete"  │
│     │   ├─ If "stop" signal received:              │
│     │   │   └─ Signal recovery to screen and       │
│     │   │      return outcome="stop"               │
│     │   ├─ If "shutdown" signal:                   │
│     │   │   └─ Return outcome="shutdown"           │
│     │   └─ If timeout (45s):                       │
│     │       └─ Return outcome="timeout"            │
│     │                                              │
│     ├─ HANDLE OUTCOME:                             │
│     │   ├─ "stop" → log, reset attempt, skip next  │
│     │   │             screen_watch, continue        │
│     │   ├─ "shutdown" → break                      │
│     │   ├─ "sequence_complete" →                   │
│     │   │   ├─ Drain pending stop/shutdown only    │
│     │   │   ├─ Wait for stop/shutdown only (60s)   │
│     │   │   ├─ If confirmed → reset, continue      │
│     │   │   └─ If timeout → run one contextual     │
│     │   │       reminder, then quiet-wait for stop │
│     │   └─ "timeout" →                             │
│     │       ├─ Increment attempt counter           │
│     │       ├─ NO RESPONSE (stages/no_response)    │
│     │       │   ├─ Generate focus reminder via      │
│     │       │   │  OpenAI (from current reading)   │
│     │       │   ├─ Abort if stop arrives before    │
│     │       │   │  speech or arm cue               │
│     │       │   ├─ Speak reminder                  │
│     │       │   └─ Cue screen with left arm (1x)   │
│     │       └─ If attempt >= max_attempts (3):     │
│     │           break                              │
│     │                                              │
│  3. SUMMARY (stages/summary.py)                    │
│     ├─ Generate and speak session summary          │
│     ├─ Show closing face and wave                  │
│     ├─ Save session log as JSON, regenerate CSVs   │
│     └─ Reset head to neutral and lower arms        │
└───────────────────────────────────────────────────┘
```

---

## File Responsibilities

### Entry Points
| File | Role |
|------|------|
| `main.py` | CLI entry point — parses args, builds Misty connection, calls `pipeline.run()` |
| `server.py` | Standalone WebSocket bridge — runs independently via `python server.py` |

### Core Pipeline
| File | Role |
|------|------|
| `pipeline.py` | Main orchestration loop — bootup → screen watch / passive wait → distraction → post-sequence confirmation / reminder → summary |
| `config.py` | `Config` frozen dataclass with all parameters; `load_config()` from `.env` |

### Stages (pipeline steps)
| File | Role |
|------|------|
| `stages/bootup.py` | One-time init: resets head and arms, greeting, scripted turn to the configured screen position (no camera/VLM) |
| `stages/screen_watch.py` | Holds the known screen position: `default_screen_position(cfg)` and turning the head back to it |
| `stages/distraction.py` | Threaded distraction-start motion + interrupt polling |
| `stages/no_response_prompt.py` | OpenAI-generated focus reminder + arm cue |
| `stages/summary.py` | Session summary speech + log save, then resets head and arms |

### Utilities
| File | Role |
|------|------|
| `utils/audio.py` | `speak_text()`, `ensure_audio_ready()` — wraps misty2py speech with bounded waits |
| `utils/expressions.py` | Face image constants (`BOOT_FACE`, etc.), `show_image()`, `arm_gesture()` |
| `utils/focus_prompt.py` | OpenAI-based focus reminder generation from reading text |
| `utils/head_control.py` | Head/arm motions with bounded action waits and stop-aware recovery |
| `utils/session_id.py` | UUID-based session ID generation |
| `utils/session_log.py` | `SessionLog` class — records events, distractions, saves JSON |
| `utils/triggers.py` | `ExternalSignalReceiver` (HTTP event server), `StubTrigger` (testing) |
| `utils/vision.py` | Camera capture and VLM gaze helper; not imported by the live pipeline |

---

## Signal Flow

1. **Browser Extension / Webcam** sends WebSocket messages to `server.py`
2. `server.py` parses signal type (disengagement, re-engagement, posture, etc.)
3. `DisengagementTracker` deduplicates state transitions (prevents double-start/stop)
4. Recognized events are forwarded via HTTP POST to `ExternalSignalReceiver` at `127.0.0.1:5050`
5. `ExternalSignalReceiver` queues events for the pipeline to consume
6. Pipeline calls `wait_for_start()`, `consume_interrupt()`, or `wait_for_stop_or_shutdown_only()` depending on whether the current phase should accept new `start` events

## VLM (Vision-Language Model) Usage

The live pipeline no longer uses a VLM or Misty's camera. The robot is positioned by hand before each session, and bootup turns its head to the fixed `screen_yaw` / `screen_pitch` from `config.py`, then announces that it found the screen.

Yaw sign: `+` turns the robot's head to its left, `-` to its right, `0` is straight ahead. The screen is at `+45` (left); the participant is at `distraction_user_turn_yaw_deg = -45` (right).

The distraction stage does not use live VLM gaze detection either; it uses a fixed distraction-start motion followed by stop/timeout confirmation logic in `pipeline.py`. OpenAI is still used for the generated focus reminders, so `OPENAI_API_KEY` is still required.

## Session Logging

- Each session creates a JSON file in `sessions/` directory
- Filename format: `session_YYYYMMDD_username_hexid.json`
- Contains: all events, distraction records (motion timing, full distraction end timing, outcome), screen observations, voice prompt counts
- Generated at session end by `SessionLog.save_to_file()`

## Configuration

- All parameters live in `config.py` as a frozen `@dataclass`
- Loaded from `.env` file (Misty IP, OpenAI API key) + dataclass defaults
- Includes bounded robot/speech wait settings (`robot_action_timeout_s`, `speech_timeout_s`) in addition to timing, robot motion, and screen position (`screen_yaw`, `screen_pitch`) settings
- Participant ID provided via `--username` CLI argument

## Running the System

```bash
# Terminal 1: Start WebSocket bridge
python server.py

# Terminal 2: Start robot pipeline
python main.py --username <participant_name>
```

Both processes must run simultaneously. The pipeline depends on `server.py` for distraction start/stop signals.
