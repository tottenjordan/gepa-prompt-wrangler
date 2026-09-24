"""Chart generation for GEPA experiments."""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ..core.config import REPORTS_DIR
from ..core.models import AGENT_ORDER, MODEL_MAP
from ..eval.evaluator import case_metrics

METRIC_LABELS = {
    "final_response_quality_v1": "Response Quality",
    "hallucination_v1": "Hallucination",
    "safety_v1": "Safety",
    "tool_use_quality_v1": "Tool Use",
    "instruction_following_v1": "Instruction Following",
}

# PROVIDERS, MODEL_MAP, and AGENT_ORDER used to be hand-written here. They now
# live in wrangler/core/models.py, derived from the registry; the consumers in
# this package import them from there directly. Keeping a second copy next to
# the registry is exactly how the two drifted apart.


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

    _fig, ax = plt.subplots(figsize=(14, 7))
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
    print("  Generated: comparison.png")


def generate_cost_quality_chart(results: dict, charts_dir: Path | None = None):
    """Per-metric cost vs quality panels with before/after arrows and a Pareto frontier.

    **The matplotlib FALLBACK for `charts.generate_cost_quality_chart_pb`.** Per CLAUDE.md
    PaperBanana draws the figures; this runs when it is unreachable. Both must show the same
    thing or the report's content depends on whether an API call succeeded.

    This used to plot ``np.mean(list(before_scores.values()))`` -- five metrics averaged into
    one number -- against ``blended_cost``, which is list price at an assumed 4:1 ratio. Per
    metric floors span 3.4x and campaign 09 measured metrics moving in opposite directions, so
    the average hid the tradeoff the chart existed to show. Cost is now priced from the run's
    own token counts, which are themselves estimates (``len(text)//4``) and labelled as such.
    """
    from .frontier import default_resolutions, frontier_for_metric, points_from_results

    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)

    after = points_from_results(results, "after")
    before = {p.arm: p for p in points_from_results(results, "before")}
    resolutions = default_resolutions(after)
    metrics = sorted(resolutions)
    if not after or not metrics:
        return

    cols = min(3, len(metrics))
    rows = (len(metrics) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 4.6 * rows), squeeze=False)

    for idx, metric in enumerate(metrics):
        ax = axes[idx // cols][idx % cols]
        on_frontier = set(frontier_for_metric(after, metric, resolutions[metric]))
        for point in after:
            if metric not in point.quality:
                continue
            is_gemini = "gemini" in (point.model or "")
            colour = "#2563EB" if is_gemini else "#EA580C"
            prior = before.get(point.arm)
            if prior is not None and metric in prior.quality:
                ax.annotate(
                    "",
                    xy=(point.cost_usd, point.quality[metric]),
                    xytext=(prior.cost_usd, prior.quality[metric]),
                    arrowprops={"arrowstyle": "->", "color": "gray", "lw": 1.0, "ls": "--"},
                )
                ax.scatter(
                    prior.cost_usd,
                    prior.quality[metric],
                    s=70,
                    c="#93C5FD" if is_gemini else "#FDBA74",
                    edgecolors="black",
                    linewidth=0.4,
                    zorder=4,
                )
            ax.scatter(
                point.cost_usd,
                point.quality[metric],
                s=150,
                c=colour,
                marker="D" if point.arm in on_frontier else "o",
                edgecolors="black",
                linewidth=0.6,
                zorder=5,
            )
            # The resolution, drawn. A reader can see directly whether two arms are
            # separated by more than this design can detect.
            ax.errorbar(
                point.cost_usd,
                point.quality[metric],
                yerr=resolutions[metric] / 2,
                fmt="none",
                ecolor="gray",
                alpha=0.5,
                capsize=3,
                zorder=3,
            )
            ax.annotate(
                point.arm,
                (point.cost_usd, point.quality[metric]),
                textcoords="offset points",
                xytext=(8, 5),
                fontsize=7,
                color="gray",
            )

        # Only the monotone envelope gets a line. Connecting every non-dominated point in
        # cost order draws a zigzag, and a line labelled "frontier" asserts a trade-off
        # boundary -- paying more buys more. When arms are mutually non-dominated because
        # they sit inside the resolution, no such boundary exists and the honest rendering
        # is the markers alone.
        ranked = sorted(
            (p for p in after if p.arm in on_frontier and metric in p.quality),
            key=lambda p: p.cost_usd,
        )
        edge: list = []
        for point in ranked:
            if not edge or point.quality[metric] > edge[-1].quality[metric]:
                edge.append(point)
        if len(edge) >= 2:
            ax.plot(
                [p.cost_usd for p in edge],
                [p.quality[metric] for p in edge],
                color="#10B981",
                lw=2.0,
                alpha=0.7,
                zorder=2,
            )
        ax.set_title(f"{metric}  (resolution {resolutions[metric]:.3f})", fontsize=9)
        ax.set_xlabel("estimated $ per run", fontsize=8)
        ax.set_ylabel("score", fontsize=8)
        ax.grid(alpha=0.2)

    for spare in range(len(metrics), rows * cols):
        axes[spare // cols][spare % cols].axis("off")

    fig.suptitle(
        "Cost vs quality, per metric — diamonds are on that metric's frontier.\n"
        "Costs are ESTIMATED (token counts are len(text)//4); bars show the design's "
        "minimum detectable effect.",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(charts_dir / "cost_quality.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


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
    _fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    has_std = any(results[a].get("after_std") for a in agents)
    for i, metric in enumerate(metrics):
        deltas = [
            results[a].get("after", {}).get(metric, 0) - results[a].get("before", {}).get(metric, 0)
            for a in agents
        ]
        yerr = [results[a].get("after_std", {}).get(metric, 0) for a in agents] if has_std else None
        ax.bar(
            x + i * width,
            deltas,
            width,
            label=METRIC_LABELS[metric],
            color=colors[i],
            yerr=yerr,
            capsize=2 if yerr else 0,
        )

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
    print("  Generated: improvement_delta.png")


def generate_tier_breakdown_chart(
    results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None
):
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
            tier_avgs[name] = dict.fromkeys(tiers_present, 0)

    _fig, ax = plt.subplots(figsize=(12, 7))
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
        ax.bar(
            x + offset + i * width,
            values,
            width,
            label=name.title(),
            color=color,
            alpha=min(alpha, 1.0),
            edgecolor="black",
            linewidth=0.5,
        )

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
    print("  Generated: tier_breakdown.png")


def generate_category_heatmap(
    results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None
):
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

    categories = sorted({m.get("category", "") for m in case_metadata if m.get("category")})
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
    print("  Generated: category_heatmap.png")


def generate_run_comparison_chart(
    results: dict,
    previous_path: str = "outputs/results_all_agents.json",
    charts_dir: Path | None = None,
):
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

    _fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(agents))
    width = 0.35

    ax.bar(
        x - width / 2,
        prev_avgs,
        width,
        label="Previous Run",
        color="#93C5FD",
        edgecolor="black",
        linewidth=0.5,
    )
    ax.bar(
        x + width / 2,
        curr_avgs,
        width,
        label="Current Run",
        color="#2563EB",
        edgecolor="black",
        linewidth=0.5,
    )

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
    print("  Generated: run_comparison.png")


def generate_radar_chart(results: dict, charts_dir: Path | None = None):
    """Radar/spider chart overlaying all metrics for each model."""
    charts_dir = Path(charts_dir or CHARTS_DIR)
    charts_dir.mkdir(parents=True, exist_ok=True)
    agents = _get_agents(results)
    phase = "after" if any(results[a].get("after") for a in agents) else "before"

    metrics = list(METRIC_LABELS.keys())
    labels = [METRIC_LABELS[m] for m in metrics]
    n_metrics = len(metrics)
    angles = np.linspace(0, 2 * np.pi, n_metrics, endpoint=False).tolist()
    angles += angles[:1]

    _fig, ax = plt.subplots(figsize=(10, 10), subplot_kw={"polar": True})
    gemini_cmap = plt.cm.Blues
    claude_cmap = plt.cm.Oranges

    gemini_agents = [a for a in agents if "gemini" in results[a].get("model", "")]
    claude_agents = [a for a in agents if a not in gemini_agents]

    for _idx, name in enumerate(agents):
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
    print("  Generated: radar.png")


def generate_tier_improvement_heatmap(
    results: dict, case_metadata: list[dict] | None, charts_dir: Path | None = None
):
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

    has_per_case = any(
        results[a].get("before_per_case") and results[a].get("after_per_case") for a in agents
    )
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
            ax.text(
                j,
                i,
                f"{val:+.3f}",
                ha="center",
                va="center",
                color=color,
                fontsize=10,
                fontweight="bold",
            )

    ax.set_title("GEPA Optimization Impact by Tier (After - Before)")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Avg Score Delta")
    plt.tight_layout()
    plt.savefig(charts_dir / "tier_improvement_heatmap.png", dpi=150)
    plt.close()
    print("  Generated: tier_improvement_heatmap.png")


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
        # Strip the reserved case-index key: it is an identifier, not a metric,
        # and would otherwise be averaged and charted as one.
        all_metrics = set()
        for s in score_list:
            all_metrics.update(case_metrics(s).keys())
        avg: dict[str, float] = {}
        for metric in all_metrics:
            values = [s[metric] for s in score_list if metric in s]
            avg[metric] = sum(values) / len(values) if values else 0.0
        result[group] = avg
    return result
