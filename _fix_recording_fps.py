"""One-time script to re-mux existing recordings with corrected fps."""
import json
import os
import subprocess

recordings_dir = os.path.join(os.path.dirname(__file__), "recordings")

for fn in sorted(os.listdir(recordings_dir)):
    if not fn.endswith(".json"):
        continue
    meta_path = os.path.join(recordings_dir, fn)
    with open(meta_path) as f:
        meta = json.load(f)

    actual_fps = meta.get("nominal_fps", 0)
    writer_fps = meta.get("writer_fps", 15)  # old files don't have writer_fps
    video_file = os.path.join(recordings_dir, meta.get("video_file", ""))

    if actual_fps <= 0:
        print(f"Skip {fn}: actual_fps={actual_fps}")
        continue
    if not os.path.exists(video_file):
        print(f"Skip {fn}: video file missing ({video_file})")
        continue
    if abs(actual_fps - writer_fps) < 0.5:
        print(f"Skip {fn}: fps already close ({actual_fps:.2f} vs writer {writer_fps})")
        continue

    print(f"Fix {fn}: {writer_fps}fps writer → {actual_fps:.2f}fps actual", flush=True)
    tmp = video_file + ".remux.avi"
    result = subprocess.run(
        ["ffmpeg", "-y", "-r", f"{actual_fps:.4f}", "-i", video_file, "-c", "copy", tmp],
        capture_output=True,
        timeout=300,
    )
    if result.returncode == 0:
        os.replace(tmp, video_file)
        print(f"  → Done!")
    else:
        err = result.stderr.decode(errors="replace")[:200]
        print(f"  → ffmpeg failed (rc={result.returncode}): {err}")
        if os.path.exists(tmp):
            os.remove(tmp)

print("Done.")
