"""Per-agent and cross-model analysis report generators + chart generation."""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .config import MODEL_COSTS, REPORTS_DIR, blended_cost

METRIC_LABELS = {
    "final_response_quality_v1": "Response Quality",
    "hallucination_v1": "Hallucination",
    "safety_v1": "Safety",
    "tool_use_quality_v1": "Tool Use",
    "instruction_following_v1": "Instruction Following",
    "final_response_match_v2": "Response Match",
}

PROVIDERS = {
    "gemini-3.1-flash-lite": "Google",
    "gemini-3.5-flash": "Google",
    "gemini-3.1-pro-preview": "Google",
    "claude-sonnet-4-6": "Anthropic",
    "claude-opus-4-6": "Anthropic",
}

AGENT_ORDER = ["lite", "flash", "pro", "sonnet", "opus"]

MODEL_MAP = {
    "lite": "gemini-3.1-flash-lite",
    "flash": "gemini-3.5-flash",
    "pro": "gemini-3.1-pro-preview",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}


def normalize_agent_keys(results: dict) -> dict:
    """Normalize agent keys to short names (lite, flash, pro, sonnet, opus).

    Matches by model field against MODEL_MAP, falling back to key prefix matching.
    """
    model_to_short = {v: k for k, v in MODEL_MAP.items()}
    normalized = {}
    for key, value in results.items():
        if key.startswith("_"):
            normalized[key] = value
            continue
        if key in AGENT_ORDER:
            normalized[key] = value
            continue
        model = value.get("model", "") if isinstance(value, dict) else ""
        short = model_to_short.get(model)
        if short:
            normalized[short] = value
        else:
            normalized[key] = value
    return normalized

TIER_ORDER = ["low", "medium", "high"]

CHARTS_DIR = Path(REPORTS_DIR) / "charts"


# ---------------------------------------------------------------------------
# Chart generation functions
# ---------------------------------------------------------------------------

def _get_agents(results: dict) -> list[str]:
    return [a for a in AGENT_ORDER if a in results]


def generate_comparison_chart(results: dict, charts_dir: Path | None = None):
    """Grouped bar chart comparing all pairs across metrics."""
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
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
    plt.savefig(charts_dir / "comparison.png", dpi=150)
    plt.close()
    print(f"  Generated: comparison.png")


def generate_cost_quality_chart(results: dict, charts_dir: Path | None = None):
    """Cost vs quality scatter with before/after arrows and Pareto frontier."""
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
    has_after = any(results[a].get("after") for a in results if not a.startswith("_"))
    fig, ax = plt.subplots(figsize=(12, 7))

    pareto_points = []

    for agent_name, data in results.items():
        if agent_name.startswith("_"):
            continue
        model = data.get("model", "unknown")
        cost = blended_cost(model)
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
            pareto_points.append((cost, avg_after, agent_name))
        else:
            pareto_points.append((cost, avg_before, agent_name))

    if pareto_points:
        pareto_points.sort(key=lambda p: p[0])
        frontier = []
        max_quality = -1
        for cost_val, quality, name in pareto_points:
            if quality >= max_quality:
                frontier.append((cost_val, quality))
                max_quality = quality
        if frontier:
            fx, fy = zip(*frontier)
            if len(frontier) >= 2:
                ax.plot(fx, fy, color="#10B981", ls="-", lw=2.5, alpha=0.7, zorder=3)
            ax.scatter(fx, fy, s=80, c="#10B981", zorder=6, marker="s", edgecolors="black", linewidth=0.5)

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
        Line2D([0], [0], color="#10B981", ls="-", lw=2.5, marker="s", markersize=6,
               markerfacecolor="#10B981", markeredgecolor="black", label="Pareto frontier"),
    ]
    ax.legend(handles=legend_items, loc="lower left", fontsize=8)
    ax.set_xlabel("Blended Cost ($/M tokens, 4:1 in:out)")
    ax.set_ylabel("Average Quality Score")
    ax.set_title("Cost-Quality Tradeoff — Before & After GEPA Optimization")
    ax.set_xscale("log")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(charts_dir / "cost_quality.png", dpi=150)
    plt.close()
    print(f"  Generated: cost_quality.png")


def generate_improvement_chart(results: dict, charts_dir: Path | None = None):
    """Delta bar chart with optional std dev error bars."""
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
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
    plt.savefig(charts_dir / "improvement_delta.png", dpi=150)
    plt.close()
    print(f"  Generated: improvement_delta.png")


def generate_tier_breakdown_chart(results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None):
    """Grouped bar chart: tier x agent, colored by provider."""
    if not case_metadata:
        print("  Skipping tier breakdown chart (no case metadata)")
        return
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
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
    plt.savefig(charts_dir / "tier_breakdown.png", dpi=150)
    plt.close()
    print(f"  Generated: tier_breakdown.png")


def generate_category_heatmap(results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None):
    """Heatmap: category x agent, cell value = average score."""
    if not case_metadata:
        print("  Skipping category heatmap (no case metadata)")
        return
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
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
    plt.savefig(charts_dir / "category_heatmap.png", dpi=150)
    plt.close()
    print(f"  Generated: category_heatmap.png")


def generate_run_comparison_chart(results: dict, previous_path: str = "outputs/results_all_agents.json", charts_dir: Path | None = None):
    """Side-by-side bars: previous run vs current run per agent."""
    prev_file = Path(previous_path)
    if not prev_file.exists():
        print(f"  Skipping run comparison chart (no previous results at {previous_path})")
        return
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
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
    plt.savefig(charts_dir / "run_comparison.png", dpi=150)
    plt.close()
    print(f"  Generated: run_comparison.png")


