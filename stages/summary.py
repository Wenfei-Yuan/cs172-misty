from __future__ import annotations

from misty2py.basic_skills.speak import speak

from utils.expressions import CLOSE_FACE, SPEAKING_FACE, show_image


def run_summary(misty, cfg, log) -> None:
    summary_text = log.generate_summary()
    show_image(misty, SPEAKING_FACE)
    speak(misty, summary_text)
    show_image(misty, CLOSE_FACE)
    log.save_to_file()
