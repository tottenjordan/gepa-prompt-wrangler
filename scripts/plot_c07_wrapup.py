"""Redraw the campaign 07 wrap-up figure.

Authored by PaperBanana (`paperbanana plot`, gemini-3.5-flash VLM, 2026-09-10) and
committed verbatim apart from the output path and the legend placement, so the published
figure is reproducible without another non-deterministic generation.

Two edits to what PaperBanana emitted:
  - output path
  - legend moved off 'upper right', where it sat on top of the safety_v1 bars

Taken from refinement iteration 1: iteration 2's script was truncated mid-statement and
rendered a blank PNG while the tool still reported "Plot saved to". Always open the image
and parse the emitted script before trusting a PaperBanana run.

Numbers are inlined from docs/analysis/2026-09-10-campaign-07-wrapup.md.

    uv run python scripts/plot_c07_wrapup.py
"""

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

# Set the output path
OUTPUT_PATH = "docs/analysis/2026-09-10-campaign-07-wrapup.png"

# Set global hatch linewidth for subtle, clean hatching
plt.rcParams["hatch.linewidth"] = 0.5

# Create figure and axis with specified dimensions
fig, ax = plt.subplots(figsize=(10, 6), facecolor="#ffffff")
ax.set_facecolor("#ffffff")

# Data structure representing the metrics and their attributes
metrics = [
    {
        "name": "instruction_following_v1*",
        "y": 0,
        "sonnet": -0.0615,
        "pro": -0.1171,
        "noise": 0.0151,
        "replicates": True,
    },
    {
        "name": "final_response_quality_v1",
        "y": 1,
        "sonnet": -0.0059,
        "pro": 0.0016,
        "noise": 0.0108,
        "replicates": False,
    },
    {
        "name": "hallucination_v1",
        "y": 2,
        "sonnet": 0.0172,
        "pro": -0.0109,
        "noise": 0.0077,
        "replicates": False,
    },
    {
        "name": "tool_use_quality_v1",
        "y": 3,
        "sonnet": 0.0205,
        "pro": -0.0131,
        "noise": 0.0164,
        "replicates": False,
    },
    {
        "name": "safety_v1",
        "y": 4,
        "sonnet": 0.1568,
        "pro": 0.0904,
        "noise": 0.0417,
        "replicates": True,
    },
]

# Layer 1: Shaded Noise Floor Bands (drawn behind everything)
for m in metrics:
    rect = mpatches.Rectangle(
        (-m["noise"], m["y"] - 0.45),
        2 * m["noise"],
        0.9,
        facecolor="#f1f5f9",
        alpha=0.6,
        edgecolor="none",
        zorder=1,
    )
    ax.add_patch(rect)

# Layer 2: Vertical Gridlines
grid_xs = [-0.15, -0.10, -0.05, 0.05, 0.10, 0.15, 0.20]
for x in grid_xs:
    ax.axvline(x=x, color="#cbd5e1", linestyle=":", linewidth=0.75, zorder=2)

# Layer 3: Zero Line
ax.axvline(x=0.0, color="#1e293b", linestyle=(0, (4, 4)), linewidth=1.5, zorder=3)

