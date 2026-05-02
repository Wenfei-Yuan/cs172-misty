import matplotlib.pyplot as plt
import numpy as np

# ===== 数据 =====
participants = ['P1','P2','P3','P5']

focus_base = np.array([0.352, 0.955, 0.7387, 0.7499])
focus_sys  = np.array([0.884, 0.756, 0.8058, 0.9444])

delta_attention = focus_sys - focus_base

delta_self = np.array([
    0.0,    # P1
    0.0,    # P2
   -0.33,   # P3
    1.0     # P5
])

# ===== 配色与 fig1 一致 =====
colors = ['#F08080', '#F4A460', '#5BC8A8', '#B8A8D8']  # P1, P2, P3, P5

# ===== 画图 =====
fig, ax = plt.subplots(figsize=(4.8, 4.8))

# scatter — ADHD 用实心三角形，非 ADHD 用实心圆形
adhd_ids = {'P1', 'P5'}
legend_adhd = legend_nonadhd = None
for i, p in enumerate(participants):
    if p in adhd_ids:
        sc = ax.scatter(delta_attention[i], delta_self[i],
                        s=90, c=[colors[i]], marker='^', zorder=3)
        if legend_adhd is None:
            legend_adhd = sc
    else:
        sc = ax.scatter(delta_attention[i], delta_self[i],
                        s=70, c=[colors[i]], marker='o', zorder=3)
        if legend_nonadhd is None:
            legend_nonadhd = sc

# label 每个点
for i, p in enumerate(participants):
    ax.text(delta_attention[i] + 0.01,
            delta_self[i] + 0.02,
            p,
            fontsize=9)

# 图例：三角 = ADHD，圆 = 非 ADHD
ax.scatter([], [], marker='^', color='gray', s=60, label='ADHD')
ax.scatter([], [], marker='o', color='gray', s=50, label='Non-ADHD')
ax.legend(title='P1–P5', loc='upper right', bbox_to_anchor=(1.0, 0.82),
          fontsize=7, title_fontsize=7,
          handlelength=1, handletextpad=0.4, borderpad=0.5, labelspacing=0.3)

# ===== 四象限线 =====
ax.axhline(0, linestyle='--', color='gray', linewidth=1)
ax.axvline(0, linestyle='--', color='gray', linewidth=1)

# ===== 象限文字 =====
# 注意：用 axes fraction 坐标，避免随数据变化跑偏
ax.text(0.98, 0.95, "Aligned improvement",
        transform=ax.transAxes,
        ha='right', va='top',
        fontsize=9, color='darkgreen')

ax.text(0.98, 0.05, "Unperceived improvement",
        transform=ax.transAxes,
        ha='right', va='bottom',
        fontsize=9, color='#1f4e79')

ax.text(0.02, 0.95, "Perceived but not real",
        transform=ax.transAxes,
        ha='left', va='top',
        fontsize=9, color='#8b0000')

ax.text(0.02, 0.05, "Aligned decline",
        transform=ax.transAxes,
        ha='left', va='bottom',
        fontsize=9, color='black')

# ===== 坐标轴标签 =====
ax.set_xlabel("Δ Sustained Attention (Focused Time Ratio)")
ax.set_ylabel("Δ Self-Reported Attention")

ax.set_title("Behavior vs Self-Perception")

# ===== 样式优化 =====
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

# 让图更居中好看
ax.set_xlim(min(delta_attention)-0.1, max(delta_attention)+0.1)
ax.set_ylim(min(delta_self)-0.2, max(delta_self)+0.2)

plt.tight_layout()
plt.savefig("behavior_vs_self_report.pdf", dpi=300)
plt.show()