import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# 数据
data = [
    ["P1", "without", 8, 3, 7, 7, 7, 4],
    ["P1", "with",    9, 8, 8,10, 7, 4],
    ["P2", "without", 6, 1, 4, 9, 4, 2],
    ["P2", "with",    8, 1, 6, 8, 6, 3],
]

columns = ["participant","condition","mental","physical","temporal",
           "performance","effort","frustration"]
df = pd.DataFrame(data, columns=columns)

dims   = ["mental","physical","temporal","performance","effort","frustration"]
labels = ["Mental","Physical","Temporal","Performance","Effort","Frustration"]

plt.rcParams.update({"font.size": 9})

fig, ax = plt.subplots(figsize=(6.5,3.5))

x = np.arange(len(dims))
offset = 0.15

# -----------------------------
# scatter + paired lines
# -----------------------------
for p in df["participant"].unique():
    sub = df[df["participant"] == p]

    base = sub[sub["condition"]=="without"][dims].values[0]
    sys  = sub[sub["condition"]=="with"][dims].values[0]

    jitter = np.random.uniform(-0.03, 0.03)

    for i in range(len(dims)):
        # paired line（淡）
        ax.plot([x[i]-offset+jitter, x[i]+offset+jitter],
                [base[i], sys[i]],
                color='#bbbbbb', alpha=0.4, linewidth=0.8)

        # scatter点
        ax.scatter(x[i]-offset+jitter, base[i],
                   color='white', edgecolor='black', s=25, zorder=3)

        ax.scatter(x[i]+offset+jitter, sys[i],
                   color='black', s=25, zorder=3)

# -----------------------------
# 均值（可选）
# -----------------------------
means_base = df[df["condition"]=="without"][dims].mean()
means_sys  = df[df["condition"]=="with"][dims].mean()

ax.scatter(x-offset, means_base, color='black', s=40, marker='D', label='Without (mean)')
ax.scatter(x+offset, means_sys,  color='black', s=40, marker='o', label='With (mean)')

# -----------------------------
# 轴
# -----------------------------
ax.set_xticks(x)
ax.set_xticklabels(labels, rotation=25)
ax.set_ylabel("NASA-TLX Score (0–10)")
ax.set_ylim(0,10)

ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

ax.legend(frameon=False, loc='upper left', bbox_to_anchor=(1.02,1))

ax.set_title("NASA-TLX Workload (Paired Scatter)")

plt.tight_layout()
plt.show()