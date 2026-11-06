from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

RECOVERED_AT_STAGES = (
    "self_recovery",
    "sound",
    "head_redirect_motion",
    "head_redirect_wait",
    "fatigue_support",
)
SUPPORT_INTERRUPTED_AT = ("during_highlight", "before_recap", "during_recap")


def local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def seconds_between(start_iso: str, end_iso: str) -> float:
    return (datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds()


class SessionLog:
    def __init__(self, session_id: str, cfg):
        self.session_id = session_id
        self.cfg = cfg
        self.start_time = local_iso()
        self.end_time = None
        self.screen_observations = []
        self.distraction_events = []
        self.total_voice_prompts = 0
        self.events = []

    def record(self, name: str, **payload) -> None:
        self.events.append(
            {
                "timestamp": local_iso(),
                "name": name,
                "payload": payload,
            }
        )

    def record_screen_observation(self, analysis: str) -> None:
        self.screen_observations.append(
            {
                "timestamp": local_iso(),
                "analysis": analysis,
            }
        )

    def record_distraction_start(self, trigger_reason: str | None = None, start_time: str | None = None) -> None:
        """start_time is when the start signal arrived; it can predate this call."""
        processing_time = local_iso()
        start_ts = start_time or processing_time
        if self.distraction_events:
            self._backfill_next_disengagement(self.distraction_events[-1], start_ts)
        self.distraction_events.append(
            {
                "event_index": len(self.distraction_events) + 1,
                "distraction_start_time": start_ts,
                "trigger_reason": trigger_reason,
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
                "processing_delayed_by_recap_s": round(max(0.0, seconds_between(start_ts, processing_time)), 2),
                "recovered_at_stage": None,
                "post_return_highlight": {"sent": False, "ok": False, "time": None},
                "post_return_recap": {
                    "status": "not_applicable",
                    "start_time": None,
                    "delay_from_return_s": None,
                    "used_fallback": False,
                },
                "disengaged_during_recap": False,
                "resumption_support_complete_time": None,
                "resumption_support_interrupted": None,
                "resumption_support_interrupted_at": None,
                "next_disengagement_start_time": None,
                "time_to_next_disengagement_s": None,
            }
        )

    def _backfill_next_disengagement(self, previous: dict, next_start: str) -> None:
        previous["next_disengagement_start_time"] = next_start
        if previous["distraction_end_time"] is not None:
            previous["time_to_next_disengagement_s"] = round(
                seconds_between(previous["distraction_end_time"], next_start), 2
            )
        support_complete = previous["resumption_support_complete_time"]
        if previous["resumption_support_interrupted"] is not None or support_complete is None:
            return
        if seconds_between(next_start, support_complete) > 0:
            recap_played = previous["post_return_recap"]["status"] == "played"
            previous["disengaged_during_recap"] = recap_played
            self._mark_support_interrupted(previous, "during_recap" if recap_played else "during_highlight")
        else:
            previous["resumption_support_interrupted"] = False

    @staticmethod
    def _mark_support_interrupted(current: dict, interrupted_at: str) -> None:
        current["resumption_support_interrupted"] = True
        current["resumption_support_interrupted_at"] = interrupted_at

    def record_post_return_highlight(self, sent: bool, ok: bool, time: str | None, duration_ms: int) -> None:
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        current["post_return_highlight"] = {"sent": sent, "ok": ok, "time": time}
        if sent and ok:
            highlight_end = datetime.fromisoformat(time) + timedelta(milliseconds=duration_ms)
            current["resumption_support_complete_time"] = highlight_end.isoformat()

    def record_post_return_recap(
        self,
        status: str,
        start_time: str | None = None,
        delay_from_return_s: float | None = None,
        used_fallback: bool = False,
        end_time: str | None = None,
        disengaged_during_recap: bool = False,
    ) -> None:
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        current["post_return_recap"] = {
            "status": status,
            "start_time": start_time,
            "delay_from_return_s": round(delay_from_return_s, 2) if delay_from_return_s is not None else None,
            "used_fallback": bool(used_fallback),
        }
        if status == "cancelled_new_disengagement":
            self._mark_support_interrupted(current, "before_recap")
        elif status == "played":
            current["resumption_support_complete_time"] = end_time
            if disengaged_during_recap:
                current["disengaged_during_recap"] = True
                self._mark_support_interrupted(current, "during_recap")

    def _close_distraction_event(self, current: dict, end_time: str | None = None) -> None:
        if current["distraction_end_time"] is not None:
            return
        end_ts = end_time or local_iso()
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
            end_ts = local_iso()
            current["motion_end_time"] = end_ts
            start = datetime.fromisoformat(current["distraction_start_time"])
            end = datetime.fromisoformat(end_ts)
            current["motion_duration_s"] = round((end - start).total_seconds(), 2)

    def record_distraction_end(self, end_time: str | None = None, recovered_at_stage: str | None = None) -> None:
        """Close the event at end_time: when the stop arrived, not when support finished."""
        if not self.distraction_events:
            return
        current = self.distraction_events[-1]
        if current["distraction_end_signal_received"]:
            return
        current["distraction_end_signal_received"] = True
        current["recovered_at_stage"] = recovered_at_stage
        self._close_distraction_event(current, end_time)

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
            "recovered_at_stage_counts": {
                stage: sum(1 for event in self.distraction_events if event["recovered_at_stage"] == stage)
                for stage in RECOVERED_AT_STAGES
            },
            "resumption_support_interrupted_count": sum(
                1 for event in self.distraction_events if event["resumption_support_interrupted"]
            ),
            "resumption_support_interrupted_at_counts": {
                interrupted_at: sum(
                    1 for event in self.distraction_events if event["resumption_support_interrupted_at"] == interrupted_at
                )
                for interrupted_at in SUPPORT_INTERRUPTED_AT
            },
        }

    def save_to_file(self) -> Path:
        self.end_time = local_iso()
        directory = Path("sessions")
        directory.mkdir(parents=True, exist_ok=True)
        output_path = directory / f"{self.session_id}.json"
        output_path.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        return output_path
