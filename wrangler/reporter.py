"""Experiment report generation — charts + markdown for prompt optimization experiments."""

import csv
import re
import subprocess
import shutil
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .factory import AgentPromptPair
from .analysis import (
    normalize_agent_keys,
    generate_all_charts,
    METRIC_LABELS,
    AGENT_ORDER,
    MODEL_MAP,
    PROVIDERS,
)
from .config import MODEL_COSTS, blended_cost

REPORTS_DIR = Path("outputs/reports")
CHARTS_DIR = REPORTS_DIR / "charts"


def _ensure_dirs():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _extract_cost(description: str) -> float | None:
    """Parse cost from pair description. Looks for '$X.XX/M' pattern."""
    match = re.search(r"\$(\d+(?:\.\d+)?)/M", description)
    if match:
        return float(match.group(1))
    return None


def _get_case_metadata(results: dict) -> list[dict] | None:
    """Extract case metadata if embedded in results."""
    meta = results.get("_eval_metadata")
    if meta and "cases" in meta:
        return meta["cases"]
    return None


def _executive_summary(results: dict, ordered: list[str]) -> list[str]:
    """Auto-generate executive summary from result data."""
    lines = []
    lines.append("## Executive Summary\n")

    has_after = any(results[n].get("after") for n in ordered)
    if not has_after:
        lines.append("Baseline evaluation complete. No optimization results yet.\n")
        return lines

    agent_deltas = {}
    for name in ordered:
        before = results[name].get("before", {})
        after = results[name].get("after", {})
        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        agent_deltas[name] = avg_a - avg_b

    improved = [n for n in ordered if agent_deltas[n] > 0.005]
    regressed = [n for n in ordered if agent_deltas[n] < -0.005]
    stable = [n for n in ordered if abs(agent_deltas[n]) <= 0.005]

    best = max(ordered, key=lambda n: agent_deltas[n])
    worst = min(ordered, key=lambda n: agent_deltas[n])

    lines.append(f"**{len(improved)}/{len(ordered)} models improved** after GEPA optimization. ")
    if improved:
        lines.append(f"Best performer: **{best}** ({agent_deltas[best]:+.3f} avg). ")
    if regressed:
        lines.append(f"Largest regression: **{worst}** ({agent_deltas[worst]:+.3f} avg). ")
    lines.append("")

    if improved:
        lines.append(f"- **Improved:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in improved)}")
    if stable:
        lines.append(f"- **Stable:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in stable)}")
    if regressed:
        lines.append(f"- **Regressed:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in regressed)}")
    lines.append("")

    metric_avg_deltas = {}
    for metric in METRIC_LABELS:
        vals = []
        for name in ordered:
            b = results[name].get("before", {}).get(metric, 0)
            a = results[name].get("after", {}).get(metric, 0)
            vals.append(a - b)
        metric_avg_deltas[metric] = sum(vals) / len(vals) if vals else 0

    best_metric = max(metric_avg_deltas, key=metric_avg_deltas.get)
    worst_metric = min(metric_avg_deltas, key=metric_avg_deltas.get)
    if metric_avg_deltas[best_metric] > 0.005:
        lines.append(f"**Strongest metric gain:** {METRIC_LABELS[best_metric]} ({metric_avg_deltas[best_metric]:+.3f} avg across models)")
    if metric_avg_deltas[worst_metric] < -0.005:
        lines.append(f"**Largest metric decline:** {METRIC_LABELS[worst_metric]} ({metric_avg_deltas[worst_metric]:+.3f} avg across models)")
    lines.append("")

    return lines


def _methodology_section(results: dict, ordered: list[str], experiment_name: str) -> list[str]:
    """Experiment configuration and methodology."""
    lines = []
    lines.append("## Methodology\n")
    lines.append(f"**Experiment:** `{experiment_name}`\n")
    lines.append("| Agent | Model | Provider | Input $/M | Output $/M | Blended $/M |")
    lines.append("|-------|-------|----------|-----------|------------|-------------|")
    for name in ordered:
        model = results[name].get("model", "unknown")
        provider = PROVIDERS.get(model, "Unknown")
        cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        blend = blended_cost(model)
        lines.append(f"| {name} | `{model}` | {provider} | ${cost['input']:.2f} | ${cost['output']:.2f} | ${blend:.2f} |")
    lines.append("")

    lines.append("**Metrics evaluated:**\n")
    for key, label in METRIC_LABELS.items():
        lines.append(f"- {label} (`{key}`)")
    lines.append("")

    return lines


