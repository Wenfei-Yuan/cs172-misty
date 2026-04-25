import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# -----------------------------
# 数据
# -----------------------------
data = {
    "participant": ["P1","P2","P3","P4","P5","P6"],
    "interrupt":   [2,   5,   None,None,None,None],
    "distracting": [4,   4,   None,None,None,None],
    "organize":    [2,   4,   None,None,None,None],
    "focus":       [3,   4,   None,None,None,None],
    "accountable": [4,   3,   None,None,None,None],
    "on_task":     [4,   2,   None,None,None,None]
}

df = pd.DataFrame(data)

# -----------------------------
# 反向编码
# -----------------------------
df["interrupt_r"] = 6 - df["interrupt"]
df["distracting_r"] = 6 - df["distracting"]

# -----------------------------
# 三个维度
# -----------------------------
df["Disruption"] = df[["interrupt_r","distracting_r"]].mean(axis=1)
df["Support"] = df[["organize","focus"]].mean(axis=1)
df["Social"] = df[["accountable","on_task"]].mean(axis=1)

dims = ["Disruption","Support","Social"]

# -----------------------------
# 统计
# -----------------------------
means = df[dims].mean()
stds = df[dims].std()

# -----------------------------
# CHI风格设置
# -----------------------------
plt.rcParams.update({
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9
})

fig, ax = plt.subplots(figsize=(4.5, 3.5))  # 更紧凑

x = np.arange(len(dims))

# -----------------------------
# Bar（淡）
# -----------------------------
ax.bar(
    x, means,
    yerr=stds,
    capsize=4,
    color='#D3D3D3',
    edgecolor='none'
)

# -----------------------------
# Individual points（重点）
# -----------------------------
np.random.seed(0)  # 固定 jitter（可复现）

for i, dim in enumerate(dims):
    y = df[dim].values
    jitter = np.random.uniform(-0.06, 0.06, size=len(y))
    
    ax.scatter(
        np.full(len(y), x[i]) + jitter,
        y,
        color='#333333',
        s=18,
        zorder=3
    )

# -----------------------------
# 均值标注
# -----------------------------
for i, v in enumerate(means):
    ax.text(i, v + 0.08, f"{v:.2f}",
            ha='center', fontsize=8)

# -----------------------------
# 坐标轴
# -----------------------------
ax.set_xticks(x)
ax.set_xticklabels(["Disruption","Support","Social"])

ax.set_ylabel("Rating (1 = Disagree → 5 = Agree)")
ax.set_ylim(1, 5)

# -----------------------------
# 去掉多余元素（关键）
# -----------------------------
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

ax.grid(axis='y', linestyle='--', alpha=0.3)

ax.set_title("Subjective Experience")

plt.tight_layout()
plt.savefig("subjective_chi_style.pdf", dpi=300)
plt.show()