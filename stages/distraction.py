from __future__ import annotations

import threading
import time
import random
import json
import os
from dataclasses import dataclass
from urllib import error, request

from utils.expressions import DISTRACTION_FACE, JOY_GOOFY_FACE, show_image
from utils.audio import play_audio_file, speak_text
from utils.head_control import (
    look_at_screen,
    perform_distraction_start_sequence,
    perform_head_redirect_only,
    cue_screen_with_left_arm,
)

STAGE2_SOUND_CHOICES = (
    "s_Joy.wav",
    "s_Distraction.wav",
    "s_Acceptance.wav",
)

DEFAULT_HIGHLIGHT_URL = "http://127.0.0.1:8766/extension/highlight_current_sentence"
DEFAULT_READING_MODE_OFFER_URL = "http://127.0.0.1:8766/extension/offer_reading_mode"


@dataclass(frozen=True)
class DistractionResult:
    outcome: str
    sequence_completed: bool = False
    completion_latency_s: float | None = None


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def _stop_thread(stop_event, thread, timeout: float = 2.0) -> None:
    stop_event.set()
    thread.join(timeout=timeout)


def _notify_extension_sentence_highlight(log, duration_ms: int = 10000, source: str = "stage2_confused_sound") -> None:
    highlight_url = os.getenv("HIGHLIGHT_SERVER_URL", DEFAULT_HIGHLIGHT_URL).strip()
    if not highlight_url:
        return

    payload = {
        "type": "highlight_current_sentence",
        "durationMs": duration_ms,
        "source": source,
    }
    req = request.Request(
        highlight_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=1.0) as response:
            detail = response.read().decode("utf-8") or "{}"
        log.record("extension_sentence_highlight_requested", ok=True, duration_ms=duration_ms, source=source, response=detail)
    except (TimeoutError, error.URLError, OSError) as exc:
        log.record("extension_sentence_highlight_requested", ok=False, duration_ms=duration_ms, source=source, reason=str(exc))


def _notify_extension_reading_mode_offer(
    log,
    current_mode: str,
    recommended_mode: str,
    source: str = "stage4_fatigue_support",
) -> bool:
    offer_url = os.getenv("READING_MODE_OFFER_SERVER_URL", DEFAULT_READING_MODE_OFFER_URL).strip()
    if not offer_url:
        return False

    payload = {
        "type": "offer_reading_mode",
        "currentMode": current_mode,
        "recommendedMode": recommended_mode,
        "source": source,
    }
    req = request.Request(
        offer_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=1.0) as response:
            detail = response.read().decode("utf-8") or "{}"
        log.record(
            "fatigue_support_offer_sent",
            ok=True,
            current_mode=current_mode,
            recommended_mode=recommended_mode,
            response=detail,
        )
        return True
    except (TimeoutError, error.URLError, OSError) as exc:
        log.record(
            "fatigue_support_offer_sent",
            ok=False,
            current_mode=current_mode,
            recommended_mode=recommended_mode,
            reason=str(exc),
        )
        return False


def _normalize_reading_mode(mode: str | None) -> str:
    normalized = (mode or "full").strip().lower()
    return normalized if normalized in {"full", "para", "sentence"} else "full"


def _fatigue_recommendation_for_mode(current_mode: str, cfg) -> tuple[str | None, str | None]:
    if current_mode == "sentence":
        return None, None
    if current_mode == "para":
        return "sentence", (
            "This part feels a little dense. We can take it one sentence at a time if that feels easier. "
            "Would you like to switch to sentence mode?"
        )
    default_mode = _normalize_reading_mode(getattr(cfg, "fatigue_default_mode", "para"))
    recommended = default_mode if default_mode in {"para", "sentence"} else "para"
    return recommended, (
        "This article is a bit of a workout. We can make the page feel lighter if that helps. "
        "Would you like to switch to paragraph or sentence mode?"
    )


