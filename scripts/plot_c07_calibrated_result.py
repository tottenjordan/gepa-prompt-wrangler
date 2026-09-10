"""Redraw the campaign 07 calibrated-result figure.

Authored by PaperBanana (`paperbanana plot`, gemini-3.5-flash VLM, 2 refinement
iterations, 2026-09-09) and committed verbatim apart from the output path, so the
published figure is reproducible without a second non-deterministic generation.

Encoding: both intervals are centred on **zero** and a bar must escape both to count.
The thin whisker is the concurrent control arm's floor (evaluation noise); the orange
band is |run A - run B| on eval_after (optimizer noise, from two runs of the identical
manifest). Only safety_v1 and instruction_following_v1 clear both.

Numbers are inlined because the GCS artifacts behind them were overwritten in place by
a later run sharing the same run_id prefix (silent-failures #14). Both runs are archived
under pipeline-runs/archive/run-8a5905dee0-2026-09-09/.

    uv run python scripts/plot_c07_calibrated_result.py
"""

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

# Define output path
OUTPUT_PATH = "docs/analysis/2026-09-09-c07-first-calibrated-result.png"

# Set global font styles to ensure a clean, modern sans-serif look
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Helvetica", "Arial", "DejaVu Sans", "sans-serif"]
plt.rcParams["text.usetex"] = False

# Data
metrics = [
    "instruction_following_v1*",
    "final_response_quality_v1",
    "hallucination_v1",
    "tool_use_quality_v1",
    "safety_v1",
]

deltas = [-0.0615, -0.0059, 0.0172, 0.0205, 0.1568]
noise_floors = [0.0151, 0.0108, 0.0077, 0.0164, 0.0417]
run_to_runs = [0.0167, 0.0250, 0.0950, 0.0750, 0.0032]
y_pos = np.arange(len(metrics))

# Initialize figure
fig, ax = plt.subplots(figsize=(10, 6), facecolor="#ffffff")
ax.set_facecolor("#ffffff")

# Grid and reference lines
ax.axvline(0.0, color="#757575", linestyle="--", linewidth=1.5, zorder=1)

# X-axis configuration
xticks = np.arange(-0.12, 0.181, 0.02)
ax.set_xticks(xticks)
ax.set_xticklabels([f"{x:.2f}" for x in xticks], fontsize=9, color="#212121")
ax.grid(axis="x", color="#e0e0e0", linewidth=0.8, linestyle="--", zorder=0)

# Plot data elements
for i, y in enumerate(y_pos):
    delta = deltas[i]
    noise = noise_floors[i]
    r2r = run_to_runs[i]

    # Determine bar color based on positive/negative delta
    bar_color = "#2e7d32" if delta >= 0 else "#d32f2f"

    # 1. Run-to-Run Spread Band (zorder=2)
    # Rendered as a solid horizontal bar of height 0.3 centered at x=0
    ax.barh(
        y,
        width=2 * r2r,
        left=-r2r,
        height=0.3,
        color="#ff9800",
        alpha=0.4,
        edgecolor="#e65100",
        linewidth=0.5,
        zorder=2,
    )

    # 2. Main Bar (zorder=3)
    # Height of 0.5 leaves a clean 0.5 gap between adjacent bars
    ax.barh(
        y,
        width=delta,
        left=0,
        height=0.5,
        color=bar_color,
        edgecolor="#1a1a1a",
        linewidth=1.0,
        zorder=3,
    )

    # 3. Noise Floor Whisker (zorder=4)
    # Horizontal line centered at x=0
    ax.plot([-noise, noise], [y, y], color="#1a1a1a", linewidth=1.5, zorder=4)
    # Flat vertical caps (height of 0.2 units: from y-0.1 to y+0.1)
    ax.plot([-noise, -noise], [y - 0.1, y + 0.1], color="#1a1a1a", linewidth=1.5, zorder=4)
    ax.plot([noise, noise], [y - 0.1, y + 0.1], color="#1a1a1a", linewidth=1.5, zorder=4)

# Spines & Borders (Open layout)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_color("#212121")
ax.spines["left"].set_linewidth(1.0)
ax.spines["bottom"].set_color("#212121")
ax.spines["bottom"].set_linewidth(1.0)

# Ticks styling
ax.tick_params(axis="x", direction="in", colors="#212121", length=4)
ax.tick_params(axis="y", left=False)  # Hide y-axis tick marks, keep labels

# Y-axis limits and labels
ax.set_yticks(y_pos)
ax.set_yticklabels(metrics, fontsize=10, color="#212121")
ax.set_ylim(-0.5, 4.8)
ax.set_xlim(-0.12, 0.18)

# Labels & Title
ax.set_xlabel("score delta (after minus before)", fontsize=11, color="#424242", labelpad=8)

# Left-aligned title for an editorial, modern aesthetic
plt.title(
    "Two results survive both rulers; the regression is the holdout metric.",
    fontsize=14,
    fontweight="bold",
    color="#212121",
    pad=20,
    loc="left",
)

# Legend
# Custom handles to accurately represent the visual elements
noise_handle = Line2D(
    [], [], color="#1a1a1a", linewidth=1.5, marker="|", markersize=10, markeredgewidth=1.5
)
r2r_handle = mpatches.Patch(facecolor="#ff9800", alpha=0.4, edgecolor="#e65100", linewidth=0.5)

ax.legend(
    [noise_handle, r2r_handle],
    ["Noise Floor", "Run-to-Run Spread"],
    loc="upper right",
    frameon=True,
    facecolor="#ffffff",
    edgecolor="#d0d0d0",
    prop={"size": 9.5},
)

# Adjust layout to ensure footnote space
plt.subplots_adjust(bottom=0.15)

# Align footnote dynamically with the left spine of the plot
bbox = ax.get_position()
fig.text(
    bbox.x0,
    0.03,
    "*not in GEPA criteria (holdout)",
    fontsize=9,
    color="#616161",
    style="italic",
    ha="left",
)

# Save the high-resolution figure
plt.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
