import numpy as np
import matplotlib.pyplot as plt

# ===== 数据（已移除 P04）=====
participants = ['P1','P2','P3','P5']

intrusive = np.array([3.0, 4.5, 3.5, 3.0])
support   = np.array([2.5, 4.0, 4.0, 4.0])
body      = np.array([4.0, 2.5, 3.5, 4.5])

data = [intrusive, support, body]
labels = ['Intrusiveness', 'Perceived Support', 'Body-Doubling']

adhd_ids = ['P1','P5']

# per-participant colors matching fig1
participant_colors = {
    'P1': '#F08080',
    'P2': '#F4A460',
    'P3': '#5BC8A8',
    'P5': '#B8A8D8',
}

# ===== 画图 =====
fig, ax = plt.subplots(figsize=(6.5, 4.8))

np.random.seed(0)

# Pre-compute jitter x positions
positions = {}
for i in range(3):
    for j in range(len(participants)):
        positions[(i, j)] = i + np.random.uniform(-0.06, 0.06)

# ===== Step 1: 画 profile 连接线 =====
for j, p in enumerate(participants):
    x_vals = [positions[(i, j)] for i in range(3)]
    y_vals = [data[i][j] for i in range(3)]
    ax.plot(x_vals, y_vals,
            color=participant_colors[p],
            alpha=0.2,
            linewidth=1.2,
            zorder=1)

# ===== Step 2: 画散点 + 错开标签 =====
from collections import defaultdict

for i in range(3):
    # group participants by y value to stagger overlapping labels
    y_groups = defaultdict(list)
    for j, p in enumerate(participants):
        y_groups[data[i][j]].append(j)

    for j, p in enumerate(participants):
        x = positions[(i, j)]
        y = data[i][j]

        group = y_groups[y]
        rank = group.index(j)
        n = len(group)
        # 同 y 值的点水平均匀错开
        x += (rank - (n - 1) / 2) * 0.04

        ax.scatter(x, y,
                   color=participant_colors[p],
                   s=90,
                   marker='^' if p in adhd_ids else 'o',
                   edgecolor='white',
                   linewidth=0.5,
                   zorder=3)

        dy = 0.07 + rank * 0.18   # stagger vertically for overlapping points
        ax.text(x + 0.04, y + dy, p, fontsize=7.5, va='bottom')

# ===== Step 3: mean 线（弱化）=====
for i, metric in enumerate(data):
    mean_val = np.mean(metric)
    ax.plot([i - 0.2, i + 0.2],
            [mean_val, mean_val],
            color='gray',
            linewidth=1.2,
            alpha=0.4,
            zorder=2)

# ===== 坐标轴 =====
ax.set_xticks(range(len(labels)))
ax.set_xticklabels(labels)

ax.set_yticks([1, 2, 3, 4, 5])
ax.set_yticklabels([
    '1 - Strongly Disagree',
    '2 - Disagree',
    '3 - Neutral',
    '4 - Agree',
    '5 - Strongly Agree',
], fontsize=7.5)
ax.set_ylim(0.8, 6.0)

ax.set_title("Post-Session Subjective Ratings")

# ===== 风格优化 =====
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

# ===== 图例：每位参与者 + 形状含义 =====
from matplotlib.lines import Line2D
legend_elements = [
    Line2D([0], [0], marker='^', color='w', markerfacecolor=participant_colors['P1'],
           markersize=8, label='P1 (ADHD)'),
    Line2D([0], [0], marker='o', color='w', markerfacecolor=participant_colors['P2'],
           markersize=8, label='P2'),
    Line2D([0], [0], marker='o', color='w', markerfacecolor=participant_colors['P3'],
           markersize=8, label='P3'),
    Line2D([0], [0], marker='^', color='w', markerfacecolor=participant_colors['P5'],
           markersize=8, label='P5 (ADHD)'),
    Line2D([0], [0], linestyle='none', markersize=0, label=''),   # spacer
]
ax.legend(handles=legend_elements, fontsize=7.5, loc='upper left',
          frameon=False)

plt.tight_layout()
plt.savefig("likert_profile_plot_noP04.pdf", dpi=300)
plt.show()