def _scores_section(results: dict, ordered: list[str]) -> list[str]:
    """Before/after score tables with deltas."""
    lines = []
    has_after = any(results[n].get("after") for n in ordered)
    has_std = any(results[n].get("after_std") for n in ordered)

    lines.append("## Evaluation Results\n")

    lines.append("### Baseline Scores (Generic Prompt)\n")
    header = "| Metric |" + " | ".join(n.title() for n in ordered) + " |"
    sep = "|--------|" + " | ".join("------" for _ in ordered) + " |"
    lines.append(header)
    lines.append(sep)
    for key, label in METRIC_LABELS.items():
        row = f"| {label} |"
        for name in ordered:
            s = results[name].get("before", {}).get(key, 0)
            row += f" {s:.2f} |"
        lines.append(row)
    lines.append("")

    if has_after:
        lines.append("### Post-Optimization Scores\n")
        lines.append(header)
        lines.append(sep)
        for key, label in METRIC_LABELS.items():
            row = f"| {label} |"
            for name in ordered:
                s = results[name].get("after", {}).get(key, 0)
                std = results[name].get("after_std", {}).get(key)
                if std and has_std:
                    row += f" {s:.2f} ±{std:.2f} |"
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
                b = results[name].get("before", {}).get(key, 0)
                a = results[name].get("after", {}).get(key, 0)
                d = a - b
                row += f" {d:+.02f} |"
            lines.append(row)

        avg_row = "| **Average** |"
        for name in ordered:
            before = results[name].get("before", {})
            after = results[name].get("after", {})
            avg_b = sum(before.values()) / max(len(before), 1) if before else 0
            avg_a = sum(after.values()) / max(len(after), 1) if after else 0
            avg_row += f" **{avg_a - avg_b:+.02f}** |"
        lines.append(avg_row)
        lines.append("")

    return lines


def _per_model_section(results: dict, ordered: list[str]) -> list[str]:
    """Per-model analysis with key observations."""
    lines = []
    has_after = any(results[n].get("after") for n in ordered)
    if not has_after:
        return lines

    lines.append("## Per-Model Analysis\n")

    for name in ordered:
        before = results[name].get("before", {})
        after = results[name].get("after", {})
        model = results[name].get("model", "unknown")
        cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        blend = blended_cost(model)

        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        delta = avg_a - avg_b

        improved = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) > 0.01]
        regressed_m = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) < -0.01]

        verdict = "improved" if delta > 0.005 else ("regressed" if delta < -0.005 else "stable")

        lines.append(f"### {name.title()} (`{model}`, ${cost['input']:.2f}/${cost['output']:.2f} in/out per M)\n")
        lines.append(f"**Overall:** {avg_b:.2f} → {avg_a:.2f} ({delta:+.3f}, {verdict})\n")

        if improved:
            lines.append(f"- **Gained:** {', '.join(METRIC_LABELS[k] for k in improved)}")
        if regressed_m:
            lines.append(f"- **Lost:** {', '.join(METRIC_LABELS[k] for k in regressed_m)}")

        opt_prompt = results[name].get("optimized_prompt", "")
        orig_prompt = results[name].get("original_prompt", "")
        if opt_prompt:
            lines.append(f"- **Prompt expansion:** {len(orig_prompt)} → {len(opt_prompt)} chars "
                         f"({len(opt_prompt)/max(len(orig_prompt),1):.0f}x)")
        lines.append("")

    return lines


def _cost_benefit_section(results: dict, ordered: list[str]) -> list[str]:
    """Cost-benefit analysis with quality/$ ranking."""
    lines = []
    lines.append("## Cost-Benefit Analysis\n")
    lines.append("| Agent | Model | Input $/M | Output $/M | Blended $/M | Before | After | Delta | Quality/$ |")
    lines.append("|-------|-------|-----------|------------|-------------|--------|-------|-------|----------|")

    for name in ordered:
        model = results[name].get("model", "unknown")
        cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        blend = blended_cost(model)
        before = results[name].get("before", {})
        after = results[name].get("after", before)
        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        delta = avg_a - avg_b
        qpd = avg_a / max(blend, 0.01)
        lines.append(f"| {name.title()} | `{model}` | ${cost['input']:.2f} | ${cost['output']:.2f} | ${blend:.2f} | {avg_b:.2f} | {avg_a:.2f} | {delta:+.02f} | {qpd:.3f} |")

    lines.append("")
    lines.append("*Blended $/M = weighted average assuming 4:1 input:output token ratio. Quality/$ = avg quality / blended cost.*\n")
    return lines


