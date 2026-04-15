from __future__ import annotations

import threading
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
    stop_event = extra.pop("stop_event", None)
    timeout_s = max(0.1, float(getattr(cfg, "speech_timeout_s", 8.0)))
    result_box: dict[str, Any] = {}
    error_box: dict[str, Exception] = {}
    done = threading.Event()

    if stop_event is not None and stop_event.is_set():
        speech_result = None
        interrupted = True
        timed_out = False
    else:
        def _run_speech() -> None:
            try:
                result_box["speech"] = speak(misty, utterance)
            except Exception as exc:
                error_box["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_run_speech, daemon=True)
        worker.start()

        interrupted = False
        timed_out = False
        while not done.wait(timeout=0.1):
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                break
            if timeout_s > 0 and not worker.is_alive():
                break
            timeout_s -= 0.1
            if timeout_s <= 0:
                timed_out = True
                break

        if done.is_set() and "error" in error_box:
            speech_result = {
                "overall_success": False,
                "rest_response": {"success": False, "message": str(error_box["error"])}
            }
        elif done.is_set():
            speech_result = result_box.get("speech")
        else:
            speech_result = None

    combined = {
        "overall_success": bool(speech_result and speech_result.get("overall_success")) and not interrupted and not timed_out,
        "speech": speech_result,
        "interrupted": interrupted,
        "timed_out": timed_out,
    }
    print("Speech result:", combined)
    if log is not None:
        payload = {
            "stage": stage,
            "utterance": utterance,
            "success": combined["overall_success"],
            "response": speech_result,
            "interrupted": interrupted,
            "timed_out": timed_out,
        }
        payload.update(extra)
        log.record("speech_attempt", **payload)
    return combined