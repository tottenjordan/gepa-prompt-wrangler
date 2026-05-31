"""Generate optimization analysis — charts + per-agent markdown reports.

Usage:
    uv run python scripts/generate_analysis.py
    uv run python scripts/generate_analysis.py --input outputs/demo_baseline_*.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from wrangler.config import MODEL_COSTS, REPORTS_DIR, OUTPUTS_DIR
from wrangler.analysis import (
    generate_agent_report, generate_comparison_report, normalize_agent_keys,
    compute_tier_scores, METRIC_LABELS, AGENT_ORDER, TIER_ORDER, PROVIDERS,
)

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


def _get_agents(results: dict) -> list[str]:
    return [a for a in AGENT_ORDER if a in results]


def _get_case_metadata(results: dict) -> list[dict] | None:
    meta = results.get("_eval_metadata")
    if meta and "cases" in meta:
        return meta["cases"]
    return None


def generate_comparison_chart(results: dict):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
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
    has_after = any(results[a].get("after") for a in results if not a.startswith("_"))
    fig, ax = plt.subplots(figsize=(12, 7))

    for agent_name, data in results.items():
        if agent_name.startswith("_"):
            continue
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
    agents = [a for a in _get_agents(results) if results[a].get("after")]
    if not agents:
        print("  Skipping improvement chart (no after scores)")
        return

    metrics = list(METRIC_LABELS.keys())
    n = len(agents)
    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    has_std = any(results[a].get("after_std") for a in agents)
    for i, metric in enumerate(metrics):
        deltas = [results[a].get("after", {}).get(metric, 0) - results[a].get("before", {}).get(metric, 0) for a in agents]
        yerr = [results[a].get("after_std", {}).get(metric, 0) for a in agents] if has_std else None
        ax.bar(x + i * width, deltas, width, label=METRIC_LABELS[metric], color=colors[i],
               yerr=yerr, capsize=2 if yerr else 0)

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


def generate_tier_breakdown_chart(results: dict, case_metadata: list[dict] | None):
    """Grouped bar chart: tier × agent, colored by provider."""
    if not case_metadata:
        print("  Skipping tier breakdown chart (no case metadata)")
        return

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
    phase = "after" if any(results[a].get("after") for a in agents) else "before"
    per_case_key = f"{phase}_per_case"

    has_per_case = any(results[a].get(per_case_key) for a in agents)
    if not has_per_case:
        print("  Skipping tier breakdown chart (no per-case scores)")
        return

    tiers_present = [t for t in TIER_ORDER if any(m.get("tier") == t for m in case_metadata)]
    if not tiers_present:
        return

    tier_avgs: dict[str, dict[str, float]] = {}
    for name in agents:
        per_case = results[name].get(per_case_key, [])
        if per_case:
            tier_scores = compute_tier_scores(per_case, case_metadata, "tier")
            tier_avgs[name] = {}
            for tier in tiers_present:
                scores = tier_scores.get(tier, {})
                tier_avgs[name][tier] = sum(scores.values()) / max(len(scores), 1) if scores else 0
        else:
            tier_avgs[name] = {t: 0 for t in tiers_present}

    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(tiers_present))
    n_agents = len(agents)
    width = 0.15
    offset = -(n_agents - 1) / 2 * width

    for i, name in enumerate(agents):
        model = results[name].get("model", "")
        is_gemini = "gemini" in model
        color = "#2563EB" if is_gemini else "#EA580C"
        alpha = 0.5 + 0.1 * i
        values = [tier_avgs[name].get(t, 0) for t in tiers_present]
        ax.bar(x + offset + i * width, values, width, label=name.title(),
               color=color, alpha=min(alpha, 1.0), edgecolor="black", linewidth=0.5)

    ax.set_xlabel("Complexity Tier")
    ax.set_ylabel("Average Score")
    ax.set_title(f"Per-Tier Performance — {phase.title()} Optimization")
    ax.set_xticks(x)
    ax.set_xticklabels([t.title() for t in tiers_present])
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.1)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "tier_breakdown.png", dpi=150)
    plt.close()
    print(f"  Generated: tier_breakdown.png")


def generate_category_heatmap(results: dict, case_metadata: list[dict] | None):
    """Heatmap: category × agent, cell value = average score."""
    if not case_metadata:
        print("  Skipping category heatmap (no case metadata)")
        return

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
    phase = "after" if any(results[a].get("after") for a in agents) else "before"
    per_case_key = f"{phase}_per_case"

    has_per_case = any(results[a].get(per_case_key) for a in agents)
    if not has_per_case:
        print("  Skipping category heatmap (no per-case scores)")
        return

    categories = sorted(set(m.get("category", "") for m in case_metadata if m.get("category")))
    if not categories:
        return

    matrix = np.zeros((len(categories), len(agents)))
    for j, name in enumerate(agents):
        per_case = results[name].get(per_case_key, [])
        if per_case:
            cat_scores = compute_tier_scores(per_case, case_metadata, "category")
            for i, cat in enumerate(categories):
                scores = cat_scores.get(cat, {})
                matrix[i, j] = sum(scores.values()) / max(len(scores), 1) if scores else 0

    fig, ax = plt.subplots(figsize=(10, max(6, len(categories) * 0.8)))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(np.arange(len(agents)))
    ax.set_yticks(np.arange(len(categories)))
    ax.set_xticklabels([a.title() for a in agents])
    ax.set_yticklabels(categories)

    for i in range(len(categories)):
        for j in range(len(agents)):
            val = matrix[i, j]
            color = "white" if val < 0.4 or val > 0.8 else "black"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", color=color, fontsize=9)

    ax.set_title(f"Category Capability — {phase.title()} Optimization")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Avg Score")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "category_heatmap.png", dpi=150)
    plt.close()
    print(f"  Generated: category_heatmap.png")


def generate_run_comparison_chart(results: dict, previous_path: str = "outputs/results_all_agents.json"):
    """Side-by-side bars: previous run vs current run per agent."""
    prev_file = Path(previous_path)
    if not prev_file.exists():
        print(f"  Skipping run comparison chart (no previous results at {previous_path})")
        return

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(prev_file) as f:
        prev = normalize_agent_keys(json.load(f))

    agents = [a for a in _get_agents(results) if a in prev]
    if not agents:
        return

    phase = "after" if any(results[a].get("after") for a in agents) else "before"

    prev_avgs = []
    curr_avgs = []
    for name in agents:
        ps = prev[name].get(phase, prev[name].get("before", {}))
        cs = results[name].get(phase, results[name].get("before", {}))
        prev_avgs.append(sum(ps.values()) / max(len(ps), 1) if ps else 0)
        curr_avgs.append(sum(cs.values()) / max(len(cs), 1) if cs else 0)

    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(agents))
    width = 0.35

    ax.bar(x - width/2, prev_avgs, width, label="Previous Run", color="#93C5FD", edgecolor="black", linewidth=0.5)
    ax.bar(x + width/2, curr_avgs, width, label="Current Run", color="#2563EB", edgecolor="black", linewidth=0.5)

    ax.set_xlabel("Agent")
    ax.set_ylabel("Average Score")
    ax.set_title(f"Run Comparison — {phase.title()} Optimization")
    ax.set_xticks(x)
    ax.set_xticklabels([a.title() for a in agents])
    ax.legend(fontsize=9)
    ax.set_ylim(0, 1.1)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "run_comparison.png", dpi=150)
    plt.close()
    print(f"  Generated: run_comparison.png")


def main(input_path: str = None):
    print("Loading results...")
    results = normalize_agent_keys(load_results(input_path))
    case_metadata = _get_case_metadata(results)
    agents = _get_agents(results)
    print(f"  Loaded {len(agents)} agents")
    if case_metadata:
        print(f"  Eval metadata: {len(case_metadata)} cases")
    print()

    print("Generating charts...")
    generate_comparison_chart(results)
    generate_cost_quality_chart(results)
    generate_improvement_chart(results)
    generate_tier_breakdown_chart(results, case_metadata)
    generate_category_heatmap(results, case_metadata)
    generate_run_comparison_chart(results)

    print("\nGenerating per-agent reports...")
    for agent_name, data in results.items():
        if agent_name.startswith("_"):
            continue
        path = generate_agent_report(
            agent_name=agent_name,
            model=data.get("model", "unknown"),
            engine_id=data.get("engine_id", ""),
            original_prompt=data.get("original_prompt", ""),
            optimized_prompt=data.get("optimized_prompt"),
            before_scores=data.get("before", {}),
            after_scores=data.get("after"),
            before_per_case=data.get("before_per_case"),
            after_per_case=data.get("after_per_case"),
            case_metadata=case_metadata,
            before_std=data.get("before_std"),
            after_std=data.get("after_std"),
        )
        print(f"  {agent_name}: {path}")

    print("\nGenerating comparison report...")
    report_path = generate_comparison_report(results, case_metadata=case_metadata)
    print(f"  {report_path}")

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    args = parser.parse_args()
    main(args.input)
