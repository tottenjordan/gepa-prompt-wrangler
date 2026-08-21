"""Experiment analysis — per-pair score diffs, prompt diffs, metric breakdowns."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.models import blended_cost_for_report as blended_cost

if TYPE_CHECKING:
    from ..orchestration.experiment import Experiment

METRIC_LABELS = {
    "final_response_quality_v1": "Quality",
    "hallucination_v1": "Hallucination",
    "safety_v1": "Safety",
    "tool_use_quality_v1": "Tool Use",
    "instruction_following_v1": "Instruction",
}


@dataclass
class PairAnalysis:
    """Analysis results for a single agent-prompt pair."""

    pair_id: str
    model: str
    description: str = ""
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    before_std: dict[str, float] = field(default_factory=dict)
    after_std: dict[str, float] = field(default_factory=dict)
    original_prompt: str = ""
    optimized_prompt: str = ""
    before_per_case: list[dict[str, float]] = field(default_factory=list)
    after_per_case: list[dict[str, float]] = field(default_factory=list)

    @property
    def deltas(self) -> dict[str, float]:
        metrics = set(self.before) | set(self.after)
        return {m: self.after.get(m, 0) - self.before.get(m, 0) for m in metrics}

    @property
    def avg_before(self) -> float:
        return sum(self.before.values()) / max(len(self.before), 1)

    @property
    def avg_after(self) -> float:
        return sum(self.after.values()) / max(len(self.after), 1)

    @property
    def improved_metrics(self) -> list[str]:
        return [m for m, d in self.deltas.items() if d > 0.005]

    @property
    def degraded_metrics(self) -> list[str]:
        return [m for m, d in self.deltas.items() if d < -0.005]

    @property
    def prompt_char_delta(self) -> int:
        return len(self.optimized_prompt) - len(self.original_prompt)

    @property
    def prompt_char_pct(self) -> float:
        if not self.original_prompt:
            return 0.0
        return self.prompt_char_delta / len(self.original_prompt) * 100


@dataclass
class ExperimentAnalysis:
    """Full analysis of an experiment's before/after results."""

    experiment_name: str
    pairs: list[PairAnalysis] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def overall_improved(self) -> bool:
        return sum(p.avg_after - p.avg_before for p in self.pairs) > 0


def analyze_experiment(exp: Experiment) -> ExperimentAnalysis:
    """Compare before/after for all pairs, identify regressions and improvements."""
    eval_before = exp.read_stage("eval_before")
    eval_after = exp.read_stage("eval_after")
    optimize_data = exp.read_stage("optimize")
    deploy_data = exp.read_stage("deploy")

    # Thresholds come from the sampler_config.json GEPA actually optimized
    # against, recorded per-pair by stage_optimize (single source of truth).
    thresholds: dict[str, float] = {}
    for pair_id in exp.pair_ids:
        thresholds.update(optimize_data.get(pair_id, {}).get("thresholds", {}))

    analysis = ExperimentAnalysis(
        experiment_name=exp.name,
        thresholds=thresholds,
    )

    pair_descriptions = {p["id"]: p.get("description", "") for p in exp.config.get("pairs", [])}

    for pair_id in exp.pair_ids:
        before_data = eval_before.get(pair_id, {})
        after_data = eval_after.get(pair_id, {})
        opt_data = optimize_data.get(pair_id, {})
        dep_data = deploy_data.get(pair_id, {})

        pa = PairAnalysis(
            pair_id=pair_id,
            model=dep_data.get("model", ""),
            description=pair_descriptions.get(pair_id, ""),
            before=before_data.get("scores", {}),
            after=after_data.get("scores", {}),
            before_std=before_data.get("scores_std", {}),
            after_std=after_data.get("scores_std", {}),
            original_prompt=dep_data.get("original_prompt", ""),
            optimized_prompt=opt_data.get("optimized_prompt", ""),
            before_per_case=before_data.get("per_case", []),
            after_per_case=after_data.get("per_case", []),
        )
        analysis.pairs.append(pa)

    return analysis