def run_distraction(misty, cfg, log, screen_pos, consume_interrupt=None, trigger_reason: str | None = None) -> DistractionResult:
    log.record_distraction_start(trigger_reason=trigger_reason)
    show_image(misty, DISTRACTION_FACE)

    motion_done = threading.Event()
    stop_motion = threading.Event()

    def _run_motion() -> None:
        try:
            perform_distraction_start_sequence(misty, cfg, screen_pos, stop_event=stop_motion)
        finally:
            motion_done.set()

    motion_thread = threading.Thread(target=_run_motion, daemon=True)
    motion_thread.start()

    poll_start = time.monotonic()
    outcome = "timeout"
    _POLL_S = 0.1

    try:
        while not motion_done.is_set():
            interrupt = consume_interrupt() if consume_interrupt else None
            if interrupt == "shutdown":
                outcome = "shutdown"
                break
            if interrupt == "stop":
                outcome = "stop"
                break
            elapsed = time.monotonic() - poll_start
            if elapsed >= cfg.gaze_timeout_s:
                outcome = "timeout"
                break
            motion_done.wait(timeout=_POLL_S)
        else:
            outcome = "sequence_complete"
    finally:
        _stop_thread(stop_motion, motion_thread)

    if outcome == "timeout" and screen_pos is not None:
        _pause_before_return_to_screen(cfg)
        look_at_screen(misty, screen_pos)

    log.record_distraction_result(outcome=outcome, sequence_completed=(outcome == "sequence_complete"), latency_s=None)
    return DistractionResult(
        outcome=outcome,
        sequence_completed=(outcome == "sequence_complete"),
        completion_latency_s=None,
    )


# ---------------------------------------------------------------------------
# Staged distraction escalation helpers and result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StagedDistractionResult:
    outcome: str          # "stop" | "shutdown" | "sequence_complete"
    stages_completed: int = 0


def _interruptible_wait(duration_s: float, stop_event, consume_interrupt) -> str | None:
    """Wait up to duration_s, returning early if stop/shutdown detected.

    Polls consume_interrupt() every 0.1 s and also checks stop_event.is_set()
    so that stops pre-set by a prior motion stage (which may have drained the
    deque) are not missed.

    Returns "stop", "shutdown", or None on clean timeout.
    """
    deadline = time.monotonic() + max(0.0, duration_s)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Check deque-based signal first
        if consume_interrupt is not None:
            interrupt = consume_interrupt()
            if interrupt in ("stop", "shutdown"):
                return interrupt
        # Also check persistent stop event (may be set but deque already drained)
        if stop_event is not None and stop_event.is_set():
            return consume_interrupt() or "stop" if consume_interrupt else "stop"
        # Sleep for up to 0.1 s, waking early if stop_event fires
        if stop_event is not None:
            stop_event.wait(timeout=min(0.1, remaining))
        else:
            time.sleep(min(0.1, remaining))
    return None


def _run_stage_in_thread(motion_fn, stop_event, consume_interrupt) -> str | None:
    """Run motion_fn(stop_event=stop_action) in a daemon thread.

    Polls consume_interrupt() and stop_event every 0.1 s. If an interrupt
    arrives, sets stop_action to abort the motion and waits up to 2 s for
    the thread to finish (motion helpers handle their own recovery).

    Returns "stop", "shutdown", or None if motion completed normally.
    """
    stop_action = threading.Event()
    done = threading.Event()

    def _run() -> None:
        try:
            motion_fn(stop_action)
        finally:
            done.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    _POLL_S = 0.1
    while not done.is_set():
        if consume_interrupt is not None:
            interrupt = consume_interrupt()
            if interrupt in ("stop", "shutdown"):
                stop_action.set()
                done.wait(timeout=2.0)
                return interrupt
        if stop_event is not None and stop_event.is_set():
            stop_action.set()
            done.wait(timeout=2.0)
            return consume_interrupt() or "stop" if consume_interrupt else "stop"
        done.wait(timeout=_POLL_S)

    return None


