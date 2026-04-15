from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat()


class SessionLog:
    def __init__(self, session_id: str, cfg):
        self.session_id = session_id
        self.cfg = cfg
        self.start_time = _local_iso()
        self.end_time = None
        self.screen_observations = []
        self.distraction_events = []
        self.total_voice_prompts = 0
        self.events = []

    def record(self, name: str, **payload) -> None:
        self.events.append(
            {
                "timestamp": _local_iso(),
                "name": name,
                "payload": payload,
            }
        )

    def record_screen_observation(self, analysis: str) -> None:
        self.screen_observations.append(
            {
                "timestamp": _local_iso(),
                "analysis": analysis,
            }
        )

    def record_distraction_start(self) -> None:
        self.distraction_events.append(
            {
                "event_index": len(self.distraction_events) + 1,
                "distraction_start_time": _local_iso(),
                "motion_end_time": None,
                "motion_duration_s": None,
                "distraction_end_time": None,
                "distraction_end_signal_received": False,
                "distraction_duration_s": None,
                "sequence_completed": False,
                "completion_latency_s": None,
                "exit_reason": None,
                "session_close_reason": None,
                "voice_prompt_used": False,
                "voice_prompt_count": 0,
            }
        )

    def _close_distraction_event(self, current: dict) -> None:
        if current["distraction_end_time"] is not None:
            return
        end_ts = _local_iso()
        current["distraction_end_time"] = end_ts
        start = datetime.fromisoformat(current["distraction_start_time"])
        end = datetime.fromisoformat(end_ts)
        current["distraction_duration_s"] = round((end - start).total_seconds(), 2)

    def record_distraction_result(self, outcome: str, sequence_completed: bool, latency_s) -> None:
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        current["sequence_completed"] = bool(sequence_completed)
        current["completion_latency_s"] = round(latency_s, 2) if latency_s is not None else None
        current["exit_reason"] = outcome
        if current["motion_end_time"] is None:
            end_ts = _local_iso()
            current["motion_end_time"] = end_ts
            start = datetime.fromisoformat(current["distraction_start_time"])
            end = datetime.fromisoformat(end_ts)
            current["motion_duration_s"] = round((end - start).total_seconds(), 2)

    def record_distraction_end(self) -> None:
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        if current["distraction_end_signal_received"]:
            return
        current["distraction_end_signal_received"] = True
        self._close_distraction_event(current)

    def close_active_distraction(self, reason: str | None = None) -> None:
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        if current["distraction_end_time"] is not None:
            return
        current["session_close_reason"] = reason
        self._close_distraction_event(current)

    def record_voice_prompt(self) -> None:
        self.total_voice_prompts += 1
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        current["voice_prompt_used"] = True
        current["voice_prompt_count"] += 1

    def generate_summary(self) -> str:
        return (
            "Great job finishing your reading session! "
            "Every session is a step forward. "
            "Keep it up — I'll be here cheering you on next time!"
        )

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "participant_id": self.cfg.participant_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "screen_observations": self.screen_observations,
            "events": self.events,
            "distraction_events": self.distraction_events,
            "total_distraction_count": len(self.distraction_events),
            "total_voice_prompts": self.total_voice_prompts,
        }

    def save_to_file(self) -> Path:
        self.end_time = _local_iso()
        directory = Path("sessions")
        directory.mkdir(parents=True, exist_ok=True)
        output_path = directory / f"{self.session_id}.json"
        output_path.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        return output_path
