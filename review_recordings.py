#!/usr/bin/env python3
"""
Review session recordings aligned with intervention_events.csv.

For each disengagement event, seeks to the corresponding video frame
and displays it. Use this to verify whether the system's disengagement
detections match what was actually visible on camera.

Usage:
    python review_recordings.py                          # review all
    python review_recordings.py --session <session_id>   # one session
    python review_recordings.py --list                   # list recordings

Time alignment:
    Each recording has a metadata JSON with `video_start_ts` (ISO 8601).
    CSV events have `disengagement_onset_ts` in the same format.
    offset_sec = (event_ts - video_start_ts).total_seconds()
    actual_fps = total_frames / (video_end_ts - video_start_ts)
    target_frame = offset_sec * actual_fps
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import cv2

RECORDINGS_DIR = Path(__file__).parent / "recordings"
EVENTS_CSV = Path(__file__).parent / "intervention_events.csv"


def load_metadata() -> list[dict]:
    """Load all recording metadata JSON files."""
    if not RECORDINGS_DIR.exists():
        return []
    metas = []
    for p in sorted(RECORDINGS_DIR.glob("*.json")):
        with open(p) as f:
            meta = json.load(f)
        meta["_meta_path"] = str(p)
        metas.append(meta)
    return metas


def load_events() -> list[dict]:
    """Load intervention events from CSV."""
    if not EVENTS_CSV.exists():
        print(f"Events CSV not found: {EVENTS_CSV}")
        return []
    with open(EVENTS_CSV) as f:
        return list(csv.DictReader(f))


def find_recording_for_event(event: dict, metas: list[dict]) -> dict | None:
    """Find the recording whose time range covers the event."""
    try:
        event_ts = datetime.fromisoformat(event["disengagement_onset_ts"])
    except (KeyError, ValueError):
        return None

    for meta in metas:
        try:
            start = datetime.fromisoformat(meta["video_start_ts"])
            end = datetime.fromisoformat(meta["video_end_ts"])
        except (KeyError, ValueError):
            continue
        if start <= event_ts <= end:
            return meta
        # Also match by username if timestamps are close
        if meta.get("username") and meta["username"] in event.get("participant_id", ""):
            if abs((event_ts - start).total_seconds()) < 3600:  # within 1 hour
                return meta
    return None


def compute_seek(meta: dict, event_ts: datetime) -> tuple[int, float]:
    """Compute target frame number and offset seconds.

    Returns (frame_number, offset_seconds).
    """
    start = datetime.fromisoformat(meta["video_start_ts"])
    end = datetime.fromisoformat(meta["video_end_ts"])
    total_frames = meta["total_frames"]
    total_duration = (end - start).total_seconds()

    if total_duration <= 0 or total_frames <= 0:
        return 0, 0.0

    actual_fps = total_frames / total_duration
    offset_sec = (event_ts - start).total_seconds()
    target_frame = int(offset_sec * actual_fps)
    target_frame = max(0, min(target_frame, total_frames - 1))

    return target_frame, offset_sec


def list_recordings(metas: list[dict]) -> None:
    """Print summary of all recordings."""
    if not metas:
        print("No recordings found.")
        return
    print(f"\n{'#':<4} {'Username':<12} {'Start Time':<34} {'Frames':>8} {'File'}")
    print("-" * 90)
    for i, m in enumerate(metas, 1):
        start = m.get("video_start_ts", "?")[:26]
        frames = m.get("total_frames", 0)
        fname = m.get("video_file", "?")
        user = m.get("username", "?")
        print(f"{i:<4} {user:<12} {start:<34} {frames:>8} {fname}")
    print()


def review_events(metas: list[dict], events: list[dict],
                   session_filter: str | None = None) -> None:
    """Show video frames at each disengagement event."""
    if not events:
        print("No events found.")
        return

    matched = 0
    for event in events:
        if session_filter and event.get("session_id") != session_filter:
            continue

        meta = find_recording_for_event(event, metas)
        if meta is None:
            continue

        event_ts = datetime.fromisoformat(event["disengagement_onset_ts"])
        target_frame, offset_sec = compute_seek(meta, event_ts)

        video_path = os.path.join(RECORDINGS_DIR, meta["video_file"])
        if not os.path.exists(video_path):
            print(f"  Video file missing: {video_path}")
            continue

        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        cap.release()

        if not ret:
            print(f"  Could not read frame {target_frame}")
            continue

        matched += 1
        eid = event.get("event_id", "?")
        reason = event.get("trigger_source", "?")
        resume = event.get("resume_stage", "?")

        # Add text overlay with event info
        info_lines = [
            f"Event #{eid}  |  {event['disengagement_onset_ts'][:19]}",
            f"Offset: {offset_sec:.1f}s  |  Frame: {target_frame}",
            f"Trigger: {reason}  |  Resume: {resume}",
        ]
        y = 30
        for line in info_lines:
            cv2.putText(frame, line, (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            y += 25

        title = f"Event {eid} @ {offset_sec:.1f}s ({reason})"
        cv2.imshow(title, frame)
        print(f"  Event #{eid}: offset={offset_sec:.1f}s  frame={target_frame}"
              f"  trigger={reason}  resume={resume}")
        print("    Press any key for next, 'q' to quit")

        key = cv2.waitKey(0) & 0xFF
        cv2.destroyAllWindows()
        if key == ord("q"):
            break

    if matched == 0:
        print("No events matched any recording. Check that recordings and CSV cover the same sessions.")
    else:
        print(f"\nReviewed {matched} events.")


def main():
    parser = argparse.ArgumentParser(description="Review session recordings")
    parser.add_argument("--list", action="store_true",
                        help="List all recordings")
    parser.add_argument("--session", type=str, default=None,
                        help="Filter by session_id")
    args = parser.parse_args()

    metas = load_metadata()

    if args.list:
        list_recordings(metas)
        return

    events = load_events()
    review_events(metas, events, args.session)


if __name__ == "__main__":
    main()