def generate_radar_chart(results: dict, charts_dir: Path | None = None):
    """Radar/spider chart overlaying all 6 metrics for each model."""
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
    phase = "after" if any(results[a].get("after") for a in agents) else "before"

    metrics = list(METRIC_LABELS.keys())
    labels = [METRIC_LABELS[m] for m in metrics]
    n_metrics = len(metrics)
    angles = np.linspace(0, 2 * np.pi, n_metrics, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(polar=True))
    gemini_cmap = plt.cm.Blues
    claude_cmap = plt.cm.Oranges

    gemini_agents = [a for a in agents if "gemini" in results[a].get("model", "")]
    claude_agents = [a for a in agents if a not in gemini_agents]

    for idx, name in enumerate(agents):
        scores = results[name].get(phase, results[name].get("before", {}))
        values = [scores.get(m, 0) for m in metrics]
        values += values[:1]

        model = results[name].get("model", "")
        if "gemini" in model:
            gi = gemini_agents.index(name)
            color = gemini_cmap(0.4 + 0.2 * gi)
        else:
            ci = claude_agents.index(name)
            color = claude_cmap(0.4 + 0.2 * ci)

        ax.plot(angles, values, "o-", linewidth=2, label=name.title(), color=color)
        ax.fill(angles, values, alpha=0.08, color=color)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_title(f"Metric Profiles — {phase.title()} Optimization", pad=20, fontsize=13)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)
    plt.tight_layout()
    plt.savefig(charts_dir / "radar.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Generated: radar.png")


def generate_tier_improvement_heatmap(results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None):
    """Heatmap: rows=tiers, cols=models, cells=avg improvement delta. Red-white-green."""
    if not case_metadata:
        print("  Skipping tier improvement heatmap (no case metadata)")
        return
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
    has_after = any(results[a].get("after") for a in agents)
    if not has_after:
        print("  Skipping tier improvement heatmap (no after scores)")
        return

    has_per_case = any(results[a].get("before_per_case") and results[a].get("after_per_case") for a in agents)
    if not has_per_case:
        print("  Skipping tier improvement heatmap (no per-case scores)")
        return

    tiers_present = [t for t in TIER_ORDER if any(m.get("tier") == t for m in case_metadata)]
    if not tiers_present:
        return

    matrix = np.zeros((len(tiers_present), len(agents)))
    for j, name in enumerate(agents):
        before_pc = results[name].get("before_per_case", [])
        after_pc = results[name].get("after_per_case", [])
        if before_pc and after_pc:
            before_tiers = compute_tier_scores(before_pc, case_metadata, "tier")
            after_tiers = compute_tier_scores(after_pc, case_metadata, "tier")
            for i, tier in enumerate(tiers_present):
                b_scores = before_tiers.get(tier, {})
                a_scores = after_tiers.get(tier, {})
                b_avg = sum(b_scores.values()) / max(len(b_scores), 1) if b_scores else 0
                a_avg = sum(a_scores.values()) / max(len(a_scores), 1) if a_scores else 0
                matrix[i, j] = a_avg - b_avg

    vmax = max(abs(matrix.min()), abs(matrix.max()), 0.05)
    fig, ax = plt.subplots(figsize=(10, 5))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)

    ax.set_xticks(np.arange(len(agents)))
    ax.set_yticks(np.arange(len(tiers_present)))
    ax.set_xticklabels([a.title() for a in agents])
    ax.set_yticklabels([t.title() for t in tiers_present])

    for i in range(len(tiers_present)):
        for j in range(len(agents)):
            val = matrix[i, j]
            color = "white" if abs(val) > vmax * 0.6 else "black"
            ax.text(j, i, f"{val:+.3f}", ha="center", va="center", color=color, fontsize=10, fontweight="bold")

    ax.set_title("GEPA Optimization Impact by Tier (After - Before)")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Avg Score Delta")
    plt.tight_layout()
    plt.savefig(charts_dir / "tier_improvement_heatmap.png", dpi=150)
    plt.close()
    print(f"  Generated: tier_improvement_heatmap.png")


def generate_all_charts(
    results: dict,
    case_metadata: list[dict] | None = None,
    charts_dir: Path | None = None,
    use_paperbanana: bool = True,
):
    """Generate all available charts for an experiment."""
    from .charts import (
        generate_comparison_chart_pb,
        generate_cost_quality_chart_pb,
        generate_improvement_chart_pb,
        generate_radar_chart_pb,
    )
    generate_comparison_chart_pb(results, charts_dir, use_paperbanana=use_paperbanana)
    generate_cost_quality_chart_pb(results, charts_dir, use_paperbanana=use_paperbanana)
    generate_improvement_chart_pb(results, charts_dir, use_paperbanana=use_paperbanana)
    generate_radar_chart_pb(results, charts_dir, use_paperbanana=use_paperbanana)
    generate_tier_breakdown_chart(results, case_metadata, charts_dir)
    generate_category_heatmap(results, case_metadata, charts_dir)
    generate_tier_improvement_heatmap(results, case_metadata, charts_dir)
    generate_run_comparison_chart(results, charts_dir=charts_dir)


