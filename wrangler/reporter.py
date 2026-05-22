"""Analysis report generation — charts + markdown for prompt optimization experiments."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .factory import AgentPromptPair

REPORTS_DIR = Path("outputs/reports")
CHARTS_DIR = REPORTS_DIR / "charts"

METRIC_LABELS = {
    "final_response_quality_v1": "Quality",
    "hallucination_v1": "Hallucination",
    "safety_v1": "Safety",
    "tool_use_quality_v1": "Tool Use",
    "instruction_following_v1": "Instruction",
    "final_response_match_v2": "Response Match",
}


def _ensure_dirs():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def generate_comparison_chart(results: dict[str, dict]):
    """Grouped bar chart comparing all pairs across metrics."""
    _ensure_dirs()
    pairs = list(results.keys())
    metrics = list(METRIC_LABELS.keys())
    n = len(pairs)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        values = [results[p].get("after", {}).get(metric, 0) for p in pairs]
        ax.bar(x + i * width, values, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent-Prompt Pair")
    ax.set_ylabel("Score")
    ax.set_title("Post-Optimization Scores — All Pairs")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels(pairs, rotation=15, ha="right")
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "comparison.png", dpi=150)
    plt.close()


def generate_improvement_chart(results: dict[str, dict]):
    """Bar chart showing improvement delta per pair."""
    _ensure_dirs()
    pairs = list(results.keys())
    metrics = list(METRIC_LABELS.keys())
    n = len(pairs)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        deltas = []
        for p in pairs:
            before = results[p].get("before", {}).get(metric, 0)
            after = results[p].get("after", {}).get(metric, 0)
            deltas.append(after - before)
        ax.bar(x + i * width, deltas, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent-Prompt Pair")
    ax.set_ylabel("Score Change")
    ax.set_title("GEPA Optimization Impact (After - Before)")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels(pairs, rotation=15, ha="right")
    ax.legend(fontsize=8)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "improvement_delta.png", dpi=150)
    plt.close()


def generate_report(results: dict[str, dict], experiment_name: str = "experiment"):
    """Generate full markdown report with charts."""
    _ensure_dirs()
    generate_comparison_chart(results)
    generate_improvement_chart(results)

    lines = []
    lines.append(f"# GEPA Prompt Wrangler — {experiment_name}\n")
    lines.append("## Results Overview\n")
    lines.append("![Comparison](charts/comparison.png)\n")
    lines.append("![Improvement Delta](charts/improvement_delta.png)\n")

    lines.append("## Before vs After Scores\n")
    lines.append("| Pair | Metric | Before | After | Delta | Change |")
    lines.append("|------|--------|--------|-------|-------|--------|")

    for pair_id, data in results.items():
        before = data.get("before", {})
        after = data.get("after", {})
        for metric in METRIC_LABELS:
            b = before.get(metric, 0)
            a = after.get(metric, 0)
            delta = a - b
            pct = f"{delta/b*100:+.0f}%" if b > 0 else "N/A"
            lines.append(
                f"| {pair_id} | {METRIC_LABELS[metric]} "
                f"| {b:.2f} | {a:.2f} | {delta:+.2f} | {pct} |"
            )
    lines.append("")

    lines.append("## Optimized Prompts\n")
    for pair_id, data in results.items():
        lines.append(f"### {pair_id}\n")
        lines.append(f"**Model:** `{data.get('model', 'unknown')}`\n")
        if data.get("optimized_prompt"):
            lines.append("**Optimized instruction:**")
            lines.append(f"```\n{data['optimized_prompt']}\n```\n")

    report_path = REPORTS_DIR / "experiment_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Report saved to: {report_path}")
