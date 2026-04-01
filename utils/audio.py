from __future__ import annotations

from typing import Any

from misty2py.basic_skills.speak import speak


def _perform_and_parse(misty, action_name: str, data: dict[str, Any] | None = None) -> dict:
    try:
        return misty.perform_action(action_name, data or {}).parse_to_dict()
    except Exception as exc:
        return {
            "overall_success": False,
            "rest_response": {"success": False, "message": str(exc)},
        }


def ensure_audio_ready(misty, cfg) -> dict[str, dict]:
    enable_result = _perform_and_parse(misty, "audio_enable")
    volume = max(0, min(int(cfg.speech_volume), 100))
    volume_result = _perform_and_parse(misty, "volume_settings", {"Volume": volume})
    return {
        "audio_enable": enable_result,
        "volume_settings": volume_result,
    }


def speak_text(misty, cfg, utterance: str, log=None, stage: str | None = None, **extra) -> dict:
    speech_result = speak(misty, utterance)
    combined = {
        "overall_success": bool(speech_result.get("overall_success")),
        "speech": speech_result,
    }
    print("Speech result:", combined)
    if log is not None:
        payload = {
            "stage": stage,
            "utterance": utterance,
            "success": bool(speech_result.get("overall_success")),
            "response": speech_result,
        }
        payload.update(extra)
        log.record("speech_attempt", **payload)
    return combined