def compute_tier_scores(
    per_case_scores: list[dict[str, float]],
    case_metadata: list[dict],
    group_key: str = "tier",
) -> dict[str, dict[str, float]]:
    """Compute average metric scores grouped by tier or category.

    Returns: {"low": {"final_response_quality_v1": 0.85, ...}, ...}
    """
    buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
    for i, scores in enumerate(per_case_scores):
        if i < len(case_metadata) and scores:
            group = case_metadata[i].get(group_key, "unknown")
            buckets[group].append(scores)

    result: dict[str, dict[str, float]] = {}
    for group, score_list in buckets.items():
        if not score_list:
            continue
        all_metrics = set()
        for s in score_list:
            all_metrics.update(s.keys())
        avg: dict[str, float] = {}
        for metric in all_metrics:
            values = [s[metric] for s in score_list if metric in s]
            avg[metric] = sum(values) / len(values) if values else 0.0
        result[group] = avg
    return result


def _eval_dataset_section(case_metadata: list[dict] | None) -> list[str]:
    """Generate the eval dataset info section with dynamic counts."""
    lines = []
    lines.append("## Eval Dataset\n")
    if case_metadata:
        tier_counts: dict[str, int] = defaultdict(int)
        cat_counts: dict[str, int] = defaultdict(int)
        for m in case_metadata:
            tier_counts[m.get("tier", "unknown")] += 1
            cat_counts[m.get("category", "unknown")] += 1
        lines.append(f"- **Total cases:** {len(case_metadata)}")
        for tier in TIER_ORDER:
            if tier in tier_counts:
                lines.append(f"- **{tier.title()} complexity:** {tier_counts[tier]} cases")
        lines.append(f"- **Categories:** {', '.join(sorted(cat_counts.keys()))}")
    else:
        lines.append("- See eval_cases.yaml for case details")
    lines.append("")
    return lines


def _tier_breakdown_section(
    before_per_case: list[dict] | None,
    after_per_case: list[dict] | None,
    case_metadata: list[dict] | None,
) -> list[str]:
    """Generate per-tier breakdown tables."""
    if not before_per_case or not case_metadata:
        return []

    lines = []
    lines.append("## Per-Tier Breakdown\n")

    tier_before = compute_tier_scores(before_per_case, case_metadata, "tier")

    if after_per_case:
        tier_after = compute_tier_scores(after_per_case, case_metadata, "tier")

        lines.append("### After Optimization — By Tier\n")
        header = "| Metric |" + " | ".join(t.title() for t in TIER_ORDER if t in tier_after) + " |"
        sep = "|--------|" + " | ".join("------" for t in TIER_ORDER if t in tier_after) + " |"
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for tier in TIER_ORDER:
                if tier in tier_after:
                    row += f" {tier_after[tier].get(key, 0):.2f} |"
            lines.append(row)
        lines.append("")

        lines.append("### Improvement Delta — By Tier\n")
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for tier in TIER_ORDER:
                if tier in tier_after:
                    b = tier_before.get(tier, {}).get(key, 0)
                    a = tier_after[tier].get(key, 0)
                    row += f" {a - b:+.2f} |"
            lines.append(row)
        lines.append("")
    else:
        lines.append("### Baseline — By Tier\n")
        tiers_present = [t for t in TIER_ORDER if t in tier_before]
        header = "| Metric |" + " | ".join(t.title() for t in tiers_present) + " |"
        sep = "|--------|" + " | ".join("------" for _ in tiers_present) + " |"
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for tier in tiers_present:
                row += f" {tier_before[tier].get(key, 0):.2f} |"
            lines.append(row)
        lines.append("")

    return lines


def _category_section(
    per_case_scores: list[dict],
    case_metadata: list[dict] | None,
    phase_label: str = "Post-Optimization",
) -> list[str]:
    """Generate per-category capability table."""
    if not per_case_scores or not case_metadata:
        return []

    cat_scores = compute_tier_scores(per_case_scores, case_metadata, "category")
    if not cat_scores:
        return []

    lines = []
    lines.append(f"## Per-Category Capability ({phase_label})\n")

    sorted_cats = sorted(cat_scores.keys())
    lines.append("| Category | Avg Score | Cases |")
    lines.append("|----------|-----------|-------|")

    cat_counts: dict[str, int] = defaultdict(int)
    for m in case_metadata:
        cat_counts[m.get("category", "unknown")] += 1

    for cat in sorted_cats:
        scores = cat_scores[cat]
        avg = sum(scores.values()) / max(len(scores), 1)
        lines.append(f"| {cat} | {avg:.2f} | {cat_counts.get(cat, 0)} |")
    lines.append("")

    return lines


def _prompt_evolution_summary(original: str, optimized: str) -> list[str]:
    """Generate a human-readable summary of what GEPA changed."""
    lines = []
    lines.append("## Prompt Evolution Summary\n")
    lines.append(f"GEPA expanded the prompt from **{len(original)} chars** to **{len(optimized)} chars** "
                 f"({len(optimized)/max(len(original),1):.0f}x expansion).\n")

    keywords = {
        "tool": "Tool-specific guidance",
        "policy": "Domain policy knowledge",
        "concis": "Conciseness directives",
        "format": "Response formatting rules",
        "scope": "Scope limitations",
        "error": "Error handling guidance",
        "safety": "Safety constraints",
        "limit": "Policy limit references",
        "manager review": "Escalation procedures",
        "example": "Response examples/templates",
    }
    orig_lower = original.lower()
    opt_lower = optimized.lower()

    added = []
    for kw, label in keywords.items():
        if kw in opt_lower and kw not in orig_lower:
            added.append(label)

    if added:
        lines.append("**Key additions by GEPA:**\n")
        for a in added:
            lines.append(f"- {a}")
        lines.append("")

    return lines


