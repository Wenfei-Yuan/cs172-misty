"""
Generate two analysis CSVs from session JSON logs:
  1. intervention_events.csv  – one row per distraction event
  2. session_summary.csv      – one row per session
"""
from __future__ import annotations

import csv
import json
import glob
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent / "sessions"


# ────────────────────────── helpers ──────────────────────────

def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str)


def _ms_between(start: str | None, end: str | None) -> float | None:
    s, e = _parse_ts(start), _parse_ts(end)
    if s is None or e is None:
        return None
    return round((e - s).total_seconds() * 1000, 1)


def _s_between(start: str | None, end: str | None) -> float | None:
    s, e = _parse_ts(start), _parse_ts(end)
    if s is None or e is None:
        return None
    return round((e - s).total_seconds(), 2)


def _find_events_in_window(events: list[dict], name: str, after_ts: str | None, before_ts: str | None) -> list[dict]:
    """Return events with given name whose timestamp is between after_ts and before_ts."""
    results = []
    after_dt = _parse_ts(after_ts)
    before_dt = _parse_ts(before_ts)
    for e in events:
        if e["name"] != name:
            continue
        e_dt = _parse_ts(e["timestamp"])
        if e_dt is None:
            continue
        if after_dt and e_dt < after_dt:
            continue
        if before_dt and e_dt > before_dt:
            continue
        results.append(e)
    return results


def _find_first_event_after(events: list[dict], name: str, after_ts: str | None) -> dict | None:
    after_dt = _parse_ts(after_ts)
    for e in events:
        if e["name"] != name:
            continue
        e_dt = _parse_ts(e["timestamp"])
        if e_dt is None:
            continue
        if after_dt and e_dt < after_dt:
            continue
        return e
    return None


# ────────────────────────── load all sessions ──────────────────────────

def load_sessions() -> list[dict]:
    files = sorted(SESSIONS_DIR.glob("session_*.json"))
    files += sorted(SESSIONS_DIR.glob("baseline_*.json"))
    sessions = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            sessions.append(json.load(fh))
    return sessions


# ────────────────────────── intervention_events ──────────────────────────

INTERVENTION_HEADER = [
    "event_id",
    "participant_id",
    "session_id",
    "condition",
    "disengagement_onset_ts",
    "trigger_source",
    "robot_nonverbal_start_ts",
    "robot_nonverbal_type",
    "robot_nonverbal_end_ts",
    "distraction_end_ts",
    "escalated_to_voice",
    "voice_start_ts",
    "voice_prompt_type",
    "voice_prompt_text_length",
    "highlight_shown",
    "highlight_shown_ts",
    "resumed_within_window",
    "resume_latency_ms",
    "resume_stage",
]


def _determine_trigger_source(de: dict, events: list[dict]) -> str:
    """
    Infer trigger source from gaze_sampled_pose events before the distraction.
    The system uses head-pose + optional eye-gaze detection via webcam.
    Looking at gaze_sampled_pose events around distraction_start_time:
      - gaze_status == "not_gazing" -> head/gaze direction was off-screen
    Since all triggers come from the webcam detecting the user is not looking
    at the screen, we classify by the gaze_sampled_pose data.
    """
    start_ts = de.get("distraction_start_time")
    if not start_ts:
        return "unknown"

    # Find gaze_sampled_pose events shortly before the distraction start
    start_dt = _parse_ts(start_ts)
    last_gaze = None
    for e in events:
        if e["name"] != "gaze_sampled_pose":
            continue
        e_dt = _parse_ts(e["timestamp"])
        if e_dt is None:
            continue
        if start_dt and e_dt > start_dt:
            break
        last_gaze = e

    if last_gaze:
        payload = last_gaze.get("payload", {})
        gaze_status = payload.get("gaze_status", "")
        yaw_deg = payload.get("yaw_deg")

        if gaze_status == "not_gazing":
            if yaw_deg is not None and abs(float(yaw_deg)) >= 30:
                return "head_turned_away"
            return "gaze_off_screen"

    return "gaze_off_screen"


