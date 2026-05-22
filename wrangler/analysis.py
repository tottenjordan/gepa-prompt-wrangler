"""Per-agent analysis markdown report generator."""

from pathlib import Path

from .config import MODEL_COSTS, REPORTS_DIR

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


def generate_agent_report(
    agent_name: str,
    model: str,
    engine_id: str,
    original_prompt: str,
    optimized_prompt: str | None,
    before_scores: dict[str, float],
    after_scores: dict[str, float] | None,
    output_dir: str | None = None,
) -> str:
    """Generate a per-agent analysis markdown file. Returns the file path."""
    output_dir = Path(output_dir or REPORTS_DIR) / "agents"
    output_dir.mkdir(parents=True, exist_ok=True)

    cost = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    provider = PROVIDERS.get(model, "Unknown")

    lines = []
    lines.append(f"# {agent_name.replace('_', ' ').title()} — GEPA Optimization Analysis\n")

    lines.append("## Agent Configuration\n")
    lines.append(f"- **Model:** `{model}`")
    lines.append(f"- **Provider:** {provider}")
    lines.append(f"- **Input cost:** ${cost['input']}/M tokens")
    lines.append(f"- **Output cost:** ${cost['output']}/M tokens")
    lines.append(f"- **Engine ID:** `{engine_id}`\n")

    lines.append("## Eval Dataset\n")
    lines.append("- **Total cases:** 30")
    lines.append("- **Low complexity:** 14 cases (single tool call)")
    lines.append("- **Medium complexity:** 9 cases (2 tools, comparison)")
    lines.append("- **High complexity:** 7 cases (3+ tools, cross-domain)")
    lines.append("- **Tool coverage:** search_mcp (2), booking_mcp (2), expense_mcp (3)\n")

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
        lines.append(f"```\n{optimized_prompt}\n```\n")

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
        lines.append("| Metric | Before | After | Delta | Change |")
        lines.append("|--------|--------|-------|-------|--------|")
        for key in METRIC_LABELS:
            b = before_scores.get(key, 0)
            a = after_scores.get(key, 0)
            delta = a - b
            pct = f"{delta/b*100:+.0f}%" if b > 0 else "N/A"
            lines.append(f"| {METRIC_LABELS[key]} | {b:.2f} | {a:.2f} | {delta:+.2f} | {pct} |")
        lines.append("")

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

    report_path = output_dir / f"{agent_name}_analysis.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    return str(report_path)