def _cost_benefit_section(model: str, before_scores: dict, after_scores: dict) -> list[str]:
    """Generate cost-benefit analysis section."""
    lines = []
    cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    blend = blended_cost(model)

    lines.append("## Cost-Benefit Analysis\n")

    avg_before = sum(before_scores.values()) / max(len(before_scores), 1)
    avg_after = sum(after_scores.values()) / max(len(after_scores), 1)
    improvement = avg_after - avg_before

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Input cost | ${cost['input']:.2f}/M tokens |")
    lines.append(f"| Output cost | ${cost['output']:.2f}/M tokens |")
    lines.append(f"| Blended cost (4:1 in:out) | ${blend:.2f}/M tokens |")
    lines.append(f"| Avg quality (before) | {avg_before:.2f} |")
    lines.append(f"| Avg quality (after) | {avg_after:.2f} |")
    lines.append(f"| Quality gain | {improvement:+.2f} ({improvement/max(avg_before,0.01)*100:+.1f}%) |")

    quality_per_dollar = avg_after / max(blend, 0.01)
    lines.append(f"| Quality per $/M tokens | {quality_per_dollar:.3f} |")
    lines.append("")

    if improvement > 0:
        lines.append(f"GEPA optimization improved average quality by **{improvement/max(avg_before,0.01)*100:+.1f}%** "
                     f"at **${cost['input']:.2f}** input / **${cost['output']:.2f}** output per M tokens. "
                     f"The quality gain comes at zero additional inference cost — only the system prompt changed.\n")
    else:
        lines.append(f"GEPA optimization resulted in a **{improvement:+.2f}** change in average quality. "
                     f"Consider re-running with a different evalset or more iterations.\n")

    return lines


def generate_agent_report(
    agent_name: str,
    model: str,
    engine_id: str,
    original_prompt: str,
    optimized_prompt: str | None,
    before_scores: dict[str, float],
    after_scores: dict[str, float] | None,
    output_dir: str | None = None,
    before_per_case: list[dict] | None = None,
    after_per_case: list[dict] | None = None,
    case_metadata: list[dict] | None = None,
    before_std: dict[str, float] | None = None,
    after_std: dict[str, float] | None = None,
) -> str:
    """Generate a per-agent analysis markdown file. Returns the file path."""
    output_dir = Path(output_dir or REPORTS_DIR) / "agents"
    output_dir.mkdir(parents=True, exist_ok=True)

    cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    provider = PROVIDERS.get(model, "Unknown")

    lines = []
    lines.append(f"# {agent_name.replace('_', ' ').title()} — GEPA Optimization Analysis\n")

    lines.append("## Architecture\n")
    lines.append("![Agent Architecture](../diagrams/agent_architecture.png)\n")

    lines.append("## Agent Configuration\n")
    lines.append(f"- **Model:** `{model}`")
    lines.append(f"- **Provider:** {provider}")
    lines.append(f"- **Input cost:** ${cost['input']}/M tokens")
    lines.append(f"- **Output cost:** ${cost['output']}/M tokens")
    lines.append(f"- **Engine ID:** `{engine_id}`\n")

    lines.extend(_eval_dataset_section(case_metadata))

    lines.append("## Metrics\n")
    lines.append("| Metric | Description |")
    lines.append("|--------|-------------|")
    for key, label in METRIC_LABELS.items():
        lines.append(f"| {label} | {key} |")
    lines.append("")

    lines.append("## Original Prompt (Generic)\n")
    lines.append(f"```\n{original_prompt}\n```\n")

    if optimized_prompt:
        lines.append("## Optimized Prompt (GEPA)\n")
        lines.append(f"```\n{optimized_prompt.strip()}\n```\n")
        lines.extend(_prompt_evolution_summary(original_prompt, optimized_prompt))

    lines.append("## Eval Results\n")
    lines.append("### Before Optimization\n")
    lines.append("| Metric | Score |")
    lines.append("|--------|-------|")
    for key in METRIC_LABELS:
        score = before_scores.get(key, 0)
        lines.append(f"| {METRIC_LABELS[key]} | {score:.2f} |")
    lines.append("")

    if after_scores:
        lines.append("### After Optimization\n")
        if after_std:
            lines.append("| Metric | Before | After | Std Dev | Delta | Change |")
            lines.append("|--------|--------|-------|---------|-------|--------|")
        else:
            lines.append("| Metric | Before | After | Delta | Change |")
            lines.append("|--------|--------|-------|-------|--------|")
        for key in METRIC_LABELS:
            b = before_scores.get(key, 0)
            a = after_scores.get(key, 0)
            delta = a - b
            pct = f"{delta/b*100:+.0f}%" if b > 0 else "N/A"
            if after_std:
                sd = after_std.get(key, 0)
                lines.append(f"| {METRIC_LABELS[key]} | {b:.2f} | {a:.2f} | {sd:.3f} | {delta:+.2f} | {pct} |")
            else:
                lines.append(f"| {METRIC_LABELS[key]} | {b:.2f} | {a:.2f} | {delta:+.2f} | {pct} |")
        lines.append("")

        lines.extend(_cost_benefit_section(model, before_scores, after_scores))

        avg_before = sum(before_scores.values()) / max(len(before_scores), 1)
        avg_after = sum(after_scores.values()) / max(len(after_scores), 1)
        lines.append("## Key Observations\n")
        lines.append(f"- Average score changed from **{avg_before:.2f}** to **{avg_after:.2f}** ({(avg_after-avg_before)/avg_before*100:+.1f}%)")

        improved = [k for k in METRIC_LABELS if after_scores.get(k, 0) > before_scores.get(k, 0)]
        regressed = [k for k in METRIC_LABELS if after_scores.get(k, 0) < before_scores.get(k, 0)]
        if improved:
            lines.append(f"- **Improved:** {', '.join(METRIC_LABELS[k] for k in improved)}")
        if regressed:
            lines.append(f"- **Regressed:** {', '.join(METRIC_LABELS[k] for k in regressed)}")
        lines.append("")

    # Per-tier breakdown
    lines.extend(_tier_breakdown_section(before_per_case, after_per_case, case_metadata))

    # Per-category capability
    best_per_case = after_per_case or before_per_case
    phase_label = "Post-Optimization" if after_per_case else "Baseline"
    lines.extend(_category_section(best_per_case or [], case_metadata, phase_label))

    report_path = output_dir / f"{agent_name}_analysis.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    return str(report_path)