# Layer 4: Grouped Horizontal Bars
for m in metrics:
    y = m["y"]
    replicates = m["replicates"]

    # --- Sonnet 5 Bar (Top bar of the pair) ---
    sonnet_y = y + 0.05
    sonnet_w = m["sonnet"]
    if replicates:
        # Saturated with colored hatch
        ax.barh(
            sonnet_y,
            sonnet_w,
            height=0.35,
            align="edge",
            facecolor="#1d4ed8",
            edgecolor="#1e3a8a",
            hatch="//",
            linewidth=0.5,
            zorder=4,
        )
        # Crisp black border overlay
        ax.barh(
            sonnet_y,
            sonnet_w,
            height=0.35,
            align="edge",
            facecolor="none",
            edgecolor="#09090b",
            linewidth=1.5,
            zorder=4,
        )
    else:
        # Faded solid fill, borderless
        ax.barh(
            sonnet_y,
            sonnet_w,
            height=0.35,
            align="edge",
            facecolor="#bfdbfe",
            edgecolor="none",
            zorder=4,
        )

    # --- Pro Bar (Bottom bar of the pair) ---
    pro_y = y - 0.40
    pro_w = m["pro"]
    if replicates:
        # Saturated with colored hatch
        ax.barh(
            pro_y,
            pro_w,
            height=0.35,
            align="edge",
            facecolor="#ea580c",
            edgecolor="#7c2d12",
            hatch="//",
            linewidth=0.5,
            zorder=4,
        )
        # Crisp black border overlay
        ax.barh(
            pro_y,
            pro_w,
            height=0.35,
            align="edge",
            facecolor="none",
            edgecolor="#09090b",
            linewidth=1.5,
            zorder=4,
        )
    else:
        # Faded solid fill, borderless
        ax.barh(
            pro_y, pro_w, height=0.35, align="edge", facecolor="#ffedd5", edgecolor="none", zorder=4
        )

# Aesthetic & Presentation Parameters
ax.set_xlim(-0.15, 0.20)
ax.set_ylim(-0.6, 4.6)

# Spines Layout (Modern "Open" layout)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_color("#4b5563")
ax.spines["left"].set_linewidth(1.0)
ax.spines["bottom"].set_color("#4b5563")
ax.spines["bottom"].set_linewidth(1.0)

# Ticks & Labels
ax.set_xticks([-0.15, -0.10, -0.05, 0.00, 0.05, 0.10, 0.15, 0.20])
ax.set_xticklabels(
    ["-0.15", "-0.10", "-0.05", "0.00", "0.05", "0.10", "0.15", "0.20"],
    fontsize=10,
    color="#374151",
    fontfamily="sans-serif",
)
ax.tick_params(axis="both", colors="#374151")

y_labels = [m["name"] for m in metrics]
ax.set_yticks([m["y"] for m in metrics])
ax.set_yticklabels(y_labels, fontsize=10.5, color="#1e293b", fontfamily="monospace")

# Title & Axis Labels
ax.set_title(
    "Two models agree on exactly two metrics; the regression is the holdout",
    loc="left",
    pad=20,
    fontweight="bold",
    fontsize=14,
    color="#0f172a",
    fontfamily="sans-serif",
)
ax.set_xlabel(
    "score delta (after minus before)",
    fontsize=11,
    color="#334155",
    labelpad=10,
    fontfamily="sans-serif",
)

# Footnote
fig.text(
    0.05,
    0.02,
    "*not in GEPA criteria (holdout)",
    fontsize=9,
    fontstyle="italic",
    color="#64748b",
    ha="left",
    fontfamily="sans-serif",
)

# Custom Legend
handle_sonnet = mpatches.Patch(
    facecolor="#1d4ed8", edgecolor="#09090b", hatch="//", linewidth=1.5, label="Sonnet 5"
)
handle_pro = mpatches.Patch(
    facecolor="#ea580c", edgecolor="#09090b", hatch="//", linewidth=1.5, label="Pro"
)
handle_repl = mpatches.Patch(
    facecolor="#94a3b8",
    edgecolor="#09090b",
    hatch="//",
    linewidth=1.5,
    label="Replicates (Solid Border)",
)
handle_norepl = mpatches.Patch(
    facecolor="#e2e8f0", edgecolor="none", label="No Replication (Faded)"
)

ax.legend(
    handles=[handle_sonnet, handle_pro, handle_repl, handle_norepl],
    loc="lower right",
    frameon=True,
    framealpha=0.92,
    fontsize=9.5,
    labelcolor="#334155",
)

# Adjust margins to prevent clipping of long metric names
fig.subplots_adjust(left=0.24, right=0.94, top=0.85, bottom=0.18)

# Save the publication-quality figure
plt.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
