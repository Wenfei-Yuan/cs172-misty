from __future__ import annotations

import threading
import time
import random
import json
import os
from dataclasses import dataclass
from urllib import error, request

from stages.no_response_prompt import speak_no_response_prompt
from utils.expressions import DISTRACTION_FACE, JOY_GOOFY_FACE, SPEAKING_FACE, show_image
from utils.audio import play_audio_file, speak_text, stop_audio
from utils.focus_prompt import fallback_focus_prompt, generate_focus_reminder_from_text
from utils.head_control import (
    look_at_screen,
    perform_distraction_start_sequence,
    perform_head_redirect_only,
    cue_screen_with_left_arm,
    reset_arms_down,
)
from utils.session_log import local_iso, seconds_between

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


def _notify_extension_sentence_highlight(log, duration_ms: int = 10000, source: str = "stage2_confused_sound") -> dict:
    """Ask the extension to highlight the current sentence. Returns {"sent", "ok", "time"}."""
    highlight_url = os.getenv("HIGHLIGHT_SERVER_URL", DEFAULT_HIGHLIGHT_URL).strip()
    if not highlight_url:
        return {"sent": False, "ok": False, "time": None}

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
    sent_at = local_iso()
    try:
        with request.urlopen(req, timeout=1.0) as response:
            detail = response.read().decode("utf-8") or "{}"
        log.record("extension_sentence_highlight_requested", ok=True, duration_ms=duration_ms, source=source, response=detail)
        return {"sent": True, "ok": True, "time": sent_at}
    except (TimeoutError, error.URLError, OSError) as exc:
        log.record("extension_sentence_highlight_requested", ok=False, duration_ms=duration_ms, source=source, reason=str(exc))
        return {"sent": True, "ok": False, "time": sent_at}


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


class _RecapGeneration:
    """Generates the recap in the background so it can be spoken right after a return.

    The result is simply left unused when the participant returns during the motion.
    """

    def __init__(self, reading_text: str, cfg):
        self.reading_text = reading_text
        self.done = threading.Event()
        self._result = None
        self._error = None
        threading.Thread(target=self._run, args=(cfg,), daemon=True).start()

    def _run(self, cfg) -> None:
        try:
            self._result = generate_focus_reminder_from_text(self.reading_text, cfg)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self.done.set()

    def prompt_or_fallback(self):
        if not self.done.is_set():
            return fallback_focus_prompt("generation_timeout")
        return self._result or fallback_focus_prompt(self._error or "generation_failed")


def _estimated_speech_s(text: str, cfg) -> float:
    words_per_s = max(0.1, float(cfg.recap_speech_words_per_s))
    return len(text.split()) / words_per_s + max(0.0, float(cfg.recap_speech_end_padding_s))


def _recap_cancel_reason(signal_rx) -> str | None:
    """Peek at the queue. A pending start must stay there for the pipeline to pick up."""
    if signal_rx is None:
        return None
    if signal_rx.has_shutdown_event():
        return "shutdown"
    if signal_rx.has_pending_start():
        return "start"
    return None


