from __future__ import annotations

from config import Config
from stages.bootup import run_bootup
from stages.distraction import run_staged_distraction
from stages.screen_watch import run_screen_watch
from stages.summary import run_summary
from utils.audio import ensure_audio_ready
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


def run(misty, cfg: Config) -> None:
    session_id = generate_session_id(cfg.participant_id)
    log = SessionLog(session_id=session_id, cfg=cfg)
    signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)
    signal_rx.start()

    screen_pos = None
    attempt = 0
    skip_screen_watch_once = False

    try:
        ensure_audio_ready(misty, cfg)
        screen_pos = run_bootup(misty, cfg, log)
        while True:
            if signal_rx.has_shutdown_event():
                _close_active_distraction(log, "shutdown")
                break

            if skip_screen_watch_once:
                skip_screen_watch_once = False
            else:
                screen_pos = run_screen_watch(misty, cfg, log, screen_pos)

            log.record("waiting_for_start_signal")
            start_signal = signal_rx.wait_for_start()
            if start_signal.stale_stop_events_cleared:
                log.record("stale_stop_events_cleared", count=start_signal.stale_stop_events_cleared)
            log.record(
                "start_signal_received",
                event=start_signal.event,
                trigger_reason=start_signal.trigger_reason,
                received_at=start_signal.received_at,
            )
            if start_signal.event == "shutdown":
                _close_active_distraction(log, "shutdown")
                break

            # Start each distraction episode with a clean stop latch so a previous
            # stop signal cannot suppress the new motion/prompt sequence.
            signal_rx.clear_redirect_stop()

            distraction = run_staged_distraction(
                misty,
                cfg,
                log,
                screen_pos,
                consume_interrupt=signal_rx.consume_interrupt,
                stop_event=signal_rx.redirect_stop_event,
                current_text_getter=signal_rx.current_text,
                current_mode_getter=signal_rx.current_mode,
                trigger_reason=start_signal.trigger_reason,
                start_time=start_signal.received_at,
                signal_rx=signal_rx,
            )

            if distraction.outcome == "stop":
                # run_staged_distraction already logged the return, and the head is
                # already on the screen: the only motion that leaves it (stage 3)
                # brings it back itself when interrupted.
                signal_rx.clear_redirect_stop()
                attempt = 0
                skip_screen_watch_once = True
                continue

            if distraction.outcome == "shutdown":
                _close_active_distraction(log, "shutdown")
                break

            if distraction.outcome == "sequence_complete":
                log.record("staged_sequence_completed", stages=distraction.stages_completed)
                _pending = signal_rx.consume_stop_or_shutdown()
                if _pending == "shutdown":
                    _close_active_distraction(log, "shutdown")
                    break
                if _pending == "stop":
                    signal_rx.clear_redirect_stop()
                    log.record_distraction_end(end_time=signal_rx.last_stop_time(), recovered_at_stage="fatigue_support")
                    attempt = 0
                    skip_screen_watch_once = True
                    continue

                log.record("post_staged_quiet_wait_started")
                quiet_wait = _wait_until_stop_or_shutdown(signal_rx)
                if quiet_wait == "shutdown":
                    _close_active_distraction(log, "shutdown")
                    break
                signal_rx.clear_redirect_stop()
                log.record_distraction_end(end_time=signal_rx.last_stop_time(), recovered_at_stage="fatigue_support")
                attempt = 0
                skip_screen_watch_once = True
                continue

    except Exception as exc:
        log.record("pipeline_error", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        signal_rx.stop()
        run_summary(misty, cfg, log)
