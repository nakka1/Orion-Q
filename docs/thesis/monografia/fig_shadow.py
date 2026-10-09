import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#8a8a86",
                     "axes.linewidth": 0.6, "xtick.color": "#4a4a47", "ytick.color": "#4a4a47"})
BETTER, WORSE, INK, MUTED = "#2a78d6", "#e34948", "#2b2b29", "#6b6b67"

# Diferenca relativa (%) = (erro_modelo - erro_persistencia) / erro_persistencia * 100
# Fonte: docs/results/orion_q_sync_campaign_20260923.md (MAE em ps, media por semente)
sync = [("Suave", 19.850, 22.724, True), ("Ruído", 144.456, 138.084, True),
        ("Reversão", 15.963, 15.879, True), ("Degrau", 20.438, 18.433, True),
        ("Oscilação", 135.540, 122.218, True), ("Perda", 20.339, 22.824, False)]
# Fonte: docs/results/orion_q_compensation_campaign_20260923.md (mrad, checkpoint calibrado.ridge)
so3 = [("Suave", 4.342, 5.237, True), ("Ruído", 7.373, 6.841, True),
       ("Reversão", 4.325, 5.235, True), ("Degrau", 5.551, 6.281, True),
       ("Perda", 4.305, 5.314, False), ("Tardio", 4.342, 5.210, False)]

fig, axes = plt.subplots(1, 2, figsize=(6.3, 2.7), sharey=True)
for ax, data, title in [(axes[0], sync, "(a) Sincronizador QKD (Ridge)"),
                        (axes[1], so3, "(b) Compensador SO(3) (Ridge calibrado)")]:
    labels = [d[0] for d in data]
    rel = [(m - p) / p * 100 for _, m, p, _ in data]
    for i, (r, d) in enumerate(zip(rel, data)):
        color = BETTER if r < 0 else WORSE
        ax.bar(i, r, width=0.62, color=color, edgecolor="white", linewidth=1.0,
               hatch=None if d[3] else "////", zorder=3)
        ax.text(i, r + (1.0 if r >= 0 else -1.0), f"{r:+.1f}%".replace(".", ","),
                ha="center", va="bottom" if r >= 0 else "top", fontsize=7, color=INK)
    ax.axhline(0, color=MUTED, linewidth=0.8, zorder=4)
    ax.set_xticks(range(len(labels)), labels, fontsize=7.5, rotation=30, ha="right", rotation_mode="anchor")
    ax.set_title(title, fontsize=9, color=INK, loc="left")
    ax.grid(axis="y", color="#e6e6e3", linewidth=0.5, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(-25, 16)
axes[0].set_ylabel("Erro relativo à persistência (%)", color=INK)
from matplotlib.patches import Patch
handles = [Patch(facecolor=BETTER, label="menor erro que a persistência"),
           Patch(facecolor=WORSE, label="maior erro que a persistência"),
           Patch(facecolor="white", edgecolor=MUTED, hatch="////", label="cobertura < 90% (reprova no filtro)")]
fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=7, bbox_to_anchor=(0.5, -0.01), handlelength=1.5, columnspacing=1.2)
fig.tight_layout(rect=(0, 0.08, 1, 1))
fig.savefig("/home/claude/tcc/figuras/shadow_campanhas.pdf")
fig.savefig("/home/claude/tcc/figuras/shadow_campanhas.png", dpi=200)
print([round((m-p)/p*100,2) for _,m,p,_ in sync]); print([round((m-p)/p*100,2) for _,m,p,_ in so3])