def run_staged_distraction(
    misty,
    cfg,
    log,
    screen_pos,
    consume_interrupt=None,
    stop_event=None,
    current_text_getter=None,
    current_mode_getter=None,
    trigger_reason: str | None = None,
) -> StagedDistractionResult:
    """Staged distraction escalation policy.

    Returns StagedDistractionResult with outcome in:
      "stop"             — user recovered (stop signal received)
      "shutdown"         — shutdown received
      "sequence_complete" — all stages completed without recovery
    """
    from stages.no_response_prompt import run_no_response

    log.record_distraction_start(trigger_reason=trigger_reason)

    stages_done = 0

    def _wait(duration_s: float) -> str | None:
        return _interruptible_wait(duration_s, stop_event, consume_interrupt)

    def _run_motion(fn) -> str | None:
        return _run_stage_in_thread(fn, stop_event, consume_interrupt)

    # ── Stage 1: SELF_RECOVERY_GRACE ─────────────────────────────────────────
    log.record("self_recovery_grace_started",
               wait_s=cfg.initial_self_recovery_wait_s)
    interrupt = _wait(cfg.initial_self_recovery_wait_s)
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="self_recovery_grace")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("self_recovery_grace_cancelled")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("self_recovery_grace_timeout")
    stages_done += 1

    # Grace period passed without recovery — now show distraction face
    show_image(misty, DISTRACTION_FACE)

    # ── Stage 2: CONFUSED_SOUND ──────────────────────────────────────────────
    stage2_sound_name = random.choice(STAGE2_SOUND_CHOICES)
    log.record("confused_sound_prompt_started", sound=stage2_sound_name)
    _notify_extension_sentence_highlight(log, duration_ms=10000)
    sound_result = play_audio_file(misty, cfg, stage2_sound_name, stop_event=stop_event)
    if sound_result.get("Joy"):
        log.record("confused_sound_prompt_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    # Check for stop/shutdown immediately after sound fires
    if consume_interrupt is not None:
        _chk = consume_interrupt()
        if _chk == "shutdown":
            log.record("distraction_shutdown", stage="confused_sound")
            log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
            return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
        if _chk == "stop":
            log.record("confused_sound_prompt_interrupted")
            log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
            return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    if stop_event is not None and stop_event.is_set():
        log.record("confused_sound_prompt_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("confused_sound_prompt_completed")
    stages_done += 1

    # ── Stage 2 wait: WAIT_AFTER_CONFUSED_SOUND ──────────────────────────────
    interrupt = _wait(cfg.confused_sound_followup_wait_s)
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="wait_after_confused_sound")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("distraction_recovered", stage="wait_after_confused_sound")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)

    # ── Stage 3: HEAD_REDIRECT_ONLY + LLM_CONTEXT_VOICE_PROMPT + ARM_WAVE_CUE ─
    show_image(misty, JOY_GOOFY_FACE)
    log.record("head_redirect_only_started")
    _notify_extension_sentence_highlight(log, duration_ms=15000, source="stage3_head_redirect")

    def _run_stage3_head_voice_arm(se) -> None:
        perform_head_redirect_only(misty, cfg, screen_pos, stop_event=se)
        if se is not None and se.is_set():
            return

        current_text = current_text_getter() if current_text_getter is not None else ""
        run_no_response(
            misty, cfg, log,
            attempt=1,
            current_text=current_text,
            stop_event=se,
            include_arm_cue=False,
        )
        if se is not None and se.is_set():
            return

        cue_screen_with_left_arm(misty, cfg, repetitions=1, stop_event=se)

    interrupt = _run_motion(
        _run_stage3_head_voice_arm
    )
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="head_redirect_only")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("head_redirect_only_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("head_redirect_only_completed")
    stages_done += 1

    # ── Stage 3 wait: WAIT_AFTER_HEAD_REDIRECT ────────────────────────────────
    interrupt = _wait(cfg.head_redirect_followup_wait_s)
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="wait_after_head_redirect")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("distraction_recovered", stage="wait_after_head_redirect")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)

    # ── Stage 4: FATIGUE_MANAGEMENT_SUPPORT ──────────────────────────────────
    current_mode = _normalize_reading_mode(current_mode_getter() if current_mode_getter is not None else "full")
    recommended_mode, fatigue_utterance = _fatigue_recommendation_for_mode(current_mode, cfg)
    log.record(
        "fatigue_support_stage_started",
        current_mode=current_mode,
        recommended_mode=recommended_mode,
        enabled=bool(getattr(cfg, "fatigue_offer_enabled", True)),
    )

    if current_mode == "sentence" or not getattr(cfg, "fatigue_offer_enabled", True):
        log.record("fatigue_support_offer_skipped", current_mode=current_mode)
        log.record("staged_distraction_sequence_complete", stages=stages_done)
        log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=None)
        return StagedDistractionResult(outcome="sequence_complete", stages_completed=stages_done)

    log.record("fatigue_support_speech_started", current_mode=current_mode, recommended_mode=recommended_mode)
    interrupt = _run_motion(
        lambda se: speak_text(
            misty,
            cfg,
            fatigue_utterance,
            log=log,
            stage="fatigue_support",
            stop_event=se,
            current_mode=current_mode,
            recommended_mode=recommended_mode,
        )
    )
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="fatigue_support_speech")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("fatigue_support_speech_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("fatigue_support_speech_completed")

    _notify_extension_reading_mode_offer(
        log,
        current_mode=current_mode,
        recommended_mode=recommended_mode or "para",
    )
    stages_done += 1

    # Wait once for confirmation/re-engagement. If nothing happens, do not repeat.
    interrupt = _wait(getattr(cfg, "fatigue_offer_wait_s", 20.0))
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="wait_after_fatigue_support")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("distraction_recovered", stage="wait_after_fatigue_support")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("fatigue_support_offer_timeout", current_mode=current_mode, recommended_mode=recommended_mode)

    log.record("staged_distraction_sequence_complete", stages=stages_done)
    log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=None)
    return StagedDistractionResult(outcome="sequence_complete", stages_completed=stages_done)