def _determine_nonverbal_type(de: dict) -> str:
    """
    The distraction start sequence is: head turn toward user + both-arm wave
    + head turn back to screen + left-arm cue.
    """
    return "head_turn_plus_wave"


def _find_voice_info(de: dict, events: list[dict], next_start_ts: str | None):
    """Find no_response_prompt_generated event in this distraction window.
    
    Use the full window from distraction start to the NEXT distraction start
    (or session end), because the voice prompt may be logged after the
    distraction_end_time in some code versions.
    """
    start_ts = de.get("distraction_start_time")
    # Always use next distraction start as upper bound for robustness
    end_ts = next_start_ts

    prompt_events = _find_events_in_window(events, "no_response_prompt_generated", start_ts, end_ts)
    if not prompt_events:
        # Also check speech_attempt with stage=no_response_prompt
        speech_events = _find_events_in_window(events, "speech_attempt", start_ts, end_ts)
        nrp_speeches = [e for e in speech_events if e.get("payload", {}).get("stage") == "no_response_prompt"]
        if nrp_speeches:
            sp = nrp_speeches[0]
            return {
                "voice_start_ts": sp["timestamp"],
                "voice_prompt_type": "generic",
                "voice_prompt_text_length": len(sp["payload"].get("utterance", "")),
            }
        return None

    pe = prompt_events[0]
    payload = pe.get("payload", {})

    # Determine type
    used_fallback = payload.get("used_fallback", True)
    prompt_type = "generic" if used_fallback else "context_aware"

    # Find the speech_attempt that corresponds
    speech_ts = None
    speech_events = _find_events_in_window(events, "speech_attempt", start_ts, end_ts)
    for se in speech_events:
        if se.get("payload", {}).get("stage") == "no_response_prompt":
            speech_ts = se["timestamp"]
            break

    reminder_text = payload.get("reminder", "")
    return {
        "voice_start_ts": speech_ts or pe["timestamp"],
        "voice_prompt_type": prompt_type,
        "voice_prompt_text_length": len(reminder_text),
    }


def _user_resumed(de: dict) -> bool:
    """Did the user actually resume (return attention) during this distraction?"""
    if de.get("distraction_end_signal_received"):
        return True
    if de.get("gaze_detected"):
        return True
    exit_reason = de.get("exit_reason")
    if exit_reason in ("stop", "gaze"):
        return True
    # sequence_complete with a stop signal received afterward
    if exit_reason == "sequence_complete" and de.get("distraction_end_signal_received"):
        return True
    return False


def _determine_resume_stage(de: dict, escalated: bool) -> str:
    if not _user_resumed(de):
        return "not_resumed"
    if escalated:
        return "resumed_after_voice"
    return "resumed_after_nonverbal"