def _comparison_tier_section(
    all_results: dict[str, dict],
    ordered: list[str],
    case_metadata: list[dict] | None,
) -> list[str]:
    """Generate cross-model per-tier comparison section."""
    if not case_metadata:
        return []

    lines = []
    lines.append("## Per-Tier Score Comparison\n")

    phase = "after" if any(all_results[n].get("after") for n in ordered) else "before"
    per_case_key = f"{phase}_per_case"

    has_per_case = any(all_results[n].get(per_case_key) for n in ordered)
    if not has_per_case:
        return []

    for tier in TIER_ORDER:
        tier_case_count = sum(1 for m in case_metadata if m.get("tier") == tier)
        if tier_case_count == 0:
            continue

        lines.append(f"### {tier.title()} Complexity ({tier_case_count} cases)\n")
        header = "| Metric |" + " | ".join(n.title() for n in ordered) + " |"
        sep = "|--------|" + " | ".join("------" for _ in ordered) + " |"
        lines.append(header)
        lines.append(sep)

        agent_tier_scores: dict[str, dict[str, float]] = {}
        for name in ordered:
            per_case = all_results[name].get(per_case_key, [])
            if per_case:
                tier_scores = compute_tier_scores(per_case, case_metadata, "tier")
                agent_tier_scores[name] = tier_scores.get(tier, {})
            else:
                agent_tier_scores[name] = {}

        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for name in ordered:
                s = agent_tier_scores[name].get(key, 0)
                row += f" {s:.2f} |"
            lines.append(row)
        lines.append("")

    return lines


def _category_heatmap_section(
    all_results: dict[str, dict],
    ordered: list[str],
    case_metadata: list[dict] | None,
) -> list[str]:
    """Generate cross-model category capability matrix."""
    if not case_metadata:
        return []

    phase = "after" if any(all_results[n].get("after") for n in ordered) else "before"
    per_case_key = f"{phase}_per_case"

    has_per_case = any(all_results[n].get(per_case_key) for n in ordered)
    if not has_per_case:
        return []

    all_cats = sorted(set(m.get("category", "") for m in case_metadata if m.get("category")))
    if not all_cats:
        return []

    cat_counts: dict[str, int] = defaultdict(int)
    for m in case_metadata:
        cat_counts[m.get("category", "unknown")] += 1

    agent_cat_scores: dict[str, dict[str, dict[str, float]]] = {}
    for name in ordered:
        per_case = all_results[name].get(per_case_key, [])
        if per_case:
            agent_cat_scores[name] = compute_tier_scores(per_case, case_metadata, "category")
        else:
            agent_cat_scores[name] = {}

    lines = []
    lines.append("## Category Capability Matrix\n")
    lines.append(f"Average score across all metrics per category ({phase} optimization).\n")

    header = "| Category | Cases |" + " | ".join(n.title() for n in ordered) + " |"
    sep = "|----------|-------|" + " | ".join("------" for _ in ordered) + " |"
    lines.append(header)
    lines.append(sep)

    for cat in all_cats:
        row = f"| {cat} | {cat_counts.get(cat, 0)} |"
        for name in ordered:
            scores = agent_cat_scores[name].get(cat, {})
            avg = sum(scores.values()) / max(len(scores), 1) if scores else 0
            row += f" {avg:.2f} |"
        lines.append(row)
    lines.append("")

    return lines


def _previous_run_comparison_section(
    current_results: dict[str, dict],
    ordered: list[str],
    previous_path: str = "outputs/results_all_agents.json",
) -> list[str]:
    """Compare current results against a previous run."""
    prev_path = Path(previous_path)
    if not prev_path.exists():
        return []

    try:
        with open(prev_path) as f:
            prev = normalize_agent_keys(json.load(f))
    except (json.JSONDecodeError, OSError):
        return []

    common = [n for n in ordered if n in prev]
    if not common:
        return []

    lines = []
    lines.append("## Comparison with Previous Run\n")
    lines.append(f"Comparing against previous results from `{previous_path}`.\n")

    phase = "after" if any(current_results[n].get("after") for n in common) else "before"

    lines.append(f"### Average Score Comparison ({phase.title()})\n")
    lines.append("| Agent | Previous | Current | Delta |")
    lines.append("|-------|----------|---------|-------|")
    for name in common:
        prev_scores = prev[name].get(phase, prev[name].get("before", {}))
        curr_scores = current_results[name].get(phase, current_results[name].get("before", {}))
        prev_avg = sum(prev_scores.values()) / max(len(prev_scores), 1) if prev_scores else 0
        curr_avg = sum(curr_scores.values()) / max(len(curr_scores), 1) if curr_scores else 0
        delta = curr_avg - prev_avg
        lines.append(f"| {name.title()} | {prev_avg:.2f} | {curr_avg:.2f} | {delta:+.2f} |")
    lines.append("")

    return lines


