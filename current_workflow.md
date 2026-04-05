# cs172-misty — Current Workflow

> Generated: April 5, 2026

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
│  - Provides: wait_for_start(), consume_interrupt() │
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
│     ├─ Speak greeting                              │
│     ├─ Find screen via VLM grid search             │
│     └─ Return screen_pos                           │
│                                                    │
│  2. MAIN LOOP (while True):                        │
│     │                                              │
│     ├─ Check for shutdown                          │
│     │                                              │
│     ├─ SCREEN WATCH (stages/screen_watch.py)       │
│     │   ├─ If cached screen_pos: reuse             │
│     │   ├─ Else: VLM grid search (coarse + fine)   │
│     │   ├─ Confirm alignment with VLM              │
│     │   ├─ If failed 3x: use default position      │
│     │   └─ Return screen_pos or None               │
│     │                                              │
│     ├─ WAIT FOR START SIGNAL                       │
│     │   └─ Blocks until "start" from server.py     │
│     │                                              │
│     ├─ DISTRACTION (stages/distraction.py)         │
│     │   ├─ Show distraction face                   │
│     │   ├─ Start head shake thread                 │
│     │   ├─ Poll gaze via VLM (camera + OpenAI)     │
│     │   │   ├─ Fast poll first 12s (0.18s interval)│
│     │   │   └─ Normal poll after (0.7s interval)   │
│     │   ├─ If gaze detected:                       │
│     │   │   ├─ Stop shake, acknowledge nod         │
│     │   │   ├─ Speak "I see you!"                  │
│     │   │   └─ Return outcome="gaze"               │
│     │   ├─ If "stop" signal received:              │
│     │   │   └─ Return outcome="stop"               │
│     │   ├─ If "shutdown" signal:                   │
│     │   │   └─ Return outcome="shutdown"           │
│     │   └─ If timeout (45s):                       │
│     │       └─ Return outcome="timeout"            │
│     │                                              │
│     ├─ HANDLE OUTCOME:                             │
│     │   ├─ "stop" → log, reset attempt, continue   │
│     │   ├─ "shutdown" → break                      │
│     │   ├─ "gaze" →                                │
│     │   │   ├─ Redirect attention to screen        │
│     │   │   │   (nod toward screen + arm cue)      │
│     │   │   ├─ Wait for stop/shutdown (60s)        │
│     │   │   ├─ If confirmed → reset, continue      │
│     │   │   └─ If timeout → run_no_response,       │
│     │   │       cue arm, reset, continue           │
│     │   └─ "timeout" →                             │
│     │       ├─ Increment attempt counter           │
│     │       ├─ NO RESPONSE (stages/no_response)    │
│     │       │   ├─ Generate focus reminder via      │
│     │       │   │  OpenAI (from current reading)   │
│     │       │   ├─ Speak reminder                  │
│     │       │   └─ Cue screen with left arm (2x)   │
│     │       └─ If attempt >= max_attempts (3):     │
│     │           break                              │
│     │                                              │
│  3. SUMMARY (stages/summary.py)                    │
│     ├─ Generate and speak session summary          │
│     ├─ Save session log as JSON                    │
│     └─ Show closing face                           │
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
| `pipeline.py` | Main orchestration loop — bootup → screen watch → distraction → recovery → summary |
| `config.py` | `Config` frozen dataclass with all parameters; `load_config()` from `.env` |

### Stages (pipeline steps)
| File | Role |
|------|------|
| `stages/bootup.py` | One-time init: greeting, screen detection |
| `stages/screen_watch.py` | VLM-based screen position search (coarse + fine grid) |
| `stages/distraction.py` | Head shake + VLM gaze polling loop |
| `stages/no_response_prompt.py` | OpenAI-generated focus reminder + arm cue |
| `stages/summary.py` | Session summary speech + log save |

### Utilities
| File | Role |
|------|------|
| `utils/audio.py` | `speak_text()`, `ensure_audio_ready()` — wraps misty2py speech |
| `utils/expressions.py` | Face image constants (`BOOT_FACE`, etc.), `show_image()`, `arm_gesture()` |
| `utils/focus_prompt.py` | OpenAI-based focus reminder generation from reading text |
| `utils/head_control.py` | Head movements (shake, nod, look), arm cues, redirect attention |
| `utils/session_id.py` | UUID-based session ID generation |
| `utils/session_log.py` | `SessionLog` class — records events, distractions, saves JSON |
| `utils/triggers.py` | `ExternalSignalReceiver` (HTTP event server), `StubTrigger` (testing) |
| `utils/vision.py` | Camera capture from Misty, VLM analysis (screen alignment, gaze detection) |

---

## Signal Flow

1. **Browser Extension / Webcam** sends WebSocket messages to `server.py`
2. `server.py` parses signal type (disengagement, re-engagement, posture, etc.)
3. `DisengagementTracker` deduplicates state transitions (prevents double-start/stop)
4. Recognized events are forwarded via HTTP POST to `ExternalSignalReceiver` at `127.0.0.1:5050`
5. `ExternalSignalReceiver` queues events for the pipeline to consume
6. Pipeline calls `wait_for_start()`, `consume_interrupt()`, or `wait_for_stop_or_shutdown()` to read events

## VLM (Vision-Language Model) Usage

Two VLM functions, both using OpenAI API (`gpt-4o`):

1. **Screen Alignment** (`analyze_screen_capture`): Classifies camera frame as `aligned`, `visible`, or `none` — used during screen search grid
2. **Gaze Detection** (`analyze_gaze_capture`): Classifies whether person is looking at robot — `yes`/`no` — used during distraction polling

Both functions:
- Capture frame from Misty's RGB camera via HTTP
- Resize/compress image (max 768px, JPEG quality 72)
- Send to OpenAI Vision API with single-shot prompt
- Parse response with regex for expected label

## Session Logging

- Each session creates a JSON file in `sessions/` directory
- Filename format: `session_YYYYMMDD_username_hexid.json`
- Contains: all events, distraction records (timing, gaze latency, outcome), screen observations, voice prompt counts
- Generated at session end by `SessionLog.save_to_file()`

## Configuration

- All parameters live in `config.py` as a frozen `@dataclass`
- Loaded from `.env` file (Misty IP, OpenAI API key) + dataclass defaults
- ~50 parameters covering: timing, robot motion, VLM settings, behavior thresholds
- Participant ID provided via `--username` CLI argument

## Running the System

```bash
# Terminal 1: Start WebSocket bridge
python server.py

# Terminal 2: Start robot pipeline
python main.py --username <participant_name>
```

Both processes must run simultaneously. The pipeline depends on `server.py` for distraction start/stop signals.
