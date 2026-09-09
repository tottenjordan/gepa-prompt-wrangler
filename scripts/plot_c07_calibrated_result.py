"""Redraw the campaign 07 calibrated-result figure.

Authored by PaperBanana (`paperbanana plot`, gemini-3.5-flash VLM +
gemini-3.1-flash-image, 2 refinement iterations, 2026-09-09) and committed verbatim
apart from the output path, so the published figure is reproducible without a second
non-deterministic generation. Regenerate from scratch with the command recorded in
docs/analysis/2026-09-09-c07-first-calibrated-result.md and the data file beside it.

Numbers are inlined because that is what PaperBanana emitted, and because the GCS
artifacts they came from were overwritten by a later run sharing the same run_id
prefix (silent-failures #14) -- re-reading the bucket would draw a different run.

Note the error-bar encoding: each is that metric's floor centred on **zero**, i.e. the
interval a delta must escape to be a result, not uncertainty on the estimate.

    uv run python scripts/plot_c07_calibrated_result.py
"""

import matplotlib.pyplot as plt

# Define output path
OUTPUT_PATH = "docs/analysis/2026-09-09-c07-first-calibrated-result.png"

# Set up publication-quality style
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Helvetica", "Arial", "DejaVu Sans"]
plt.rcParams["text.usetex"] = False

# Create figure with optimal dimensions for horizontal layout
fig, ax = plt.subplots(figsize=(10, 6), facecolor="#ffffff")
ax.set_facecolor("#ffffff")

# Set limits to comfortably accommodate all elements without clipping
ax.set_xlim(-0.12, 0.18)
ax.set_ylim(-0.6, 4.8)

# Fine dotted vertical grid lines rendered behind the data (zorder=0)
ax.set_xticks([-0.10, -0.05, 0, 0.05, 0.10, 0.15])
ax.grid(True, axis="x", color="#e0e0e0", linestyle=":", linewidth=0.75, zorder=0)

# Shaded Scalar Floor Band (zorder=1)
ax.axvspan(-0.0417, 0.0417, color="#eceff1", alpha=0.6, zorder=1)

# Zero Line (zorder=2)
ax.axvline(0, color="#424242", linestyle="--", linewidth=1.25, zorder=2)

# Average Delta Line (zorder=3)
ax.axvline(-0.0109, color="#0d47a1", linestyle="-", linewidth=2.0, zorder=3)

# Data Coordinates
metrics = [
    "hallucination_v1",
    "tool_use_quality_v1",
    "instruction_following_v1",
    "final_response_quality_v1",
    "safety_v1",
]
y_coords = [0.0, 1.0, 2.0, 3.0, 4.0]
deltas = [-0.0778, -0.0545, -0.0448, -0.0309, 0.1536]
noise_floors = [0.0077, 0.0164, 0.0151, 0.0108, 0.0417]

# Draw Bars (zorder=4)
# safety_v1 (positive delta, solid forest green)
ax.barh(
    4.0, 0.1536, height=0.5, left=0, color="#1b5e20", edgecolor="#1a1a1a", linewidth=0.75, zorder=4
)

# Other metrics (negative deltas, crimson red with diagonal hatching)
for y, d in zip(y_coords[:-1], deltas[:-1], strict=True):
    ax.barh(
        y,
        d,
        height=0.5,
        left=0,
        color="#b71c1c",
        hatch="//",
        edgecolor="#1a1a1a",
        linewidth=0.75,
        zorder=4,
    )

# Draw Symmetric Noise Floor Error Bars (zorder=5)
ax.errorbar(
    x=[0] * 5,
    y=y_coords,
    xerr=noise_floors,
    fmt="none",
    ecolor="#000000",
    elinewidth=1.5,
    capsize=6,
    capthick=1.5,
    zorder=5,
)

# Text Annotations for Delta Over Floor (zorder=6)
# safety_v1
ax.text(
    0.158,
    4.0,
    "3.7x floor",
    color="#1b5e20",
    fontsize=10,
    fontweight="bold",
    va="center",
    ha="left",
    zorder=6,
)
# final_response_quality_v1
ax.text(
    -0.035,
    3.0,
    "2.9x floor",
    color="#b71c1c",
    fontsize=10,
    fontweight="bold",
    va="center",
    ha="right",
    zorder=6,
)
# instruction_following_v1
ax.text(
    -0.049,
    2.0,
    "3.0x floor",
    color="#b71c1c",
    fontsize=10,
    fontweight="bold",
    va="center",
    ha="right",
    zorder=6,
)
# tool_use_quality_v1
ax.text(
    -0.059,
    1.0,
    "3.3x floor",
    color="#b71c1c",
    fontsize=10,
    fontweight="bold",
    va="center",
    ha="right",
    zorder=6,
)
# hallucination_v1
ax.text(
    -0.082,
    0.0,
    "10.1x floor",
    color="#b71c1c",
    fontsize=10,
    fontweight="bold",
    va="center",
    ha="right",
    zorder=6,
)

# Reference Line Labels (zorder=6)
ax.text(
    0.005,
    4.6,
    "scalar floor",
    color="#546e7a",
    fontsize=9,
    style="italic",
    va="center",
    ha="left",
    zorder=6,
)
ax.text(
    -0.013,
    4.6,
    "average",
    color="#0d47a1",
    fontsize=9,
    fontweight="bold",
    va="center",
    ha="right",
    zorder=6,
)

# Spines (Open modern layout)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_visible(False)
ax.spines["bottom"].set_color("#212121")
ax.spines["bottom"].set_linewidth(1.0)

# X-Axis Styling
ax.set_xlabel(
    "score delta (after minus before)", fontsize=12, fontweight="bold", color="#212121", labelpad=10
)
ax.tick_params(axis="x", colors="#212121", labelsize=10, direction="in")

# Y-Axis Styling
ax.set_yticks(y_coords)
ax.set_yticklabels(metrics, fontsize=11, color="#212121")
ax.tick_params(axis="y", left=False)  # Hide y-ticks but keep labels

# Title (Top-left aligned, flush with the left edge of the Y-axis labels)
ax.set_title(
    "Every metric clears its own noise floor; the average clears none.",
    fontsize=14,
    fontweight="bold",
    color="#212121",
    pad=20,
    loc="left",
)

# Save the publication-quality figure
plt.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
