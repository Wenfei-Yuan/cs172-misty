from __future__ import annotations

from config import Config
from stages.bootup import run_bootup
from stages.distraction import run_distraction
from stages.no_response_prompt import run_no_response
from stages.screen_watch import run_screen_watch
from stages.summary import run_summary
from utils.audio import speak_text
from utils.session_id import generate_session_id
from utils.session_log import SessionLog
from utils.triggers import ExternalSignalReceiver


def run(misty, cfg: Config) -> None:
    session_id = generate_session_id(cfg.participant_id)
    log = SessionLog(session_id=session_id, cfg=cfg)
    signal_rx = ExternalSignalReceiver(cfg.signal_host, cfg.signal_port)
    signal_rx.start()

    screen_pos = None
    attempt = 0

    try:
        screen_pos = run_bootup(misty, cfg, log)
        while True:
            if signal_rx.has_shutdown_event():
                break

            screen_pos = run_screen_watch(misty, cfg, log, screen_pos)
            if screen_pos is None:
                log.record("screen_retry_pending")
                continue

            speak_text(
                misty,
                cfg,
                "Let's start reading, I am ready.",
                log=log,
                stage="screen_watch_success",
            )

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

            if distraction.outcome == "shutdown":
                break

            if distraction.outcome == "gaze":
                speak_text(misty, cfg, "I see you! Let me check what you were working on.", log=log, stage="gaze_recovered")
                log.record("gaze_recovered")
                attempt = 0
                continue

            if distraction.outcome == "stop":
                attempt = 0
                continue

            attempt += 1
            run_no_response(misty, cfg, log, attempt)
    finally:
        signal_rx.stop()
        run_summary(misty, cfg, log)