def _charts_section() -> list[str]:
    """Embed chart images with captions."""
    lines = []
    lines.append("## Visualizations\n")

    chart_info = [
        ("radar.png", "Metric Profiles", "Radar overlay showing each model's strength/weakness pattern across all 6 metrics."),
        ("comparison.png", "Baseline Comparison", "Grouped bar chart of pre-optimization scores across all agents."),
        ("improvement_delta.png", "Optimization Impact", "Per-metric score change from GEPA optimization. Bars above zero = improved."),
        ("cost_quality.png", "Cost-Quality Tradeoff", "Model cost vs average quality. Arrows show before→after movement."),
        ("tier_breakdown.png", "Tier Performance", "Average scores by complexity tier (low/medium/high)."),
        ("category_heatmap.png", "Category Capability", "Heatmap of per-category scores across models."),
        ("tier_improvement_heatmap.png", "Tier Improvement", "Optimization impact by complexity tier. Green=improved, red=regressed."),
        ("run_comparison.png", "Run Comparison", "Side-by-side comparison with previous experiment run."),
    ]

    for filename, title, caption in chart_info:
        if (CHARTS_DIR / filename).exists():
            lines.append(f"### {title}\n")
            lines.append(f"![{title}](../images/{filename})\n")
            lines.append(f"*{caption}*\n")

    return lines


def _conclusions_section(results: dict, ordered: list[str]) -> list[str]:
    """Auto-generated conclusions and next steps."""
    lines = []
    has_after = any(results[n].get("after") for n in ordered)
    if not has_after:
        return lines

    lines.append("## Conclusions & Next Steps\n")

    agent_deltas = {}
    for name in ordered:
        before = results[name].get("before", {})
        after = results[name].get("after", {})
        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        agent_deltas[name] = avg_a - avg_b

    improved = [n for n in ordered if agent_deltas[n] > 0.005]
    regressed = [n for n in ordered if agent_deltas[n] < -0.005]

    if len(improved) >= len(ordered) * 0.6:
        lines.append("GEPA optimization was broadly successful. ")
    elif len(regressed) >= len(ordered) * 0.6:
        lines.append("GEPA optimization showed widespread regression — review sampler config thresholds and eval criteria alignment. ")
    else:
        lines.append("Results were mixed across models. ")

    lines.append("")
    lines.append("**Recommended next steps:**\n")

    if regressed:
        metric_deltas = {}
        for metric in METRIC_LABELS:
            vals = []
            for name in regressed:
                b = results[name].get("before", {}).get(metric, 0)
                a = results[name].get("after", {}).get(metric, 0)
                vals.append(a - b)
            metric_deltas[metric] = sum(vals) / len(vals) if vals else 0
        worst = min(metric_deltas, key=metric_deltas.get)
        lines.append(f"1. **Investigate {METRIC_LABELS[worst]} regression** ({metric_deltas[worst]:+.3f} avg in regressed models) — "
                     f"consider adding as explicit optimization target in sampler config")

    lines.append(f"2. **Re-run with tighter thresholds** — higher thresholds force GEPA to discover domain-specific content")
    lines.append(f"3. **Verify per-case scores** are being extracted correctly for tier/category analysis")
    lines.append(f"4. **Monitor deployed agents** with online evaluators to catch drift on real traffic")
    lines.append("")

    return lines


def generate_report(
    results: dict[str, dict],
    experiment_name: str = "experiment",
    use_paperbanana: bool = True,
):
    """Generate full markdown report with rich charts and analytical sections."""
    _ensure_dirs()

    normalized = normalize_agent_keys(results)
    case_metadata = _get_case_metadata(results)
    ordered = [a for a in AGENT_ORDER if a in normalized]

    if not ordered:
        ordered = sorted(normalized.keys())

    print("Generating charts...")
    generate_all_charts(normalized, case_metadata, CHARTS_DIR, use_paperbanana=use_paperbanana)

    lines = []
    lines.append(f"# GEPA Prompt Wrangler — {experiment_name}\n")

    lines.extend(_executive_summary(normalized, ordered))
    lines.extend(_methodology_section(normalized, ordered, experiment_name))
    lines.extend(_charts_section())
    lines.extend(_scores_section(normalized, ordered))
    lines.extend(_per_model_section(normalized, ordered))
    lines.extend(_cost_benefit_section(normalized, ordered))
    lines.extend(_conclusions_section(normalized, ordered))

    lines.append("## Optimized Prompts\n")
    for name in ordered:
        data = normalized[name]
        lines.append(f"### {name.title()}\n")
        lines.append(f"**Model:** `{data.get('model', 'unknown')}`\n")
        if data.get("optimized_prompt"):
            lines.append("<details><summary>Click to expand optimized prompt</summary>\n")
            lines.append(f"```\n{data['optimized_prompt'].strip()}\n```\n")
            lines.append("</details>\n")

    report_path = REPORTS_DIR / "experiment_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Report saved to: {report_path}")
