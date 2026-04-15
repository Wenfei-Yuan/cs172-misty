import csv

with open('intervention_events.csv') as f:
    rows = list(csv.DictReader(f))

resumed = sum(1 for r in rows if r['resumed_within_window'] == '1')
not_resumed = sum(1 for r in rows if r['resumed_within_window'] == '0')
voice = sum(1 for r in rows if r['escalated_to_voice'] == '1')
nonverbal_only = sum(1 for r in rows if r['resume_stage'] == 'resumed_after_nonverbal')
after_voice = sum(1 for r in rows if r['resume_stage'] == 'resumed_after_voice')
ctx_aware = sum(1 for r in rows if r['voice_prompt_type'] == 'context_aware')
generic = sum(1 for r in rows if r['voice_prompt_type'] == 'generic')

print(f'Total events: {len(rows)}')
print(f'Resumed: {resumed}, Not resumed: {not_resumed}')
print(f'Escalated to voice: {voice}')
print(f'Resume stage: nonverbal_only={nonverbal_only}, after_voice={after_voice}, not_resumed={not_resumed}')
print(f'Voice prompt type: context_aware={ctx_aware}, generic={generic}')

lats = [float(r['resume_latency_ms']) for r in rows if r['resume_latency_ms']]
print(f'Resume latency: min={min(lats):.0f}ms, max={max(lats):.0f}ms, count={len(lats)}')