def _prompt_diff_summary(original: str, optimized: str) -> str:
    """Generate a readable diff summary of prompt changes."""
    if not original or not optimized:
        return "(no prompt comparison available)"

    orig_lines = original.splitlines(keepends=True)
    opt_lines = optimized.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(orig_lines, opt_lines, fromfile="original", tofile="optimized", n=2)
    )

    if not diff:
        return "(prompts are identical)"

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return f"+{added} lines / -{removed} lines"


def _find_removed_content(
    original: str, optimized: str, keywords: list[str] | None = None
) -> list[str]:
    """Identify significant content removed from the original prompt."""
    if not original or not optimized:
        return []

    if keywords is None:
        keywords = [
            "$",
            "policy",
            "limit",
            "maximum",
            "threshold",
            "must",
            "require",
            "tool",
            "always",
            "never",
        ]

    opt_lower = optimized.lower()
    removed = []
    for line in original.splitlines():
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 10:
            continue
        if line_stripped.lower() not in opt_lower and any(
            kw in line_stripped.lower() for kw in keywords
        ):
            removed.append(line_stripped)
    return removed


# ── MCP Tool Usage Audit ──────────────────────────────────────


TOOL_KEYWORDS = [
    "search",
    "book",
    "cancel",
    "expense",
    "hotel",
    "flight",
    "policy",
    "submit",
    "lookup",
    "check",
    "tool",
    "mcp",
]


@dataclass
class GepaRunStats:
    """Stats from a single GEPA optimization run's logs."""

    pair_id: str
    app_name: str
    total_lines: int = 0
    error_count: int = 0
    warning_count: int = 0
    timeout_count: int = 0
    tool_failure_count: int = 0
    log_exists: bool = False


def _resolve_app_name(pair_id: str, exp: Experiment) -> str:
    """Map pair_id to the GEPA app_name (the *_opt directory name)."""
    manifest = exp.manifest
    for pair in manifest.pairs:
        if pair.id == pair_id:
            agent_ref = pair.agent_module or manifest.agent_module
            stem = Path(agent_ref).stem.replace("_agent", "")
            return f"{stem}_opt"
    return ""


def _extract_gepa_run_stats(exp: Experiment) -> dict[str, GepaRunStats]:
    """Parse GEPA run logs for each pair."""
    stats: dict[str, GepaRunStats] = {}

    for pair_id in exp.pair_ids:
        app_name = _resolve_app_name(pair_id, exp)
        gs = GepaRunStats(pair_id=pair_id, app_name=app_name)

        log_path = Path("outputs/gepa_runs") / app_name / "run_log_stderr.txt"
        if not log_path.exists():
            stats[pair_id] = gs
            continue

        gs.log_exists = True
        with open(log_path) as f:
            for line in f:
                gs.total_lines += 1
                lower = line.lower()
                if "error" in lower:
                    gs.error_count += 1
                if "warning" in lower:
                    gs.warning_count += 1
                if "timeout" in lower:
                    gs.timeout_count += 1
                if "failed to get tools" in lower:
                    gs.tool_failure_count += 1

        stats[pair_id] = gs

    return stats


def _analyze_tool_keywords(original: str, optimized: str) -> dict:
    """Compare tool-related keyword presence between original and optimized prompts."""

    def find_keywords(text: str) -> set[str]:
        lower = text.lower()
        return {kw for kw in TOOL_KEYWORDS if kw in lower}

    orig_kw = find_keywords(original)
    opt_kw = find_keywords(optimized)

    return {
        "original_keywords": sorted(orig_kw),
        "optimized_keywords": sorted(opt_kw),
        "added": sorted(opt_kw - orig_kw),
        "dropped": sorted(orig_kw - opt_kw),
    }


