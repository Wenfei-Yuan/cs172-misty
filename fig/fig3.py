import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# -----------------------------
# 数据 (Post-Session Likert, With-System Only, 1–5)
# Intrusiveness  = avg(prompts interrupted flow, prompts felt distracting)
# Support        = avg(phases organized reading, system supported focus)
# Body-Doubling  = avg(robot made me feel accountable, robot encouraged staying on task)
# -----------------------------
data = {
    "participant":    ["P01", "P02", "P03", "P04", "P05"],
    "Intrusiveness":  [3.0,   4.5,   3.5,   4.0,   3.0],
    "Support":        [2.5,   4.0,   4.0,   4.5,   4.0],
    "Body-Doubling":  [4.0,   2.5,   3.5,   2.0,   4.5],
}

df = pd.DataFrame(data)

dims = ["Intrusiveness", "Support", "Body-Doubling"]

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
ax.set_xticklabels(["Intrusiveness", "Perceived Support", "Body-Doubling"])

ax.set_ylabel("Rating (1–5)")
ax.set_ylim(1, 5)

# -----------------------------
# 去掉多余元素（关键）
# -----------------------------
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

ax.grid(axis='y', linestyle='--', alpha=0.3)

ax.set_title("Post-Session Likert Scales (With-System Only)")

plt.tight_layout()
plt.savefig("subjective_chi_style.pdf", dpi=300)
plt.show()