def build_intervention_events(sessions: list[dict]) -> list[dict]:
    rows = []
    global_event_id = 0

    for sess in sessions:
        sid = sess.get("session_id", "")
        pid = sess.get("participant_id", "")
        events = sess.get("events", [])
        distractions = sess.get("distraction_events", [])
        condition = sess.get("condition", "with_system")

        for i, de in enumerate(distractions):
            global_event_id += 1

            # ── Baseline (no_system) — simplified row ──
            if condition == "no_system":
                start_ts = de.get("distraction_start_time")
                end_ts = de.get("distraction_end_time")
                duration_s = de.get("distraction_duration_s")
                resume_latency_ms = round(duration_s * 1000, 1) if duration_s is not None else ""
                resumed_at_all = de.get("distraction_end_signal_received", False)
                _RESUME_WINDOW_MS = 90_000
                within_window = (resumed_at_all
                                 and resume_latency_ms != ""
                                 and float(resume_latency_ms) <= _RESUME_WINDOW_MS)
                resumed = 1 if within_window else 0
                resume_stage = "self_recovered" if within_window else "not_resumed"
                trigger_source = de.get("trigger_source", "unknown")

                rows.append({
                    "event_id": global_event_id,
                    "participant_id": pid,
                    "session_id": sid,
                    "condition": condition,
                    "disengagement_onset_ts": start_ts or "",
                    "trigger_source": trigger_source,
                    "robot_nonverbal_start_ts": "",
                    "robot_nonverbal_type": "",
                    "robot_nonverbal_end_ts": "",
                    "distraction_end_ts": end_ts or "",
                    "escalated_to_voice": 0,
                    "voice_start_ts": "",
                    "voice_prompt_type": "",
                    "voice_prompt_text_length": "",
                    "highlight_shown": 0,
                    "highlight_shown_ts": "",
                    "resumed_within_window": resumed,
                    "resume_latency_ms": resume_latency_ms,
                    "resume_stage": resume_stage,
                })
                continue

            # ── with_system — full intervention row ──

            # Time window for this distraction
            next_start_ts = distractions[i + 1]["distraction_start_time"] if i + 1 < len(distractions) else sess.get("end_time")

            start_ts = de.get("distraction_start_time")
            end_ts = de.get("distraction_end_time")
            motion_end_ts = de.get("motion_end_time")

            escalated = de.get("voice_prompt_used", False)
            voice_info = _find_voice_info(de, events, next_start_ts) if escalated else None

            # Highlight: the arm cue after voice prompt acts as a physical highlight cue
            # Logged alongside no_response_prompt_generated (arm cue happens right after speech)
            highlight_shown = 1 if escalated else 0
            highlight_ts = voice_info["voice_start_ts"] if voice_info and escalated else ""

            # Resume latency = distraction_duration_s (already in seconds, convert to ms)
            duration_s = de.get("distraction_duration_s")
            resume_latency_ms = round(duration_s * 1000, 1) if duration_s is not None else ""

            # Resumed within 90-second window
            _RESUME_WINDOW_MS = 90_000
            resumed_at_all = _user_resumed(de)
            within_window = (resumed_at_all
                            and resume_latency_ms != ""
                            and float(resume_latency_ms) <= _RESUME_WINDOW_MS)
            resumed = 1 if within_window else 0

            resume_stage = _determine_resume_stage(de, escalated)
            # If user returned attention but beyond 90-s window, downgrade
            if not within_window and resume_stage != "not_resumed":
                resume_stage = "not_resumed"

            rows.append({
                "event_id": global_event_id,
                "participant_id": pid,
                "session_id": sid,
                "condition": condition,
                "disengagement_onset_ts": start_ts or "",
                "trigger_source": _determine_trigger_source(de, events),
                "robot_nonverbal_start_ts": start_ts or "",
                "robot_nonverbal_type": _determine_nonverbal_type(de),
                "robot_nonverbal_end_ts": motion_end_ts or "",
                "distraction_end_ts": end_ts or "",
                "escalated_to_voice": 1 if escalated else 0,
                "voice_start_ts": voice_info["voice_start_ts"] if voice_info else "",
                "voice_prompt_type": voice_info["voice_prompt_type"] if voice_info else "",
                "voice_prompt_text_length": voice_info["voice_prompt_text_length"] if voice_info else "",
                "highlight_shown": highlight_shown,
                "highlight_shown_ts": highlight_ts,
                "resumed_within_window": resumed,
                "resume_latency_ms": resume_latency_ms,
                "resume_stage": resume_stage,
            })

    return rows


# ────────────────────────── session_summary ──────────────────────────

SESSION_HEADER = [
    "participant_id",
    "session_id",
    "condition",
    "total_session_duration_ms",
    "num_disengagement_events",
    "num_resumed_events",
    "reengagement_success_rate",
    "median_resume_latency_ms",
    "mean_resume_latency_ms",
    "effective_focused_time_ms",
    "effective_focused_time_ratio",
    "num_nonverbal_only_success",
    "num_voice_escalations",
    "num_voice_success",
    "num_highlight_events",
]


