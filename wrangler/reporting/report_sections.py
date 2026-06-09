"""Report section generators for per-agent and cross-model analysis."""

import json
from collections import defaultdict
from pathlib import Path

from ..core.config import MODEL_COSTS, REPORTS_DIR, blended_cost
from .analysis import (
    METRIC_LABELS, PROVIDERS, AGENT_ORDER, MODEL_MAP, TIER_ORDER,
    normalize_agent_keys, compute_tier_scores,
)


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
