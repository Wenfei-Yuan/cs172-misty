from __future__ import annotations

from misty2py.basic_skills.speak import speak

from config import Config
from stages.bootup import run_bootup
from stages.distraction import run_distraction
from stages.no_response_prompt import run_no_response
from stages.screen_watch import run_screen_watch
from stages.summary import run_summary
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
            if signal_rx.shutdown_received():
                break

            screen_pos = run_screen_watch(misty, cfg, log, screen_pos)

            event = signal_rx.wait_for_start()
            if event == "shutdown":
                break

            gaze_seen = run_distraction(
                misty,
                cfg,
                log,
                screen_pos,
                should_interrupt=lambda: signal_rx.has_end_event() or signal_rx.shutdown_received(),
            )

            stop_received = signal_rx.end_received()
            if stop_received:
                log.record_distraction_end()

            if signal_rx.shutdown_received():
                break

            if gaze_seen:
                speak(misty, "I see you! Let me check what you were working on.")
                log.record("gaze_recovered")
                attempt = 0
                continue

            if stop_received:
                continue

            attempt += 1
            run_no_response(misty, cfg, log, attempt)
            if attempt >= cfg.max_attempts:
                break
    finally:
        signal_rx.stop()
        run_summary(misty, cfg, log)