def _format_tool_audit(
    analysis: ExperimentAnalysis, run_stats: dict[str, GepaRunStats]
) -> list[str]:
    """Generate markdown lines for the MCP tool audit section."""
    lines: list[str] = []
    lines.append("## MCP Tool Usage Audit\n")

    # GEPA run error summary
    has_logs = any(s.log_exists for s in run_stats.values())
    if has_logs:
        lines.append("### GEPA Run Log Summary\n")
        lines.append("| Pair | Log Lines | Errors | Warnings | Timeouts | Tool Failures |")
        lines.append("|------|----------|--------|----------|----------|---------------|")
        for p in analysis.pairs:
            s = run_stats.get(p.pair_id)
            if s and s.log_exists:
                lines.append(
                    f"| {p.pair_id} | {s.total_lines} | {s.error_count} "
                    f"| {s.warning_count} | {s.timeout_count} | {s.tool_failure_count} |"
                )
            else:
                lines.append(f"| {p.pair_id} | — | — | — | — | — |")
        lines.append("")

        total_timeouts = sum(s.timeout_count for s in run_stats.values())
        total_failures = sum(s.tool_failure_count for s in run_stats.values())
        if total_timeouts > 0:
            lines.append(f"**{total_timeouts} total MCP timeouts** across all runs — ")
            lines.append("GEPA iterations with tool timeouts run without tool outputs, ")
            lines.append("reducing optimization signal for tool-dependent behavior.\n")
        if total_failures > 0:
            lines.append(f"**{total_failures} total tool acquisition failures** — ")
            lines.append("these iterations could not call any MCP tools.\n")
    else:
        lines.append("*No GEPA run logs found in `outputs/gepa_runs/`.*\n")

    # Tool keyword preservation
    lines.append("### Tool Keyword Preservation\n")
    lines.append("Checks whether optimized prompts retained tool-related terminology.\n")
    lines.append("| Pair | Original Keywords | Optimized Keywords | Added | Dropped |")
    lines.append("|------|------------------|-------------------|-------|---------|")
    for p in analysis.pairs:
        kw = _analyze_tool_keywords(p.original_prompt, p.optimized_prompt)
        lines.append(
            f"| {p.pair_id} "
            f"| {', '.join(kw['original_keywords']) or '—'} "
            f"| {', '.join(kw['optimized_keywords']) or '—'} "
            f"| {', '.join(kw['added']) or '—'} "
            f"| {', '.join(kw['dropped']) or '—'} |"
        )
    lines.append("")

    return lines


