"""Generate optimization analysis — charts + per-agent markdown reports.

Usage:
    uv run python scripts/generate_analysis.py
    uv run python scripts/generate_analysis.py --input outputs/demo_baseline_*.json
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from wrangler.config import MODEL_COSTS, REPORTS_DIR, OUTPUTS_DIR
from wrangler.analysis import generate_agent_report, METRIC_LABELS

CHARTS_DIR = Path(REPORTS_DIR) / "charts"
AGENTS_DIR = Path(REPORTS_DIR) / "agents"


def load_results(input_path: str = None) -> dict:
    if input_path:
        with open(input_path) as f:
            return json.load(f)
    files = sorted(Path(OUTPUTS_DIR).glob("demo_*.json")) + sorted(Path(OUTPUTS_DIR).glob("results_*.json"))
    if not files:
        raise FileNotFoundError("No results files found in outputs/")
    with open(files[-1]) as f:
        return json.load(f)


def generate_comparison_chart(results: dict):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = list(results.keys())
    metrics = list(METRIC_LABELS.keys())
    n = len(agents)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        values = [results[a].get("before", {}).get(metric, 0) for a in agents]
        ax.bar(x + i * width, values, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent")
    ax.set_ylabel("Score")
    ax.set_title("Baseline Eval Scores — All Agents")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels([a.title() for a in agents])
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.axhline(y=0.6, color="red", linestyle="--", alpha=0.3)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "comparison.png", dpi=150)
    plt.close()
    print(f"  Generated: comparison.png")


def generate_cost_quality_chart(results: dict):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    has_after = any(results[a].get("after") for a in results)
    fig, ax = plt.subplots(figsize=(12, 7))

    for agent_name, data in results.items():
        model = data.get("model", "unknown")
        cost_info = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        cost = cost_info["input"] + cost_info["output"]
        is_gemini = "gemini" in model

        before_scores = data.get("before", {})
        avg_before = np.mean(list(before_scores.values())) if before_scores else 0
        before_color = "#93C5FD" if is_gemini else "#FDBA74"
        ax.scatter(cost, avg_before, s=160, c=before_color, zorder=4,
                   edgecolors="black", linewidth=0.5, marker="o")
        label_offset = (-45, -15) if has_after else (10, 5)
        ax.annotate(agent_name.title(), (cost, avg_before),
                    textcoords="offset points", xytext=label_offset, fontsize=8, color="gray")

        if has_after and data.get("after"):
            after_scores = data["after"]
            avg_after = np.mean(list(after_scores.values())) if after_scores else 0
            after_color = "#2563EB" if is_gemini else "#EA580C"
            ax.scatter(cost, avg_after, s=220, c=after_color, zorder=5,
                       edgecolors="black", linewidth=0.5, marker="D")
            ax.annotate(agent_name.title(), (cost, avg_after),
                        textcoords="offset points", xytext=(10, 5), fontsize=9, fontweight="bold")
            ax.annotate("", xy=(cost, avg_after), xytext=(cost, avg_before),
                        arrowprops=dict(arrowstyle="->", color="gray", lw=1.2, ls="--"))

    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_items = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#93C5FD",
               markeredgecolor="black", markersize=10, label="Gemini (Before)"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#2563EB",
               markeredgecolor="black", markersize=10, label="Gemini (After)"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#FDBA74",
               markeredgecolor="black", markersize=10, label="Claude (Before)"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#EA580C",
               markeredgecolor="black", markersize=10, label="Claude (After)"),
        Line2D([0], [0], color="gray", ls="--", lw=1.2, label="GEPA improvement"),
    ]
    ax.legend(handles=legend_items, loc="lower left", fontsize=8)
    ax.set_xlabel("Combined Cost — Input + Output ($/M tokens)")
    ax.set_ylabel("Average Quality Score")
    ax.set_title("Cost-Quality Tradeoff — Before & After GEPA Optimization")
    ax.set_xscale("log")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "cost_quality.png", dpi=150)
    plt.close()
    print(f"  Generated: cost_quality.png")


def generate_improvement_chart(results: dict):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = [a for a in results if results[a].get("after")]
    if not agents:
        print("  Skipping improvement chart (no after scores)")
        return

    metrics = list(METRIC_LABELS.keys())
    n = len(agents)
    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        deltas = [results[a].get("after", {}).get(metric, 0) - results[a].get("before", {}).get(metric, 0) for a in agents]
        ax.bar(x + i * width, deltas, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent")
    ax.set_ylabel("Score Change")
    ax.set_title("GEPA Optimization Impact (After - Before)")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels([a.title() for a in agents])
    ax.legend(fontsize=8)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "improvement_delta.png", dpi=150)
    plt.close()
    print(f"  Generated: improvement_delta.png")


def main(input_path: str = None):
    print("Loading results...")
    results = load_results(input_path)
    print(f"  Loaded {len(results)} agents\n")

    print("Generating charts...")
    generate_comparison_chart(results)
    generate_cost_quality_chart(results)
    generate_improvement_chart(results)

    print("\nGenerating per-agent reports...")
    for agent_name, data in results.items():
        path = generate_agent_report(
            agent_name=agent_name,
            model=data.get("model", "unknown"),
            engine_id=data.get("engine_id", ""),
            original_prompt=data.get("original_prompt", ""),
            optimized_prompt=data.get("optimized_prompt"),
            before_scores=data.get("before", {}),
            after_scores=data.get("after"),
        )
        print(f"  {agent_name}: {path}")

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    args = parser.parse_args()
    main(args.input)
