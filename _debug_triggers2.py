import json, glob

files = sorted(glob.glob('sessions/session_*.json'))

# Check all redirect-related events and their payloads
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    events = sess.get('events', [])
    for e in events:
        if 'redirect' in e['name']:
            print(f"{f}: {e['name']} -> {e.get('payload', {})}")
            break
    # Also look for gaze_sampled_pose with different fields
    for e in events:
        if e['name'] == 'gaze_sampled_pose':
            p = e.get('payload', {})
            keys = sorted(p.keys())
            if keys != ['gaze_status', 'yaw_deg']:
                print(f"  DIFFERENT gaze_sampled_pose keys: {keys}")
            break

print()
print("=== Checking gaze_redirect_wait_started events for reason ===")
for f in files:
    with open(f) as fh:
        sess = json.load(fh)
    events = sess.get('events', [])
    for e in events:
        if e['name'] == 'gaze_redirect_wait_started':
            print(f"{f}: {json.dumps(e, indent=2)}")
            break