def format_analysis_report(
    analysis: ExperimentAnalysis,
    run_stats: dict[str, GepaRunStats] | None = None,
) -> str:
    """Generate a markdown analysis report."""
    lines: list[str] = []
    lines.append(f"# Analysis Report — {analysis.experiment_name}\n")
    lines.append(f"Generated: {datetime.now(tz=UTC).isoformat(timespec='seconds')}\n")

    # --- Aggregate summary ---
    lines.append("## Summary\n")
    lines.append("| Pair | Model | Avg Before | Avg After | Delta | Verdict |")
    lines.append("|------|-------|-----------|----------|-------|---------|")
    for p in analysis.pairs:
        delta = p.avg_after - p.avg_before
        verdict = "improved" if delta > 0.005 else "regressed" if delta < -0.005 else "unchanged"
        lines.append(
            f"| {p.pair_id} | {p.model} "
            f"| {p.avg_before:.3f} | {p.avg_after:.3f} "
            f"| {delta:+.3f} | {verdict} |"
        )
    lines.append("")

    # --- Per-metric breakdown ---
    lines.append("## Per-Metric Breakdown\n")
    all_metrics = sorted({m for p in analysis.pairs for m in (set(p.before) | set(p.after))})

    for metric in all_metrics:
        label = METRIC_LABELS.get(metric, metric)
        lines.append(f"### {label} (`{metric}`)\n")

        threshold = analysis.thresholds.get(metric)
        if threshold is not None:
            lines.append(f"GEPA threshold: **{threshold}**\n")

        lines.append("| Pair | Before | After | Delta | Significant? |")
        lines.append("|------|--------|-------|-------|-------------|")
        for p in analysis.pairs:
            b = p.before.get(metric, 0)
            a = p.after.get(metric, 0)
            d = a - b
            b_std = p.before_std.get(metric, 0)
            a_std = p.after_std.get(metric, 0)
            sig = "YES" if abs(d) > max(b_std, a_std, 0.01) else "no"
            lines.append(f"| {p.pair_id} | {b:.3f} | {a:.3f} | {d:+.3f} | {sig} |")
        lines.append("")

    # --- Prompt analysis ---
    lines.append("## Prompt Changes\n")
    for p in analysis.pairs:
        lines.append(f"### {p.pair_id}\n")
        lines.append(f"- Original: {len(p.original_prompt)} chars")
        lines.append(f"- Optimized: {len(p.optimized_prompt)} chars")
        lines.append(f"- Delta: {p.prompt_char_delta:+d} chars ({p.prompt_char_pct:+.0f}%)")
        lines.append(f"- Diff: {_prompt_diff_summary(p.original_prompt, p.optimized_prompt)}")

        removed = _find_removed_content(p.original_prompt, p.optimized_prompt)
        if removed:
            lines.append(
                f"\n**Removed content with policy/tool keywords** ({len(removed)} lines):\n"
            )
            lines.extend(f"  - `{r[:120]}`" for r in removed[:10])
        lines.append("")

    # --- Degradation diagnosis ---
    degraded_pairs = [p for p in analysis.pairs if p.degraded_metrics]
    if degraded_pairs:
        lines.append("## Degradation Diagnosis\n")
        for p in degraded_pairs:
            lines.append(f"### {p.pair_id}\n")
            lines.append(
                f"Degraded metrics: {', '.join(METRIC_LABELS.get(m, m) for m in p.degraded_metrics)}\n"
            )
            for m in p.degraded_metrics:
                d = p.deltas[m]
                b = p.before.get(m, 0)
                pct = f" ({d / b * 100:+.0f}%)" if b > 0 else ""
                lines.append(f"- **{METRIC_LABELS.get(m, m)}**: {d:+.3f}{pct}")
            lines.append("")

    # --- Threshold analysis ---
    if analysis.thresholds:
        lines.append("## Threshold Alignment Check\n")
        lines.append("Checks whether GEPA thresholds are calibrated against baseline scores.\n")
        lines.append("| Metric | Threshold | Min Baseline | Gap | Status |")
        lines.append("|--------|-----------|-------------|-----|--------|")
        for metric, threshold in sorted(analysis.thresholds.items()):
            baselines = [p.before.get(metric, 0) for p in analysis.pairs if metric in p.before]
            if baselines:
                min_b = min(baselines)
                gap = min_b - threshold
                status = "OK" if gap > 0.05 else "TIGHT" if gap > 0 else "FAILING"
                lines.append(f"| {metric} | {threshold} | {min_b:.3f} | {gap:+.3f} | {status} |")
        lines.append("")

    # --- Per-case analysis (when available) ---
    has_per_case = any(p.before_per_case or p.after_per_case for p in analysis.pairs)
    if has_per_case:
        lines.append("## Per-Case Analysis\n")
        for p in analysis.pairs:
            if not (p.before_per_case and p.after_per_case):
                continue
            n_cases = min(len(p.before_per_case), len(p.after_per_case))
            degraded_cases = []
            for i in range(n_cases):
                bc = p.before_per_case[i]
                ac = p.after_per_case[i]
                common = set(bc) & set(ac)
                case_delta = sum(ac.get(m, 0) - bc.get(m, 0) for m in common) / max(len(common), 1)
                if case_delta < -0.05:
                    degraded_cases.append((i, case_delta, bc, ac))

            if degraded_cases:
                lines.append(f"### {p.pair_id} — {len(degraded_cases)}/{n_cases} cases degraded\n")
                lines.append("| Case | Avg Delta | Worst Metric | Metric Delta |")
                lines.append("|------|----------|-------------|-------------|")
                for idx, delta, bc, ac in sorted(degraded_cases, key=lambda x: x[1])[:15]:
                    worst_m = min(
                        (set(bc) & set(ac)),
                        key=lambda m: ac.get(m, 0) - bc.get(m, 0),
                        default="?",
                    )
                    worst_d = ac.get(worst_m, 0) - bc.get(worst_m, 0)
                    lines.append(f"| {idx} | {delta:+.3f} | {worst_m} | {worst_d:+.3f} |")
                lines.append("")
    else:
        lines.append("## Per-Case Analysis\n")
        lines.append(
            "*Per-case scores not available for this experiment. Future runs with updated eval"
        )
        lines.append("extraction will enable per-case degradation tracking.*\n")

    # --- MCP tool audit ---
    if run_stats:
        lines.extend(_format_tool_audit(analysis, run_stats))

    # --- Cost efficiency ---
    cost_rows = []
    for p in analysis.pairs:
        cost = blended_cost(p.model)
        if cost > 0:
            delta = p.avg_after - p.avg_before
            cost_rows.append((p.pair_id, p.model, cost, delta))
    if cost_rows:
        lines.append("## Cost Efficiency\n")
        lines.append("| Pair | Model | Blended $/M | Avg Delta | Cost per +0.01 |")
        lines.append("|------|-------|------------|----------|----------------|")
        for pid, model, cost, delta in cost_rows:
            if delta > 0.001:
                cost_per_unit = f"${cost / (delta * 100):.2f}"
            elif delta < -0.001:
                cost_per_unit = "regressed"
            else:
                cost_per_unit = "no change"
            lines.append(f"| {pid} | {model} | ${cost:.2f} | {delta:+.3f} | {cost_per_unit} |")
        lines.append("")

    # --- Recommendations ---
    lines.append("## Recommendations\n")
    rec_num = 1

    no_threshold_metrics = [
        m for m in all_metrics if m not in analysis.thresholds and m != "response_match_score"
    ]
    if no_threshold_metrics:
        lines.append(f"{rec_num}. **Add thresholds for**: {', '.join(no_threshold_metrics)}")
        lines.append(
            "   Metrics without thresholds default to 0.0 in GEPA — no optimization pressure.\n"
        )
        rec_num += 1

    heavy_compression = [p for p in analysis.pairs if p.prompt_char_pct < -30]
    if heavy_compression:
        ids = ", ".join(p.pair_id for p in heavy_compression)
        lines.append(f"{rec_num}. **Add prompt constraints** for: {ids}")
        lines.append("   GEPA compressed prompts >30%, likely removing load-bearing content.\n")
        rec_num += 1

    mismatched = set()
    for p in analysis.pairs:
        if p.before and p.after and set(p.before) != set(p.after):
            mismatched |= set(p.before).symmetric_difference(set(p.after))
    if mismatched:
        lines.append(
            f"{rec_num}. **Investigate metric name alignment** — before/after use different metric keys: {', '.join(sorted(mismatched))}"
        )
        lines.append(
            "   Mismatched names mean GEPA optimizes for different metrics than cloud reports.\n"
        )
        rec_num += 1

    if rec_num == 1:
        lines.append("No actionable recommendations — all metrics improved or held steady.\n")

    return "\n".join(lines)


