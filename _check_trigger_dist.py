import csv

with open('intervention_events.csv') as f:
    reader = csv.DictReader(f)
    counts = {}
    for row in reader:
        src = row['trigger_source']
        counts[src] = counts.get(src, 0) + 1

for k, v in sorted(counts.items()):
    print(f'{k}: {v}')
print(f'Total: {sum(counts.values())}')