def _interpretation_section(
    all_results: dict[str, dict],
    ordered: list[str],
    ranked: list[str],
) -> list[str]:
    """Generate data-driven interpretation of optimization results."""
    lines = []
    has_after = any(all_results[n].get("after") for n in ordered)

    if not has_after:
        return lines

    # Compute per-agent and per-metric summaries
    agent_deltas: dict[str, float] = {}
    metric_deltas: dict[str, list[float]] = {k: [] for k in METRIC_LABELS}
    for name in ordered:
        before = all_results[name].get("before", {})
        after = all_results[name].get("after", {})
        avg_b = sum(before.values()) / max(len(before), 1)
        avg_a = sum(after.values()) / max(len(after), 1)
        agent_deltas[name] = avg_a - avg_b
        for k in METRIC_LABELS:
            metric_deltas[k].append(after.get(k, 0) - before.get(k, 0))

    improved = [n for n in ordered if agent_deltas[n] > 0.005]
    regressed = [n for n in ordered if agent_deltas[n] < -0.005]
    stable = [n for n in ordered if abs(agent_deltas[n]) <= 0.005]

    metrics_up = {k: sum(v)/len(v) for k, v in metric_deltas.items() if sum(v)/len(v) > 0.01}
    metrics_down = {k: sum(v)/len(v) for k, v in metric_deltas.items() if sum(v)/len(v) < -0.01}

    best_value_name = ranked[0]
    best_quality_name = max(ordered, key=lambda n: (
        sum(all_results[n].get("after", {}).values()) / max(len(all_results[n].get("after", {})), 1)
    ) if all_results[n].get("after") else 0)
    best_quality = sum(all_results[best_quality_name].get("after", {}).values()) / max(
        len(all_results[best_quality_name].get("after", {})), 1)

    # --- Interpretation ---
    lines.append("## Interpretation\n")

    # Overall assessment
    if len(improved) == len(ordered):
        lines.append("GEPA optimization improved average quality across all agents.\n")
    elif len(regressed) == len(ordered):
        lines.append("GEPA optimization reduced average quality across all agents, "
                     "suggesting the optimized prompts traded gains in some metrics for "
                     "losses in others.\n")
    else:
        parts = []
        if improved:
            parts.append(f"**{', '.join(n.title() for n in improved)}** saw net improvement")
        if stable:
            parts.append(f"**{', '.join(n.title() for n in stable)}** held steady")
        if regressed:
            parts.append(f"**{', '.join(n.title() for n in regressed)}** saw net decline")
        lines.append("Results were mixed across agents: " + "; ".join(parts) + ".\n")

    # Metric-level tradeoff analysis
    if metrics_up or metrics_down:
        lines.append("### Metric-Level Tradeoffs\n")
        lines.append("GEPA optimization revealed a clear tradeoff pattern:\n")

        if metrics_up:
            lines.append("**Metrics that improved:**\n")
            for k, avg in sorted(metrics_up.items(), key=lambda x: -x[1]):
                best_agent = max(ordered, key=lambda n: metric_deltas[k][ordered.index(n)])
                best_val = metric_deltas[k][ordered.index(best_agent)]
                before_val = all_results[best_agent].get("before", {}).get(k, 0)
                after_val = all_results[best_agent].get("after", {}).get(k, 0)
                lines.append(f"- **{METRIC_LABELS[k]}** ({avg:+.3f} avg) — "
                             f"largest gain in {best_agent.title()} ({before_val:.2f} → {after_val:.2f})")
            lines.append("")

        if metrics_down:
            lines.append("**Metrics that declined:**\n")
            for k, avg in sorted(metrics_down.items(), key=lambda x: x[1]):
                worst_agent = min(ordered, key=lambda n: metric_deltas[k][ordered.index(n)])
                worst_val = metric_deltas[k][ordered.index(worst_agent)]
                before_val = all_results[worst_agent].get("before", {}).get(k, 0)
                after_val = all_results[worst_agent].get("after", {}).get(k, 0)
                lines.append(f"- **{METRIC_LABELS[k]}** ({avg:+.3f} avg) — "
                             f"largest drop in {worst_agent.title()} ({before_val:.2f} → {after_val:.2f})")
            lines.append("")

        lines.append("This tradeoff is expected: GEPA optimizes toward the eval criteria "
                     "in `sampler_config.json` (response match, safety, tool use). Metrics "
                     "not included as optimization targets — like instruction following and "
                     "response quality — may shift as the prompt is reshaped to maximize "
                     "target metrics.\n")

    # Per-agent insights
    lines.append("### Per-Agent Insights\n")
    for name in ordered:
        before = all_results[name].get("before", {})
        after = all_results[name].get("after", {})
        delta = agent_deltas[name]
        model = all_results[name].get("model", "")
        prompt_len = len(all_results[name].get("optimized_prompt", ""))

        agent_improved = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) > 0.03]
        agent_regressed = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) < -0.03]

        summary = f"**{name.title()}** (`{model}`, {prompt_len:,} char prompt): "
        summary += f"net {delta:+.3f}. "
        if agent_improved:
            summary += f"Gained in {', '.join(METRIC_LABELS[k] for k in agent_improved)}. "
        if agent_regressed:
            summary += f"Lost in {', '.join(METRIC_LABELS[k] for k in agent_regressed)}."
        lines.append(f"- {summary}\n")
    lines.append("")

    # Cost-quality insight
    lines.append("### Cost-Quality Assessment\n")
    lines.append(f"**Best value: {best_value_name.title()}** delivers the most quality per dollar. "
                 f"**Best absolute quality: {best_quality_name.title()}** at {best_quality:.2f} average.\n")

    top_blend = blended_cost(all_results[ordered[-1]].get("model", ""))
    lite_blend = blended_cost(all_results[ordered[0]].get("model", ""))
    if lite_blend > 0 and top_blend > 0:
        lite_quality = sum(all_results[ordered[0]].get("after", {}).values()) / max(
            len(all_results[ordered[0]].get("after", {})), 1)
        top_quality = sum(all_results[ordered[-1]].get("after", {}).values()) / max(
            len(all_results[ordered[-1]].get("after", {})), 1)
        lines.append(f"The most expensive model ({ordered[-1].title()} at ${top_blend:.2f}/M blended) costs "
                     f"**{top_blend/lite_blend:.0f}x more** than the cheapest ({ordered[0].title()} at "
                     f"${lite_blend:.2f}/M blended) but scores {top_quality:.2f} vs {lite_quality:.2f} — "
                     f"{'a marginal' if abs(top_quality - lite_quality) < 0.05 else 'a meaningful'} "
                     f"quality difference.\n")

    # Recommendations
    lines.append("### Recommendations\n")
    lines.append(f"1. **For cost-sensitive workloads:** Use **{best_value_name.title()}** "
                 f"(`{all_results[best_value_name].get('model', '')}`) — best quality-per-dollar ratio.\n")
    lines.append(f"2. **For quality-critical workloads:** Use **{best_quality_name.title()}** "
                 f"(`{all_results[best_quality_name].get('model', '')}`) — highest absolute quality.\n")

    if metrics_down:
        worst_metric = min(metrics_down, key=metrics_down.get)
        lines.append(f"3. **Re-optimize with expanded criteria.** The decline in "
                     f"{METRIC_LABELS[worst_metric]} ({metrics_down[worst_metric]:+.3f} avg) "
                     f"suggests adding it as an explicit optimization target in `sampler_config.json`.\n")

    lines.append(f"4. **Prompt cost is zero.** Optimization only changes the system prompt — "
                 f"no additional inference cost. Even mixed results are worth iterating on.\n")
    lines.append(f"5. **Monitor with online evaluators** after deployment to catch regressions "
                 f"on real traffic beyond the eval dataset.\n")

    return lines


