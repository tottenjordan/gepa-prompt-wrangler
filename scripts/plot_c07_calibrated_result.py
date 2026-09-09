"""Redraw the campaign 07 calibrated-result figure.

Committed so the figure is reproducible rather than a one-off paste. Numbers are
inlined deliberately: they are the *published* values from
docs/analysis/2026-09-09-c07-first-calibrated-result.md, and the GCS artifacts they
came from were later overwritten (see that file's "run_id does not uniquely identify
a run"). Re-reading them from the bucket would silently redraw a different run.

Uses matplotlib against the repo's PaperBanana convention -- paperbanana.generate_plot
failed three times on 2026-09-09 with RetryError[...ClientError], including on a
minimal request, so the failure was the service. Prefer PaperBanana when it is back.

    uv run python scripts/plot_c07_calibrated_result.py
"""

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt

M = [
    ("safety_v1", 0.1536, 0.0417),
    ("final_response_quality_v1", -0.0309, 0.0108),
    ("instruction_following_v1", -0.0448, 0.0151),
    ("tool_use_quality_v1", -0.0545, 0.0164),
    ("hallucination_v1", -0.0778, 0.0077),
]
M = sorted(M, key=lambda r: r[1])
names = [m[0] for m in M]
d = [m[1] for m in M]
f = [m[2] for m in M]
SCALAR = 0.0417
AVG = -0.0109

fig, ax = plt.subplots(figsize=(11.2, 6.3), dpi=160)
y = range(len(M))
ax.axvspan(-SCALAR, SCALAR, color="0.88", zorder=0)
ax.text(
    0,
    -0.72,
    f"scalar floor  ±{SCALAR:.4f}",
    ha="center",
    va="center",
    fontsize=8.5,
    color="0.35",
    zorder=5,
)
ax.axvline(AVG, color="#1565c0", ls="-", lw=1.6, zorder=5)
ax.text(
    AVG - 0.004,
    len(M) - 0.42,
    f"average  {AVG:+.4f}",
    ha="right",
    va="center",
    fontsize=9,
    color="#1565c0",
    zorder=6,
)
ax.barh(y, d, color=["#2e7d32" if v > 0 else "#c62828" for v in d], height=0.55, zorder=3)
ax.errorbar(d, y, xerr=f, fmt="none", ecolor="0.15", elinewidth=1.4, capsize=5, zorder=4)
ax.axvline(0, color="0.25", ls="--", lw=1, zorder=2)

for i, (v, fl) in enumerate(zip(d, f, strict=True)):
    off = 0.006 if v > 0 else -0.006
    ax.text(
        v + fl * (1 if v > 0 else -1) + off,
        i,
        f"{abs(v) / fl:.1f}x floor",
        va="center",
        ha="left" if v > 0 else "right",
        fontsize=9,
        color="0.2",
    )

ax.set_yticks(list(y))
ax.set_yticklabels(names, fontsize=10)
ax.set_xlabel("score delta  (eval_after − eval_before)", fontsize=10.5)
ax.set_title(
    "Every metric clears its own noise floor — the average (−0.0109) clears none\n"
    "campaign 07 · c07-sonnet5 vs concurrent control c07-ctrl-sonnet5 · 64 cases · num_runs=3",
    fontsize=11.5,
    pad=13,
)
ax.set_xlim(-0.135, 0.245)
ax.set_ylim(-1.1, len(M) - 0.15)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.tick_params(axis="y", length=0)
ax.grid(axis="x", color="0.92", lw=0.7, zorder=1)
ax.set_axisbelow(True)
fig.text(
    0.5,
    0.015,
    "Error bars are that metric's own floor: the control arm's movement on a byte-identical prompt "
    "(larger of paired/unpaired).",
    ha="center",
    fontsize=8.5,
    color="0.4",
)
fig.tight_layout(rect=(0, 0.035, 1, 1))
out = "docs/analysis/2026-09-09-c07-first-calibrated-result.png"
fig.savefig(out)
print("wrote", out)
