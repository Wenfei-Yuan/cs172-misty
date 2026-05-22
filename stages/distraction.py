from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from utils.expressions import DISTRACTION_FACE, JOY_GOOFY_FACE, show_image
from utils.audio import play_audio_file
from utils.head_control import (
    look_at_screen,
    perform_distraction_start_sequence,
    perform_head_redirect_only,
    cue_screen_with_left_arm,
)


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
    trigger_reason: str | None = None,
) -> StagedDistractionResult:
    """Staged 8-step distraction escalation policy.

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
    log.record("confused_sound_prompt_started", sound=cfg.confused_sound_name)
    sound_result = play_audio_file(misty, cfg, cfg.confused_sound_name, stop_event=stop_event)
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

    # ── Stage 3: HEAD_REDIRECT_ONLY ──────────────────────────────────────────
    show_image(misty, JOY_GOOFY_FACE)
    log.record("head_redirect_only_started")
    interrupt = _run_motion(
        lambda se: perform_head_redirect_only(misty, cfg, screen_pos, stop_event=se)
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

    # ── Stage 4: LLM_CONTEXT_VOICE_PROMPT ────────────────────────────────────
    current_text = current_text_getter() if current_text_getter is not None else ""
    log.record("llm_voice_prompt_started")
    interrupt = _run_motion(
        lambda se: run_no_response(
            misty, cfg, log,
            attempt=1,
            current_text=current_text,
            stop_event=se,
            include_arm_cue=False,
        )
    )
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="llm_voice_prompt")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("llm_voice_prompt_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("llm_voice_prompt_completed")
    stages_done += 1

    # ── Stage 4 wait: WAIT_AFTER_VOICE_PROMPT ────────────────────────────────
    interrupt = _wait(cfg.voice_prompt_followup_wait_s)
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="wait_after_voice_prompt")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("distraction_recovered", stage="wait_after_voice_prompt")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)

    # ── Stage 5: ARM_WAVE_CUE ────────────────────────────────────────────────
    log.record("arm_wave_cue_started")
    interrupt = _run_motion(
        lambda se: cue_screen_with_left_arm(misty, cfg, repetitions=1, stop_event=se)
    )
    if interrupt == "shutdown":
        log.record("distraction_shutdown", stage="arm_wave_cue")
        log.record_distraction_result(outcome="shutdown", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="shutdown", stages_completed=stages_done)
    if interrupt == "stop":
        log.record("arm_wave_cue_interrupted")
        log.record_distraction_result(outcome="stop", sequence_completed=False, latency_s=None)
        return StagedDistractionResult(outcome="stop", stages_completed=stages_done)
    log.record("arm_wave_cue_completed")
    stages_done += 1

    log.record("staged_distraction_sequence_complete", stages=stages_done)
    log.record_distraction_result(outcome="sequence_complete", sequence_completed=True, latency_s=None)
    return StagedDistractionResult(outcome="sequence_complete", stages_completed=stages_done)
