# Codebase Overview — cs172-misty

## Description

`cs172-misty` is a Python project scaffold for programming and interacting with a **Misty II social robot** as part of the CS172 (Human-Computer Interaction) course at Tufts University. The Misty II is a research and education robot capable of movement, audio output/input, computer vision, and multi-sensor interaction.

The repository provides a minimal connection scaffold: `main.py` bootstraps a live `Misty` client instance pointed at the physical robot by reading the robot's IP address from a local `.env` file. All HCI experiments, behaviors, and demos are to be built on top of `my_misty` using the `misty2py` SDK methods.

Additionally, the repository contains a fully structured **AI agentic coding workflow system** under `.github/instructions/`, which routes developer prompts (code, debug, query, correctness-check, initialize) to specialized instruction files and sub-agent workflows. This system is meta-level tooling and is not part of the robot control logic.

---

## Pipeline Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ROBOT APPLICATION PIPELINE                     │
│                                                                     │
│  .env (MISTY_IP_ADDRESS)                                            │
│       │                                                             │
│       ▼                                                             │
│  env_loader = EnvLoader()    ← misty2py.utils.env_loader           │
│       │                         instantiated first; reads .env     │
│  env_loader.get_ip()         ← instance method → Optional[str]    │
│       │                                                             │
│       ▼                                                             │
│  Misty(ip)                   ← misty2py.robot                      │
│       │                         instantiates robot client           │
│       │                         wires REST + WebSocket channels     │
│       ├──── .get_info(name)  ──────────────►  REST GET   ──► Misty II Robot
│       │        [main.py]        misty2py                           │
│       ├──── .perform_action(name, data)  ──► REST POST  ──► Misty II Robot
│       │        [main.py]                    misty2py               │
│       └──── .event(action, **kwargs)  ─────► WebSocket ──► Misty II Robot
│                [main.py]               misty2py                    │
│                                                                     │
│  Response → .parse_to_dict()  ← structured JSON response from robot│
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    AI AGENT WORKFLOW PIPELINE                       │
│                                                                     │
│  User Prompt                                                        │
│       │                                                             │
│       ▼                                                             │
│  master.instructions.md  ── classifies intent ──►  one of:         │
│       │                    [.github/instructions/workflow/]         │
│       │                                                             │
│       ├── initialize.instructions.md  → bootstrap repo_info/ docs  │
│       ├── code.instructions.md        → implement new features      │
│       ├── debug.instructions.md       → root-cause analysis & fix   │
│       ├── correctness_check.md        → full-repo audit             │
│       └── query.instructions.md       → Q&A with sub-agents        │
│                                                                     │
│  All workflows also consult:                                        │
│  ├── philosophy.instructions.md       (general rules)              │
│  ├── repo_info/codebase_overview.md   (this file)                  │
│  ├── repo_info/scripts_overview.md                                  │
│  ├── repo_info/update_logs.md                                       │
│  └── repo_info/known_issues.md                                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Components

| Component | Location | Role |
|---|---|---|
| `main.py` | repo root | Entry point. Loads IP from `.env`, instantiates `Misty` robot client. All robot behavior is built here. |
| `.env` | repo root (gitignored) | Stores `MISTY_IP_ADDRESS` — the local network IP of the physical Misty II robot. |
| `.gitignore` | repo root | Excludes `.env` from version control. |
| `misty2py.robot.Misty` | external SDK | Central robot class. Wraps all Misty II REST API endpoints (movement, audio, LED, vision, etc.) and WebSocket event streams. |
| `misty2py.utils.env_loader.EnvLoader` | external SDK | Reads `.env` to extract `MISTY_IP_ADDRESS`, decoupling configuration from code. |
| `master.instructions.md` | `.github /instructions/` | Prompt router. Reads user intent and dispatches to the correct workflow instruction file. Enforces safety rules. |
| `workflow/*.instructions.md` | `.github /instructions/workflow/` | Category-specific multi-step agentic workflows (initialize, code, debug, correctness check, query). |
| `general/philosophy.instructions.md` | `.github /instructions/general/` | Meta-rules for all agents: context management, sub-agent usage, guiding principles. |
| `general/*_request_template.md` | `.github /instructions/general/` | User-facing prompt templates for code, debug, correctness-check, and query workflows. Note: no initialize template exists. |
| `repo_info/` | `.github /instructions/repo_info/` | Persistent context store populated by the initialize workflow. Consumed by all other workflows. |

---

## Dependencies

| Dependency | Purpose |
|---|---|
| `misty2py` | Python SDK for the Misty II robot (REST + WebSocket API wrapper) |
| `python-dotenv` | Used internally by `misty2py.EnvLoader` to parse the `.env` file |
| Python 3.x | Runtime |
| Misty II hardware | Physical robot on the same LAN, reachable by IP |

> **Note:** No `requirements.txt` or `pyproject.toml` is present. The repo is in early scaffold state.

---

## Current State

- **Minimal scaffolding.** Only `main.py` (≈9 lines) and `README.md` (title only) exist as application-level files.
- **No robot behavior implemented yet.** The connection test call (`get_info("device")`) is commented out.
- **AI workflow infrastructure is complete** and ready to guide development.
- The repo is expected to grow with robot behavior scripts as the HCI course progresses.
