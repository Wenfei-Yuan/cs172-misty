from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import SPEAKING_FACE, show_image


def run_no_response(misty, cfg, log, attempt: int) -> None:
    show_image(misty, SPEAKING_FACE)
    prompt = cfg.escalation_prompts[0]
    speak_text(misty, cfg, prompt, log=log, stage="no_response_prompt", attempt=attempt)
    log.record_voice_prompt(attempt)