def build_session_summary(sessions: list[dict], intervention_rows: list[dict]) -> list[dict]:
    # Index intervention rows by session_id
    by_session: dict[str, list[dict]] = {}
    for r in intervention_rows:
        by_session.setdefault(r["session_id"], []).append(r)

    rows = []
    for sess in sessions:
        sid = sess.get("session_id", "")
        pid = sess.get("participant_id", "")
        condition = sess.get("condition", "with_system")

        total_ms = _ms_between(sess.get("start_time"), sess.get("end_time"))

        events_for_session = by_session.get(sid, [])
        num_events = len(events_for_session)
        # resumed_within_window already encodes the 90-s threshold
        num_resumed = sum(1 for e in events_for_session if e["resumed_within_window"] == 1)
        success_rate = round(num_resumed / num_events, 4) if num_events > 0 else ""

        # Resume latencies (only for resumed events)
        latencies = []
        for e in events_for_session:
            if e["resumed_within_window"] == 1 and e["resume_latency_ms"] != "":
                latencies.append(float(e["resume_latency_ms"]))

        if latencies:
            latencies_sorted = sorted(latencies)
            n = len(latencies_sorted)
            median_lat = latencies_sorted[n // 2] if n % 2 == 1 else round((latencies_sorted[n // 2 - 1] + latencies_sorted[n // 2]) / 2, 1)
            mean_lat = round(sum(latencies) / n, 1)
        else:
            median_lat = ""
            mean_lat = ""

        # Effective focused time = total - sum of distraction durations
        total_distraction_ms = 0
        for e in events_for_session:
            if e["resume_latency_ms"] != "":
                total_distraction_ms += float(e["resume_latency_ms"])
        effective_focused_ms = round(total_ms - total_distraction_ms, 1) if total_ms is not None else ""
        focused_ratio = round(effective_focused_ms / total_ms, 4) if total_ms and effective_focused_ms != "" and total_ms > 0 else ""

        num_nonverbal_only = sum(1 for e in events_for_session if e["resumed_within_window"] == 1 and e["resume_stage"] == "resumed_after_nonverbal")
        num_voice_esc = sum(1 for e in events_for_session if e["escalated_to_voice"] == 1)
        num_voice_success = sum(1 for e in events_for_session if e["escalated_to_voice"] == 1 and e["resumed_within_window"] == 1)
        num_highlight = sum(1 for e in events_for_session if e["highlight_shown"] == 1)

        rows.append({
            "participant_id": pid,
            "session_id": sid,
            "condition": condition,
            "total_session_duration_ms": round(total_ms, 1) if total_ms is not None else "",
            "num_disengagement_events": num_events,
            "num_resumed_events": num_resumed,
            "reengagement_success_rate": success_rate,
            "median_resume_latency_ms": median_lat,
            "mean_resume_latency_ms": mean_lat,
            "effective_focused_time_ms": effective_focused_ms,
            "effective_focused_time_ratio": focused_ratio,
            "num_nonverbal_only_success": num_nonverbal_only,
            "num_voice_escalations": num_voice_esc,
            "num_voice_success": num_voice_success,
            "num_highlight_events": num_highlight,
        })

    return rows


# ────────────────────────── main ──────────────────────────

def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓ {path}  ({len(rows)} rows)")


def main() -> None:
    print("Loading sessions …")
    sessions = load_sessions()
    print(f"  Found {len(sessions)} session files\n")

    print("Building intervention_events …")
    intervention_rows = build_intervention_events(sessions)

    print("Building session_summary …")
    summary_rows = build_session_summary(sessions, intervention_rows)

    out_dir = Path(__file__).resolve().parent
    write_csv(out_dir / "intervention_events.csv", INTERVENTION_HEADER, intervention_rows)
    write_csv(out_dir / "session_summary.csv", SESSION_HEADER, summary_rows)

    print("\nDone!")


if __name__ == "__main__":
    main()
