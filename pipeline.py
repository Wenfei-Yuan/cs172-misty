from __future__ import annotations

from config import Config
from stages.bootup import run_bootup
from stages.distraction import run_distraction
from stages.no_response_prompt import run_no_response
from stages.screen_watch import run_screen_watch, default_screen_position
from stages.summary import run_summary
from utils.audio import ensure_audio_ready, speak_text
from utils.head_control import look_at_screen
from utils.session_id import generate_session_id
from utils.session_log import SessionLog
from utils.triggers import ExternalSignalReceiver


def _wait_until_stop_or_shutdown(signal_rx) -> str:
    while True:
        interrupt = signal_rx.wait_for_stop_or_shutdown_only(1.0)
        if interrupt in {"stop", "shutdown"}:
            return interrupt


def _close_active_distraction(log, reason: str) -> None:
    close_active = getattr(log, "close_active_distraction", None)
    if callable(close_active):
        close_active(reason=reason)


def _return_to_waiting_position(misty, cfg, log, bootup_screen_pos, current_screen_pos) -> None:
    target_screen_pos = bootup_screen_pos or current_screen_pos or default_screen_position(cfg)
    look_at_screen(misty, target_screen_pos)
    log.record(
        "returned_to_waiting_position",
        yaw=target_screen_pos.yaw,
        pitch=target_screen_pos.pitch,
        source="bootup" if bootup_screen_pos is not None else ("current_screen" if current_screen_pos is not None else "default_fallback"),
    )


def run(misty, cfg: Config) -> None:
    session_id = generate_session_id(cfg.participant_id)
    log = SessionLog(session_id=session_id, cfg=cfg)
    signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)
    signal_rx.start()

    screen_pos = None
    attempt = 0
    screen_search_retries = 0
    skip_screen_watch_once = False

    try:
        ensure_audio_ready(misty, cfg)
        screen_pos = run_bootup(misty, cfg, log)
        bootup_screen_pos = screen_pos
        while True:
            if signal_rx.has_shutdown_event():
                _close_active_distraction(log, "shutdown")
                break

            if skip_screen_watch_once:
                skip_screen_watch_once = False
            else:
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
            log.record("start_signal_received", event=start_signal.event, trigger_reason=start_signal.trigger_reason)
            if start_signal.event == "shutdown":
                _close_active_distraction(log, "shutdown")
                break

            # Start each distraction episode with a clean stop latch so a previous
            # stop signal cannot suppress the new motion/prompt sequence.
            signal_rx.clear_redirect_stop()

            distraction = run_distraction(
                misty,
                cfg,
                log,
                screen_pos,
                consume_interrupt=signal_rx.consume_interrupt,
                trigger_reason=start_signal.trigger_reason,
            )

            if distraction.outcome == "stop":
                signal_rx.clear_redirect_stop()
                _return_to_waiting_position(misty, cfg, log, bootup_screen_pos, screen_pos)
                log.record_distraction_end()
                attempt = 0
                skip_screen_watch_once = True
                continue

            if distraction.outcome == "shutdown":
                _close_active_distraction(log, "shutdown")
                break

            if distraction.outcome == "sequence_complete":
                log.record("sequence_completed")
                _pending = signal_rx.consume_stop_or_shutdown()
                if _pending == "shutdown":
                    _close_active_distraction(log, "shutdown")
                    break
                if _pending == "stop":
                    signal_rx.clear_redirect_stop()
                    log.record_distraction_end()
                    attempt = 0
                    skip_screen_watch_once = True
                    continue

                log.record("post_sequence_wait_started", wait_s=cfg.redirect_confirmation_wait_s)
                confirmation = signal_rx.wait_for_stop_or_shutdown_only(cfg.redirect_confirmation_wait_s)
                if confirmation == "shutdown":
                    _close_active_distraction(log, "shutdown")
                    break
                if confirmation == "stop":
                    signal_rx.clear_redirect_stop()
                    log.record_distraction_end()
                    attempt = 0
                    skip_screen_watch_once = True
                    continue

                log.record("post_sequence_wait_timed_out", wait_s=cfg.redirect_confirmation_wait_s)
                signal_rx.clear_redirect_stop()
                run_no_response(
                    misty,
                    cfg,
                    log,
                    attempt + 1,
                    current_text=signal_rx.current_text(),
                    stop_event=signal_rx.redirect_stop_event,
                )
                log.record("post_sequence_quiet_wait_started")
                quiet_wait = _wait_until_stop_or_shutdown(signal_rx)
                if quiet_wait == "shutdown":
                    _close_active_distraction(log, "shutdown")
                    break
                signal_rx.clear_redirect_stop()
                log.record_distraction_end()
                attempt = 0
                skip_screen_watch_once = True
                continue

            attempt += 1
            signal_rx.clear_redirect_stop()
            run_no_response(misty, cfg, log, attempt, current_text=signal_rx.current_text(), stop_event=signal_rx.redirect_stop_event)
            if attempt >= cfg.max_attempts:
                log.record("max_attempts_reached", attempt=attempt)
                _close_active_distraction(log, "max_attempts")
                break
    except Exception as exc:
        log.record("pipeline_error", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        signal_rx.stop()
        run_summary(misty, cfg, log)
