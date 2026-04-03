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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pause_before_return_to_screen(cfg) -> None:
    pause_s = max(0.0, float(getattr(cfg, "return_to_screen_pause_s", 2.0)))
    if pause_s > 0:
        time.sleep(pause_s)


def default_screen_position(cfg) -> ScreenPos:
    return ScreenPos(
        yaw=_clamp(cfg.screen_init_left_front_yaw, -90.0, 90.0),
        pitch=_clamp(cfg.screen_init_left_front_pitch, -40.0, 40.0),
    )


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


def _confirm_alignment(misty, cfg, candidate: ScreenPos, log=None, stage: str = "screen_search") -> bool:
    checks = max(1, int(getattr(cfg, "screen_alignment_confirm_checks", 2)))
    settle_s = max(0.0, float(getattr(cfg, "screen_alignment_confirm_settle_s", cfg.screen_settle_s)))
    for check_idx in range(1, checks):
        time.sleep(settle_s)
        verify = _check_screen_alignment(misty, cfg, log=log, stage=f"{stage}_confirm")
        if verify.status != "aligned":
            if log is not None:
                log.record(
                    "screen_alignment_unstable",
                    yaw=candidate.yaw,
                    pitch=candidate.pitch,
                    check=check_idx + 1,
                    reason=verify.reason or verify.status,
                )
            return False
    return True


def _run_grid_search(
    misty,
    cfg,
    seed: ScreenPos,
    yaw_offsets: tuple[float, ...],
    pitch_offsets: tuple[float, ...],
    log=None,
    stage: str = "screen_search",
) -> tuple[ScreenPos | None, ScreenPos | None, str | None]:
    first_visible: ScreenPos | None = None
    last_reason = None

    for pitch_offset in pitch_offsets:
        for yaw_offset in yaw_offsets:
            candidate = ScreenPos(
                yaw=_clamp(seed.yaw + yaw_offset, -90.0, 90.0),
                pitch=_clamp(seed.pitch + pitch_offset, -40.0, 40.0),
            )
            look_at_screen(misty, candidate)
            time.sleep(cfg.screen_settle_s)
            result = _check_screen_alignment(misty, cfg, log=log, stage=stage)
            last_reason = result.reason or result.status
            if result.status == "aligned" and _confirm_alignment(misty, cfg, candidate, log=log, stage=stage):
                return candidate, first_visible, last_reason
            if result.status == "visible":
                if first_visible is None:
                    first_visible = candidate
                if log is not None:
                    log.record("screen_verification_visible", yaw=candidate.yaw, pitch=candidate.pitch, reason=result.reason)

    return None, first_visible, last_reason


def _search_screen_position_with_vlm(misty, cfg, seed: ScreenPos, log=None) -> ScreenSearchResult:
    coarse_position, first_visible, last_reason = _run_grid_search(
        misty,
        cfg,
        seed=seed,
        yaw_offsets=cfg.screen_search_yaw_offsets,
        pitch_offsets=cfg.screen_search_pitch_offsets,
        log=log,
        stage="screen_search",
    )
    if coarse_position is not None:
        return ScreenSearchResult(status="verified", position=coarse_position)

    fine_seed = first_visible or seed
    fine_yaw_offsets = tuple(getattr(cfg, "screen_search_fine_yaw_offsets", (0.0, -5.0, 5.0, -10.0, 10.0)))
    fine_pitch_offsets = tuple(getattr(cfg, "screen_search_fine_pitch_offsets", (0.0, -4.0, 4.0, -8.0, 8.0)))
    fine_position, _, fine_last_reason = _run_grid_search(
        misty,
        cfg,
        seed=fine_seed,
        yaw_offsets=fine_yaw_offsets,
        pitch_offsets=fine_pitch_offsets,
        log=log,
        stage="screen_search_fine",
    )
    if fine_position is not None:
        return ScreenSearchResult(status="verified", position=fine_position)

    final_reason = fine_last_reason or last_reason or "screen_not_found"
    if log is not None:
        log.record("screen_search_error", reason=final_reason)
    return ScreenSearchResult(status="not_found", reason=final_reason)


def find_screen_position_with_vlm(misty, cfg, log=None) -> ScreenSearchResult:
    seed = ScreenPos(
        yaw=_clamp(cfg.screen_init_left_front_yaw, -90.0, 90.0),
        pitch=_clamp(cfg.screen_init_left_front_pitch, -40.0, 40.0),
    )
    look_at_screen(misty, seed)
    time.sleep(cfg.screen_settle_s)
    return _search_screen_position_with_vlm(misty, cfg, seed, log=log)


def run_screen_watch(misty, cfg, log, screen_pos: ScreenPos | None) -> ScreenPos | None:
    if screen_pos is not None and getattr(cfg, "cache_screen_pos", True):
        show_image(misty, READING_FACE)
        _pause_before_return_to_screen(cfg)
        look_at_screen(misty, screen_pos)
        time.sleep(cfg.screen_settle_s)
        log.record("screen_cache_hit", yaw=screen_pos.yaw, pitch=screen_pos.pitch)
        log.record_screen_observation("Screen alignment verified via cached position.")
        return screen_pos
    if screen_pos is None:
        search = find_screen_position_with_vlm(misty, cfg, log=log)
        position = search.position
    else:
        show_image(misty, READING_FACE)
        position = screen_pos
        _pause_before_return_to_screen(cfg)
        look_at_screen(misty, position)
        time.sleep(cfg.screen_settle_s)

    result = _check_screen_alignment(misty, cfg, log=log, stage="screen_watch")
    if result.status != "aligned":
        log.record("screen_verification_error", reason=result.reason or result.status)
        if screen_pos is None:
            retry = find_screen_position_with_vlm(misty, cfg, log=log)
            position = retry.position
            if position is not None:
                look_at_screen(misty, position)
                time.sleep(cfg.screen_settle_s)
                result = _check_screen_alignment(misty, cfg, log=log, stage="screen_watch_retry")
            else:
                result = VisionCheckResult(ok=False, status="search_exhausted", reason=retry.reason)
        else:
            log.record(
                "screen_position_reused",
                yaw=screen_pos.yaw,
                pitch=screen_pos.pitch,
                source="bootup",
            )
            position = screen_pos
    if result.status != "aligned" and screen_pos is None:
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
