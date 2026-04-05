from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import SPEAKING_FACE, show_image
from utils.focus_prompt import generate_focus_reminder_from_text
from utils.head_control import cue_screen_with_left_arm


def run_no_response(misty, cfg, log, attempt: int, current_text: str = "") -> None:
    show_image(misty, SPEAKING_FACE)
    dynamic = generate_focus_reminder_from_text(current_text, cfg)
    prompt = dynamic.reminder
    speak_text(misty, cfg, prompt, log=log, stage="no_response_prompt", attempt=attempt)
    cue_screen_with_left_arm(misty, cfg, repetitions=2)
    log.record(
        "no_response_prompt_generated",
        attempt=attempt,
        summary=dynamic.summary,
        reminder=dynamic.reminder,
        used_fallback=dynamic.used_fallback,
        fallback_reason=dynamic.reason,
        has_current_text=bool((current_text or "").strip()),
    )
    log.record_voice_prompt(attempt)
