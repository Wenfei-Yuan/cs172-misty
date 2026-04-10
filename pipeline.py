from __future__ import annotations

from config import Config
from stages.bootup import run_bootup
from stages.distraction import run_distraction
from stages.no_response_prompt import run_no_response
from stages.screen_watch import run_screen_watch, default_screen_position
from stages.summary import run_summary
from utils.audio import ensure_audio_ready, speak_text
from utils.head_control import cue_screen_with_left_arm, redirect_attention_to_screen
from utils.session_id import generate_session_id
from utils.session_log import SessionLog
from utils.triggers import ExternalSignalReceiver


def _wait_until_stop_or_shutdown(signal_rx) -> str:
    while True:
        interrupt = signal_rx.wait_for_stop_or_shutdown(1.0)
        if interrupt in {"stop", "shutdown"}:
            return interrupt


def run(misty, cfg: Config) -> None:
    session_id = generate_session_id(cfg.participant_id)
    log = SessionLog(session_id=session_id, cfg=cfg)
    signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)
    signal_rx.start()

    screen_pos = None
    attempt = 0
    screen_search_retries = 0

    try:
        ensure_audio_ready(misty, cfg)
        screen_pos = run_bootup(misty, cfg, log)
        while True:
            if signal_rx.has_shutdown_event():
                break

            screen_pos = run_screen_watch(misty, cfg, log, screen_pos)
            if screen_pos is None:
                screen_search_retries += 1
                if screen_search_retries >= 3:
                    log.record("screen_search_exhausted", retries=screen_search_retries)
                    speak_text(misty, cfg, "I'm having trouble seeing your screen. Let me try a default position.",
                               log=log, stage="screen_fallback")
                    screen_pos = default_screen_position(cfg)
                    screen_search_retries = 0
                else:
                    log.record("screen_retry_pending", attempt=screen_search_retries)
                continue

            log.record("waiting_for_start_signal")
            start_signal = signal_rx.wait_for_start()
            if start_signal.stale_stop_events_cleared:
                log.record("stale_stop_events_cleared", count=start_signal.stale_stop_events_cleared)
            log.record("start_signal_received", event=start_signal.event)
            if start_signal.event == "shutdown":
                break

            distraction = run_distraction(
                misty,
                cfg,
                log,
                screen_pos,
                consume_interrupt=signal_rx.consume_interrupt,
            )

            if distraction.outcome == "stop":
                log.record_distraction_end()
                attempt = 0
                continue

            if distraction.outcome == "shutdown":
                break

            if distraction.outcome == "gaze":
                log.record("gaze_recovered")
                _pending = signal_rx.consume_interrupt()
                if _pending == "shutdown":
                    break
                if _pending == "stop":
                    log.record_distraction_end()
                    attempt = 0
                    continue

                signal_rx.clear_redirect_stop()
                if screen_pos is not None:
                    redirect_attention_to_screen(misty, cfg, screen_pos, stop_event=signal_rx.redirect_stop_event)
                post_redirect = signal_rx.consume_interrupt()
                if post_redirect == "shutdown":
                    break
                if post_redirect == "stop":
                    log.record_distraction_end()
                    attempt = 0
                    continue

                log.record("gaze_redirect_wait_started", wait_s=cfg.redirect_confirmation_wait_s)
                confirmation = signal_rx.wait_for_stop_or_shutdown(cfg.redirect_confirmation_wait_s)
                if confirmation == "shutdown":
                    break
                if confirmation == "stop":
                    log.record_distraction_end()
                    attempt = 0
                    continue

                log.record("gaze_redirect_wait_timed_out", wait_s=cfg.redirect_confirmation_wait_s)
                signal_rx.clear_redirect_stop()
                run_no_response(
                    misty,
                    cfg,
                    log,
                    attempt + 1,
                    current_text=signal_rx.current_text(),
                    stop_event=signal_rx.redirect_stop_event,
                )
                log.record("gaze_redirect_quiet_wait_started")
                quiet_wait = _wait_until_stop_or_shutdown(signal_rx)
                if quiet_wait == "shutdown":
                    break
                log.record_distraction_end()
                attempt = 0
                continue

            attempt += 1
            signal_rx.clear_redirect_stop()
            run_no_response(misty, cfg, log, attempt, current_text=signal_rx.current_text(), stop_event=signal_rx.redirect_stop_event)
            if attempt >= cfg.max_attempts:
                log.record("max_attempts_reached", attempt=attempt)
                break
    finally:
        signal_rx.stop()
        run_summary(misty, cfg, log)
