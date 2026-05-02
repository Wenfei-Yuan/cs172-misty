import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import numpy as np

plt.rcParams.update({"font.size": 9})

participants = ['P1 (ADHD)', 'P2', 'P3', 'P5 (ADHD)']
colors = ['#F08080', '#F4A460', '#5BC8A8', '#B8A8D8']  # P1, P2, P3, P5
adhd_flags = [True, False, False, True]  # P1=ADHD, P2=non-ADHD, P3=non-ADHD, P5=ADHD

# ====== 原始数据 ======
lat_base  = np.array([23.3, 8.8,  6.2,    3.1])
lat_sys   = np.array([5.8,  20.5, 7.7,    3.2])

focus_base = np.array([0.352, 0.955, 0.7387, 0.7499])
focus_sys  = np.array([0.884, 0.756, 0.8058, 0.9444])

acc_base  = np.array([0.8, 1.0, 1.0, 1.0])
acc_sys   = np.array([1.0, 1.0, 0.6, 1.0])

dist_base = np.array([7,  2,  22, 26])
dist_sys  = np.array([10, 5,  18,  9])

# ====== Delta（全部"越高越好"）======
# Latency: lower = better → flip sign
delta_lat   = -(lat_sys  - lat_base)
# Attention: higher = better
delta_focus = focus_sys - focus_base
# Accuracy: higher = better
delta_acc   = acc_sys - acc_base
# Distraction: lower = better → flip sign
delta_dist  = -(dist_sys - dist_base)

x = np.arange(len(participants))
bar_width = 0.5

# ====== Layout: 2 rows × 4 cols ======
# Top row: paired plots; Bottom row: delta plots
fig, axes = plt.subplots(2, 4, figsize=(14, 7))

# ---------- helpers ----------
def paired_plot(ax, base, sys_vals, title, ylabel, ylim=None):
    jitter = np.linspace(-0.02, 0.02, len(base))
    for i in range(len(base)):
        marker = '^' if adhd_flags[i] else 'o'
        msize  = 7 if adhd_flags[i] else 5
        ax.plot([0 + jitter[i], 1 + jitter[i]],
                [base[i], sys_vals[i]],
                color=colors[i], alpha=0.7, linewidth=1.5,
                marker=marker, markersize=msize, label=participants[i])
    m_base, m_sys = np.nanmean(base), np.nanmean(sys_vals)
    delta = m_sys - m_base
    sign = '+' if delta >= 0 else ''
    ax.plot([0, 1], [m_base, m_sys],
            color='black', linewidth=1, linestyle='--',
            marker='o', markersize=3, alpha=0.6, label='Mean')
    # Annotate delta at the midpoint between the two mean markers
    ax.text(0.5, (m_base + m_sys) / 2, f'Δ={sign}{delta:.2f}',
            fontsize=7, color='gray', ha='center', va='bottom',
            transform=ax.transData)
    ax.set_title(title, fontsize=9)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(['Baseline', 'With System'])
    ax.set_ylabel(ylabel)
    if ylim:
        ax.set_ylim(ylim)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

def delta_plot(ax, deltas, title, ylabel):
    bars = ax.bar(x, deltas, width=bar_width, color=colors, alpha=0.85, zorder=3)
    ax.axhline(0, color='black', linewidth=0.8, linestyle='--')
    ax.set_title(title, fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(participants)
    ax.set_ylabel(ylabel)
    # shade positive region lightly
    ax.set_facecolor('#f9f9f9')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linewidth=0.5, alpha=0.5, zorder=0)

# ---------- Top row: paired ----------
paired_plot(axes[0, 0], lat_base,   lat_sys,   "Re-engagement Latency", "Time (s)")
# Build legend manually so shape encodes ADHD status
_handles = []
for i, p in enumerate(participants):
    mk = '^' if adhd_flags[i] else 'o'
    ms = 7 if adhd_flags[i] else 5
    _handles.append(mlines.Line2D([], [], color=colors[i], marker=mk, markersize=ms,
                                  linestyle='-', linewidth=1.5, label=p))
_handles.append(mlines.Line2D([], [], color='black', marker='o', markersize=3,
                               linestyle='--', linewidth=1, alpha=0.6, label='Mean'))
axes[0, 0].legend(handles=_handles, fontsize=7, loc='upper right')
paired_plot(axes[0, 1], focus_base, focus_sys, "Sustained Attention",   "Focused Time Ratio", ylim=(0, 1.1))
paired_plot(axes[0, 2], acc_base,   acc_sys,   "Comprehension Accuracy","Accuracy",           ylim=(0, 1.1))
paired_plot(axes[0, 3], dist_base,  dist_sys,  "Distraction Count",     "# Events")

# ---------- Bottom row: delta ----------
delta_plot(axes[1, 0], delta_lat,   "Δ Re-engagement Latency\n(higher = faster)",       "Δ seconds (↑ better)")
delta_plot(axes[1, 1], delta_focus, "Δ Sustained Attention\n(higher = better)",          "Δ ratio (↑ better)")
delta_plot(axes[1, 2], delta_acc,   "Δ Comprehension Accuracy\n(higher = better)",       "Δ accuracy (↑ better)")
delta_plot(axes[1, 3], delta_dist,  "Δ Distraction Count\n(higher = fewer distractions)","Δ count (↑ better)")

plt.tight_layout()
plt.savefig("final_clean_realdata.pdf", dpi=300, bbox_inches='tight')
plt.show()