from __future__ import annotations

from misty2py.basic_skills.speak import speak

from utils.expressions import SPEAKING_FACE, show_image


def run_no_response(misty, cfg, log, attempt: int) -> None:
    show_image(misty, SPEAKING_FACE)
    prompt = cfg.escalation_prompts[(attempt - 1) % len(cfg.escalation_prompts)]
    speak(misty, prompt)
    log.record_voice_prompt(attempt)