def run_analysis(exp: Experiment) -> Path:
    """Run full analysis and save report. Returns path to the report file."""
    analysis = analyze_experiment(exp)
    run_stats = _extract_gepa_run_stats(exp)
    report = format_analysis_report(analysis, run_stats=run_stats)

    report_dir = exp.dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "analysis_report.md"
    report_path.write_text(report)

    print(f"  Analysis report saved to {report_path}")
    _print_summary(analysis)
    return report_path


def _print_summary(analysis: ExperimentAnalysis) -> None:
    """Print a concise terminal summary."""
    print(f"\n  {'=' * 60}")
    print(f"  Analysis: {analysis.experiment_name}")
    print(f"  {'=' * 60}")
    for p in analysis.pairs:
        delta = p.avg_after - p.avg_before
        icon = "+" if delta > 0.005 else "-" if delta < -0.005 else "="
        imp = len(p.improved_metrics)
        deg = len(p.degraded_metrics)
        print(f"  [{icon}] {p.pair_id:30s}  {delta:+.3f}  ({imp} up, {deg} down)")

        for m in p.degraded_metrics:
            d = p.deltas[m]
            label = METRIC_LABELS.get(m, m)
            print(f"       ↓ {label}: {d:+.3f}")
        for m in p.improved_metrics:
            d = p.deltas[m]
            label = METRIC_LABELS.get(m, m)
            print(f"       ↑ {label}: {d:+.3f}")
    print()