def _detect_version(all_results: dict[str, dict]) -> str:
    """Detect the optimization version from results metadata or prompt files."""
    version = all_results.get("_eval_metadata", {}).get("version")
    if version:
        return version
    import re
    for data in all_results.values():
        if isinstance(data, dict) and data.get("optimized_prompt"):
            for key in data:
                m = re.match(r"(wrangler_v\d+)", str(key))
                if m:
                    return m.group(1)
    return "GEPA"


def generate_comparison_report(
    all_results: dict[str, dict],
    output_dir: str | None = None,
    case_metadata: list[dict] | None = None,
    version: str | None = None,
) -> str:
    """Generate a cross-model comparison report with cost-benefit and recommendations."""
    output_dir = Path(output_dir or REPORTS_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    version_label = version or _detect_version(all_results)

    lines = []
    lines.append("# GEPA Prompt Wrangler — Cross-Model Comparison Report\n")

    lines.append("## Pipeline Overview\n")
    lines.append("![GEPA Optimization Pipeline](diagrams/demo_pipeline.png)\n")

    # --- Prompt Evolution Summary ---
    lines.append("## Prompt Evolution Summary\n")
    lines.append("All agents started with the same generic 78-character prompt:\n")
    lines.append("```\nYou are a helpful assistant. Use the available tools to answer user questions.\n```\n")
    lines.append("GEPA expanded this into specialized, model-tailored instructions:\n")
    lines.append("| Agent | Model | Before | After | Expansion |")
    lines.append("|-------|-------|--------|-------|-----------|")

    ordered = [a for a in AGENT_ORDER if a in all_results]
    for name in ordered:
        data = all_results[name]
        model = data.get("model", "unknown")
        orig = data.get("original_prompt", "")
        opt = data.get("optimized_prompt", "")
        orig_len = len(orig)
        opt_len = len(opt) if opt else orig_len
        expansion = f"{opt_len/max(orig_len,1):.0f}x" if opt else "—"
        lines.append(f"| {name.title()} | `{model}` | {orig_len} chars | {opt_len} chars | {expansion} |")
    lines.append("")

    lines.append("![Before/After Overview](diagrams/before_after_overview.png)\n")

    # --- Eval Dataset ---
    lines.extend(_eval_dataset_section(case_metadata))

    # --- Before/After Score Comparison ---
    lines.append("## Baseline vs Optimized Scores\n")
    lines.append("### Baseline (Generic Prompt)\n")
    header = "| Metric |" + " | ".join(n.title() for n in ordered) + " |"
    sep = "|--------|" + " | ".join("------" for _ in ordered) + " |"
    lines.append(header)
    lines.append(sep)
    for key, label in METRIC_LABELS.items():
        row = f"| {label} |"
        for name in ordered:
            s = all_results[name].get("before", {}).get(key, 0)
            row += f" {s:.2f} |"
        lines.append(row)
    lines.append("")

    has_after = any(all_results[n].get("after") for n in ordered)
    has_std = any(all_results[n].get("after_std") for n in ordered)
    if has_after:
        lines.append(f"### After Optimization ({version_label})\n")
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for name in ordered:
                s = all_results[name].get("after", {}).get(key, 0)
                std = all_results[name].get("after_std", {}).get(key)
                if std and has_std:
                    row += f" {s:.2f} +/-{std:.2f} |"
                else:
                    row += f" {s:.2f} |"
            lines.append(row)
        lines.append("")

        lines.append("### Improvement Delta (After - Before)\n")
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for name in ordered:
                b = all_results[name].get("before", {}).get(key, 0)
                a = all_results[name].get("after", {}).get(key, 0)
                d = a - b
                row += f" {d:+.02f} |"
            lines.append(row)

        avg_row = "| **Average** |"
        for name in ordered:
            before = all_results[name].get("before", {})
            after = all_results[name].get("after", {})
            avg_b = sum(before.values()) / max(len(before), 1) if before else 0
            avg_a = sum(after.values()) / max(len(after), 1) if after else 0
            avg_row += f" **{avg_a - avg_b:+.02f}** |"
        lines.append(avg_row)
        lines.append("")

    # --- Per-Tier Comparison ---
    lines.extend(_comparison_tier_section(all_results, ordered, case_metadata))

    # --- Category Capability Matrix ---
    lines.extend(_category_heatmap_section(all_results, ordered, case_metadata))

    # --- Cost-Benefit Analysis ---
    lines.append("## Cost-Benefit Analysis\n")
    lines.append("### Per-Model Cost and Quality\n")
    lines.append("| Agent | Model | Input $/M | Output $/M | Blended $/M | Avg Quality (Before) | Avg Quality (After) | Quality Gain | Quality/$ |")
    lines.append("|-------|-------|-----------|------------|-------------|---------------------|--------------------|--------------|-----------:|")
    for name in ordered:
        data = all_results[name]
        model = data.get("model", "unknown")
        cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        blend = blended_cost(model)
        before = data.get("before", {})
        after = data.get("after", {})
        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        gain = avg_a - avg_b
        qpd = avg_a / max(blend, 0.01)
        lines.append(f"| {name.title()} | `{model}` | ${cost['input']:.2f} | ${cost['output']:.2f} | ${blend:.2f} | {avg_b:.2f} | {avg_a:.2f} | {gain:+.02f} | {qpd:.3f} |")
    lines.append("")

    lines.append("### Cost-Quality Tradeoff\n")
    lines.append("![Cost-Quality Tradeoff](charts/cost_quality.png)\n")

    lines.append("*Blended $/M = weighted average assuming 4:1 input:output token ratio. "
                 "Quality/$ = avg quality / blended cost. Higher is better.*\n")

    # Rank by quality per dollar
    ranked = sorted(ordered, key=lambda n: (
        sum((all_results[n].get("after") or all_results[n].get("before", {})).values()) /
        max(len((all_results[n].get("after") or all_results[n].get("before", {}))), 1)
    ) / max(blended_cost(all_results[n].get("model", "")), 0.01),
        reverse=True)

    lines.append("**Ranked by Quality/$:**\n")
    for i, name in enumerate(ranked, 1):
        model = all_results[name].get("model", "")
        blend = blended_cost(model)
        after = all_results[name].get("after") or all_results[name].get("before", {})
        avg = sum(after.values()) / max(len(after), 1)
        qpd = avg / max(blend, 0.01)
        lines.append(f"{i}. **{name.title()}** — {qpd:.3f} quality/$ (avg {avg:.2f} at ${blend:.2f}/M blended)")
    lines.append("")

    # --- Charts ---
    lines.append("## Evaluation Charts\n")
    lines.append("### Baseline Comparison\n")
    lines.append("![Baseline Comparison](charts/comparison.png)\n")
    if has_after:
        lines.append("### Optimization Impact\n")
        lines.append("![Improvement Delta](charts/improvement_delta.png)\n")

    charts_dir = Path(output_dir) / "charts"
    if (charts_dir / "tier_breakdown.png").exists():
        lines.append("### Tier Breakdown\n")
        lines.append("![Tier Breakdown](charts/tier_breakdown.png)\n")

    if (charts_dir / "category_heatmap.png").exists():
        lines.append("### Category Heatmap\n")
        lines.append("![Category Heatmap](charts/category_heatmap.png)\n")

    # --- Previous Run Comparison ---
    lines.extend(_previous_run_comparison_section(all_results, ordered))

    # --- Interpretation & Recommendations ---
    lines.extend(_interpretation_section(all_results, ordered, ranked))

    # --- Per-Agent Reports ---
    lines.append("## Per-Agent Reports\n")
    for name in ordered:
        lines.append(f"- [{name.title()} Agent Analysis](agents/{name}_analysis.md)")
    lines.append("")

    report_path = output_dir / "comparison_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    return str(report_path)
