import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    "font.size": 9,
})

participants = ['P1','P2','P3','P4','P5','P6']

# ====== 真实数据（其余用 np.nan） ======
lat_base = [23.3, 8.8, np.nan, np.nan, np.nan, np.nan]
lat_sys  = [5.8, 20.5, np.nan, np.nan, np.nan, np.nan]

focus_base = [0.352, 0.955, np.nan, np.nan, np.nan, np.nan]
focus_sys  = [0.884, 0.756, np.nan, np.nan, np.nan, np.nan]

acc_base = [0.8, 1.0, np.nan, np.nan, np.nan, np.nan]
acc_sys  = [1.0, 1.0, np.nan, np.nan, np.nan, np.nan]

fig, axes = plt.subplots(1, 3, figsize=(10,3))

def paired_plot(ax, base, sys, title, ylabel, ylim=None):
    jitter = np.linspace(-0.02, 0.02, len(base))  # 固定 jitter（论文更稳定）

    # individual lines（只画真实数据）
    for i in range(len(base)):
        if not np.isnan(base[i]) and not np.isnan(sys[i]):
            ax.plot([0+jitter[i],1+jitter[i]],
                    [base[i], sys[i]],
                    color='gray', alpha=0.4, linewidth=1)

    # mean（只算真实数据）
    mean_base = np.nanmean(base)
    mean_sys = np.nanmean(sys)

    ax.plot([0,1],
            [mean_base, mean_sys],
            color='black', linewidth=3, marker='o')

    ax.set_title(title)
    ax.set_xticks([0,1])
    ax.set_xticklabels(['Baseline','With System'])
    ax.set_ylabel(ylabel)

    if ylim:
        ax.set_ylim(ylim)

    # cleaner style
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

# ====== 三个子图 ======
paired_plot(
    axes[0],
    lat_base, lat_sys,
    "Re-engagement Latency",
    "Time (seconds)"
)

paired_plot(
    axes[1],
    focus_base, focus_sys,
    "Sustained Attention",
    "Focused Time Ratio",
    ylim=(0,1)
)

paired_plot(
    axes[2],
    acc_base, acc_sys,
    "Comprehension Accuracy",
    "Accuracy",
    ylim=(0,1)
)

plt.tight_layout()
plt.savefig("final_clean_realdata.pdf", dpi=300)
plt.show()