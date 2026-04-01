from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import SPEAKING_FACE, show_image


def run_no_response(misty, cfg, log, attempt: int) -> None:
    if not cfg.escalation_prompts:
        return
    show_image(misty, SPEAKING_FACE)
    idx = max(0, min(attempt - 1, len(cfg.escalation_prompts) - 1))
    prompt = cfg.escalation_prompts[idx]
    speak_text(misty, cfg, prompt, log=log, stage="no_response_prompt", attempt=attempt)
    log.record_voice_prompt(attempt)
