from __future__ import annotations

import time
from dataclasses import dataclass

from utils.audio import speak_text
from utils.expressions import READING_FACE, SPEAKING_FACE, show_image
from utils.head_control import look_at_screen
from utils.vision import VisionCheckResult, analyze_screen_capture, capture_frame_result


@dataclass(frozen=True)
class ScreenPos:
    yaw: float
    pitch: float


@dataclass(frozen=True)
class ScreenSearchResult:
    status: str
    position: ScreenPos | None = None
    reason: str | None = None


def default_screen_position(cfg) -> ScreenPos:
    return ScreenPos(yaw=cfg.default_screen_yaw, pitch=cfg.default_screen_pitch)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _check_screen_alignment(misty, cfg, log=None, stage: str = "screen_watch") -> VisionCheckResult:
    frame = capture_frame_result(misty)
    if not frame.ok:
        reason = frame.reason or "camera_capture_failed"
        if log is not None:
            log.record("camera_capture_error", stage=stage, reason=reason, source=frame.source)
        return VisionCheckResult(ok=False, status="capture_error", reason=reason)

    result = analyze_screen_capture(frame, cfg)
    if log is not None and not result.ok:
        log.record("vlm_check_error", stage=stage, reason=result.reason, status=result.status)
    return result


def _search_screen_position_with_vlm(misty, cfg, seed: ScreenPos, log=None) -> ScreenSearchResult:
    last_reason = None
    for pitch_offset in cfg.screen_search_pitch_offsets:
        for yaw_offset in cfg.screen_search_yaw_offsets:
            candidate = ScreenPos(
                yaw=_clamp(seed.yaw + yaw_offset, -90.0, 90.0),
                pitch=_clamp(seed.pitch + pitch_offset, -40.0, 40.0),
            )
            look_at_screen(misty, candidate)
            time.sleep(cfg.screen_settle_s)
            result = _check_screen_alignment(misty, cfg, log=log, stage="screen_search")
            last_reason = result.reason or result.status
            if result.status == "aligned":
                return ScreenSearchResult(status="verified", position=candidate)
            if log is not None and result.status == "visible":
                log.record("screen_verification_visible", yaw=candidate.yaw, pitch=candidate.pitch, reason=result.reason)
    if log is not None and last_reason:
        log.record("screen_search_error", reason=last_reason)
    return ScreenSearchResult(status="not_found", reason=last_reason or "screen_not_found")


def find_screen_position_with_vlm(misty, cfg, log=None) -> ScreenSearchResult:
    seed = ScreenPos(
        yaw=_clamp(cfg.screen_init_left_front_yaw, -90.0, 90.0),
        pitch=_clamp(cfg.screen_init_left_front_pitch, -40.0, 40.0),
    )
    look_at_screen(misty, seed)
    time.sleep(cfg.screen_settle_s)
    return _search_screen_position_with_vlm(misty, cfg, seed, log=log)


def run_screen_watch(misty, cfg, log, screen_pos: ScreenPos | None) -> ScreenPos | None:
    if screen_pos is None:
        search = find_screen_position_with_vlm(misty, cfg, log=log)
        position = search.position
    else:
        show_image(misty, READING_FACE)
        position = screen_pos
        look_at_screen(misty, position)
        time.sleep(cfg.screen_settle_s)

    result = _check_screen_alignment(misty, cfg, log=log, stage="screen_watch")
    if result.status != "aligned":
        log.record("screen_verification_error", reason=result.reason or result.status)
        retry = find_screen_position_with_vlm(misty, cfg, log=log)
        position = retry.position
        if position is not None:
            look_at_screen(misty, position)
            time.sleep(cfg.screen_settle_s)
            result = _check_screen_alignment(misty, cfg, log=log, stage="screen_watch_retry")
        else:
            result = VisionCheckResult(ok=False, status="search_exhausted", reason=retry.reason)
    if result.status != "aligned":
        position = None

    show_image(misty, SPEAKING_FACE)
    aligned = result.status == "aligned"
    speak_text(
        misty,
        cfg,
        "I found the screen and aligned my head to face it." if aligned else "I'm still adjusting to find and face your screen.",
        log=log,
        stage="screen_watch",
        aligned=aligned,
    )
    show_image(misty, READING_FACE)

    log.record_screen_observation(
        "Screen alignment verified." if aligned else f"Screen alignment not yet verified ({result.reason or result.status})."
    )
    return position
