import json, glob
from datetime import datetime

files = sorted(glob.glob('sessions/session_*.json'))

count = 0
for f in sorted(files):
    with open(f) as fh:
        sess = json.load(fh)
    events = sess.get('events', [])
    gaze_events = [e for e in events if e['name'] == 'gaze_sampled_pose']
    if not gaze_events:
        continue
    distractions = sess.get('distraction_events', [])
    for de in distractions:
        start_ts = de.get('distraction_start_time')
        if not start_ts:
            continue
        start_dt = datetime.fromisoformat(start_ts)
        # Find the gaze event CLOSEST to the distraction start (before or after)
        best = None
        best_delta = float('inf')
        for e in gaze_events:
            e_dt = datetime.fromisoformat(e['timestamp'])
            delta = abs((e_dt - start_dt).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best = e
        if best and best_delta < 10:
            p = best.get('payload', {})
            yaw = p.get('yaw_deg')
            status = p.get('gaze_status')
            print(f'{f[-40:]}: yaw={yaw}, status={status}, delta={best_delta:.1f}s')
            count += 1
    if count > 40:
        break

print(f'\nTotal distractions with nearby gaze_sampled_pose: {count}')
