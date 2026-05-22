from __future__ import annotations

import threading

from utils.audio import speak_text
from utils.expressions import SPEAKING_FACE, show_image
from utils.focus_prompt import generate_focus_reminder_from_text
from utils.head_control import cue_screen_with_left_arm


def _run_until_done_or_stop(stop_event, func, *args, **kwargs):
    if stop_event is None:
        return False, func(*args, **kwargs)
    if stop_event.is_set():
        return True, None

    result_box = {}
    error_box = {}
    done = threading.Event()

    def _run() -> None:
        try:
            result_box["value"] = func(*args, **kwargs)
        except Exception as exc:
            error_box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    while not done.wait(timeout=0.1):
        if stop_event.is_set():
            return True, None
    if "error" in error_box:
        raise error_box["error"]
    return False, result_box.get("value")


def run_no_response(misty, cfg, log, attempt: int, current_text: str = "", stop_event=None, include_arm_cue: bool = True) -> None:
    if stop_event is not None and stop_event.is_set():
        log.record("no_response_skipped", attempt=attempt, reason="stop_received_before_prompt")
        return

    show_image(misty, SPEAKING_FACE)
    interrupted, dynamic = _run_until_done_or_stop(stop_event, generate_focus_reminder_from_text, current_text, cfg)
    if interrupted:
        log.record("no_response_skipped", attempt=attempt, reason="stop_received_during_prompt_generation")
        return

    prompt = dynamic.reminder
    speech = speak_text(misty, cfg, prompt, log=log, stage="no_response_prompt", attempt=attempt, stop_event=stop_event) or {}
    if speech.get("interrupted"):
        log.record("no_response_skipped", attempt=attempt, reason="stop_received_during_speech")
        return
    if include_arm_cue:
        if stop_event is not None and stop_event.is_set():
            log.record("no_response_skipped", attempt=attempt, reason="stop_received_before_arm_cue")
            return
        cue_screen_with_left_arm(misty, cfg, repetitions=1, stop_event=stop_event)
    log.record(
        "no_response_prompt_generated",
        attempt=attempt,
        summary=dynamic.summary,
        reminder=dynamic.reminder,
        used_fallback=dynamic.used_fallback,
        fallback_reason=dynamic.reason,
        has_current_text=bool((current_text or "").strip()),
    )
    log.record_voice_prompt()
