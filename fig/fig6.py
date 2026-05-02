import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({"font.size": 9})

# per-participant colors matching fig1
participant_colors = ['#F08080', '#F4A460', '#5BC8A8', '#B8A8D8']  # P1, P2, P3, P5

# ===== 数据（不含 P04）=====
data = {
    'Participant': ['P1', 'P2', 'P3', 'P5'],
    'No System': [55.0, 32.0, 36.7, 53.3],
    'With System': [62.0, 45.0, 55.0, 56.7],
}
# removed is_ADHD column — triangle markers not used
df = pd.DataFrame(data)
df['Delta'] = df['With System'] - df['No System']


x = np.arange(len(df))
width = 0.35

fig, ax = plt.subplots(figsize=(6, 4.5))

# ===== 1. 柱状图 =====
bars1 = ax.bar(x - width/2, df['No System'], width,
               color=participant_colors, alpha=0.45, label='No System')

bars2 = ax.bar(x + width/2, df['With System'], width,
               color=participant_colors, alpha=0.9, label='With System')

# ===== 2. paired slope =====
for i in range(len(df)):
    ax.plot([x[i]-width/2, x[i]+width/2],
            [df['No System'][i], df['With System'][i]],
            color='gray', alpha=0.6, linewidth=1.5)

# ===== 3. Delta 标注 =====
for i, row in df.iterrows():
    ax.text(x[i]+width/2,
            row['With System'] + 2,
            f"+{row['Delta']:.1f}",
            ha='center',
            fontsize=9,
            color='#d62728',
            fontweight='bold')

# ===== 5. 图表修饰 =====
ax.set_xticks(x)
ax.set_xticklabels([f"{p}\n(ADHD)" if p in ('P1', 'P5') else p
                    for p in df['Participant']])

ax.set_ylabel("NASA-TLX (0–100)")
ax.set_title("Cognitive Load: With vs. No System")

ax.set_ylim(0, 100)

ax.legend(frameon=False)

# cleaner look
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

plt.tight_layout()
plt.savefig("cognitive_load.pdf", dpi=300, bbox_inches='tight')
plt.show()