def _deliver_post_return_recap(misty, cfg, log, generation: _RecapGeneration, reengaged_at: str, signal_rx) -> None:
    """Speak the recap after a return during the stage 3 wait.

    The delay runs from the stop itself. No stop_event is passed to the speech: it is
    still set by that same stop, and the recap is never interrupted once it starts.
    """
    remaining_delay_s = float(cfg.recap_delay_after_return_s) - seconds_between(reengaged_at, local_iso())
    speak_at = time.monotonic() + max(0.0, remaining_delay_s)
    give_up_on_generation_at = speak_at + max(0.0, float(cfg.recap_generation_wait_s))

    while True:
        cancel_reason = _recap_cancel_reason(signal_rx)
        if cancel_reason == "shutdown":
            log.record("post_return_recap_cancelled", reason="shutdown")
            log.record_post_return_recap(status="cancelled_shutdown")
            return
        if cancel_reason == "start":
            log.record("post_return_recap_cancelled", reason="new_disengagement")
            log.record_post_return_recap(status="cancelled_new_disengagement")
            return
        now = time.monotonic()
        if now >= speak_at and (generation.done.is_set() or now >= give_up_on_generation_at):
            break
        time.sleep(0.05)

    dynamic = generation.prompt_or_fallback()
    show_image(misty, SPEAKING_FACE)
    recap_started_at = local_iso()
    speech = speak_no_response_prompt(
        misty, cfg, log,
        attempt=1,
        dynamic=dynamic,
        current_text=generation.reading_text,
        include_arm_cue=False,
    )
    played = bool(speech.get("overall_success"))
    if played:
        # speak_text returns while Misty is still talking; hold the pipeline until the
        # recap ends so signals received during it are processed afterwards.
        time.sleep(_estimated_speech_s(dynamic.reminder, cfg))
    log.record_post_return_recap(
        status="played" if played else "speech_failed",
        start_time=recap_started_at,
        delay_from_return_s=seconds_between(reengaged_at, recap_started_at),
        used_fallback=dynamic.used_fallback,
        end_time=local_iso(),
        disengaged_during_recap=played and signal_rx is not None and signal_rx.has_pending_start(),
    )


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
    start_time: str | None = None,
    signal_rx=None,
) -> StagedDistractionResult:
    """Staged distraction escalation policy.

    start_time is when the start signal arrived. signal_rx supplies the receipt time of
    stops and lets the post-return recap watch the queue without consuming from it.

    Returns StagedDistractionResult with outcome in:
      "stop"             — user recovered (stop signal received)
      "shutdown"         — shutdown received
      "sequence_complete" — all stages completed without recovery
    """
    log.record_distraction_start(trigger_reason=trigger_reason, start_time=start_time)

    stages_done = 0

    def _wait(duration_s: float) -> str | None:
        return _interruptible_wait(duration_s, stop_event, consume_interrupt)

    def _run_motion(fn) -> str | None:
        return _run_stage_in_thread(fn, stop_event, consume_interrupt)

    def _pending_interrupt() -> str | None:
        interrupt = consume_interrupt() if consume_interrupt is not None else None
        if interrupt in ("stop", "shutdown"):
            return interrupt
        if stop_event is not None and stop_event.is_set():
            return "stop"
        return None

    def _end_at_return(recovered_at_stage: str, highlight_source: str | None = None) -> str:
        """Close the event at the moment of the stop, then highlight. Returns that moment."""
        reengaged_at = (signal_rx.last_stop_time() if signal_rx is not None else None) or local_iso()
        log.record_distraction_end(end_time=reengaged_at, recovered_at_stage=recovered_at_stage)
        if highlight_source is not None:
            highlight = _notify_extension_sentence_highlight(
                log, duration_ms=cfg.return_highlight_ms, source=highlight_source,
            )
            log.record_post_return_highlight(**highlight, duration_ms=cfg.return_highlight_ms)
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return reengaged_at

    def _end_at_sound_return() -> None:
        # play_audio_file is fire-and-forget, so we cannot tell whether the sound is
        # still playing. Stopping is harmless if it already finished.
        log.record("confused_sound_stop_requested", ok=bool(stop_audio(misty).get("overall_success")))
        _end_at_return("sound", highlight_source="stage2_return")

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
        _end_at_return("self_recovery")
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("self_recovery_grace_timeout")
    stages_done += 1

    # Grace period passed without recovery — now show distraction face
    show_image(misty, DISTRACTION_FACE)

    # ── Stage 2: CONFUSED_SOUND ──────────────────────────────────────────────
    stage2_sound_name = random.choice(STAGE2_SOUND_CHOICES)
    log.record("confused_sound_prompt_started", sound=stage2_sound_name)
    sound_result = play_audio_file(misty, cfg, stage2_sound_name, stop_event=stop_event)
    # Check for stop/shutdown immediately after sound fires
    interrupt = _pending_interrupt()
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="confused_sound")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop" or sound_result.get("interrupted"):
        log.record("confused_sound_prompt_interrupted")
        _end_at_sound_return()
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
        _end_at_sound_return()
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)

    # ── Stage 3: HEAD_REDIRECT_ONLY + ARM_WAVE_CUE ───────────────────────────
    show_image(misty, JOY_GOOFY_FACE)
    log.record("head_redirect_only_started")
    recap_generation = _RecapGeneration(current_text_getter() if current_text_getter is not None else "", cfg)

    def _run_stage3_head_arm(se) -> None:
        perform_head_redirect_only(misty, cfg, screen_pos, stop_event=se)
        if se.is_set():
            return

        cue_screen_with_left_arm(misty, cfg, repetitions=1, stop_event=se)
        if se.is_set():
            # An interrupted cue returns with the arm still raised.
            reset_arms_down(misty, cfg)

    interrupt = _run_motion(_run_stage3_head_arm)
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="head_redirect_only")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("head_redirect_only_interrupted")
        _end_at_return("head_redirect_motion", highlight_source="stage3_motion_return")
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
        reengaged_at = _end_at_return("head_redirect_wait", highlight_source="stage3_return")
        _deliver_post_return_recap(misty, cfg, log, recap_generation, reengaged_at, signal_rx)
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
        _end_at_return("fatigue_support")
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
        _end_at_return("fatigue_support")
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("fatigue_support_offer_timeout", current_mode=current_mode, recommended_mode=recommended_mode)

    log.record("staged_distraction_sequence_complete", stages=stages_done)
    log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=None)
    return StagedDistractionResult(outcome="sequence_complete", stages_completed=stages_done)
