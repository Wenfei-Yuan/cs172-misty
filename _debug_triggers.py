import json, glob
from datetime import datetime

files = sorted(glob.glob('sessions/session_*.json'))

# 1) Show all unique keys in distraction_events
all_keys = set()
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    for de in sess.get('distraction_events', []):
        all_keys.update(de.keys())
print("All distraction_event keys:", sorted(all_keys))
print()

# 2) Check gaze_sampled_pose payloads: yaw and gaze_status distribution
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    distractions = sess.get('distraction_events', [])
    events = sess.get('events', [])
    if not distractions:
        continue
    gaze_events = [e for e in events if e['name'] == 'gaze_sampled_pose']
    if gaze_events:
        yaws = []
        statuses = set()
        for g in gaze_events:
            p = g.get('payload', {})
            yaw = p.get('yaw_deg')
            status = p.get('gaze_status')
            if yaw is not None:
                yaws.append(round(float(yaw)))
            if status:
                statuses.add(status)
        if yaws:
            print(f'{f}: yaw range [{min(yaws)}, {max(yaws)}], statuses={statuses}, samples={len(gaze_events)}')

print()

# 3) Check what event types exist related to distraction triggers
all_event_names = set()
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    for e in sess.get('events', []):
        all_event_names.add(e['name'])
print("All event names:", sorted(all_event_names))

print()

# 4) Look at distraction-triggering events specifically
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    events = sess.get('events', [])
    distractions = sess.get('distraction_events', [])
    if not distractions:
        continue
    # Look at distraction_detected or similar events
    trigger_events = [e for e in events if 'distraction' in e['name'].lower() or 'trigger' in e['name'].lower()]
    if trigger_events:
        print(f'{f}:')
        for te in trigger_events[:5]:
            print(f'  {te["name"]}: payload={te.get("payload",{})}')

print()

# 5) For each distraction, show the last gaze_sampled_pose before it
print("=== Per-distraction trigger analysis ===")
count = 0
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    events = sess.get('events', [])
    distractions = sess.get('distraction_events', [])
    if not distractions:
        continue
    for de in distractions:
        start_ts = de.get('distraction_start_time')
        if not start_ts:
            continue
        start_dt = datetime.fromisoformat(start_ts)
        last_gaze = None
        for e in events:
            if e['name'] != 'gaze_sampled_pose':
                continue
            e_dt = datetime.fromisoformat(e['timestamp'])
            if e_dt > start_dt:
                break
            last_gaze = e
        if last_gaze:
            p = last_gaze.get('payload', {})
            yaw = p.get('yaw_deg')
            pitch = p.get('pitch_deg')
            status = p.get('gaze_status')
            print(f'  distraction@{start_ts}: yaw={yaw}, pitch={pitch}, status={status}')
            count += 1
        if count > 20:
            break
    if count > 20:
        break
