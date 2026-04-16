from __future__ import annotations

from utils.audio import speak_text
from utils.expressions import CLOSE_FACE, SPEAKING_FACE, arm_gesture, show_image


def _regenerate_csvs() -> None:
    """Re-generate analysis CSVs from all session JSONs (best-effort)."""
    try:
        from generate_csv import main as generate_main
        generate_main()
    except Exception as exc:
        print(f"[summary] CSV regeneration failed: {exc}")


def run_summary(misty, cfg, log) -> None:
    summary_text = log.generate_summary()
    show_image(misty, SPEAKING_FACE)
    speak_text(misty, cfg, summary_text, log=log, stage="summary")
    show_image(misty, CLOSE_FACE)
    arm_gesture(misty, "wave")
    log.save_to_file()
    _regenerate_csvs()
