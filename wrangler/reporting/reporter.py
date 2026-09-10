"""Experiment report generation — charts + markdown for prompt optimization experiments."""

import math
import re
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

from ..core.config import MODEL_COSTS
from ..core.models import AGENT_ORDER, PROVIDERS, blended_cost_for_report, measured_cost
from ..core.models import blended_cost_for_report as blended_cost
from .analysis import (
    METRIC_LABELS,
    generate_all_charts,
    normalize_agent_keys,
)
from .analyzer import classify_deltas, measure_noise_floor_per_metric

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


def _is_control_arm(data: dict) -> bool:
    """True when this arm's prompt did not change between the two evals.

    A control arm exists to measure noise: same prompt, evaluated twice, so
    whatever it moves by is the floor. Detected from the artifacts rather than
    a flag, because a `skip_optimize` run never writes an optimize stage and so
    has no flag to read -- `optimized_prompt` is simply absent or identical to
    the original.
    """
    optimized = (data.get("optimized_prompt") or "").strip()
    original = (data.get("original_prompt") or "").strip()
    return not optimized or optimized == original


def _arm_delta(data: dict) -> float:
    before, after = data.get("before", {}), data.get("after", {})
    avg_b = sum(before.values()) / len(before) if before else 0.0
    avg_a = sum(after.values()) / len(after) if after else 0.0
    return avg_a - avg_b


def _metric_deltas(data: dict) -> dict[str, float]:
    """Per-metric after-minus-before, for metrics scored on *both* sides.

    A metric present on only one side is skipped rather than treated as a move
    from zero -- that would manufacture a delta the size of the whole score.
    """
    before = data.get("before") or {}
    after = data.get("after") or {}
    return {m: after[m] - before[m] for m in before if m in after}


class _ArmView:
    """Adapts a report `results[name]` dict to what analyzer expects.

    `measure_noise_floor_per_metric` and `classify_deltas` read `.deltas` and
    `.is_control`; the reporter holds plain dicts. This is the bridge, and it
    is why those two functions sat with no caller for so long.
    """

    __slots__ = ("deltas", "is_control", "name")

    def __init__(self, name: str, data: dict):
        self.name = name
        self.deltas = _metric_deltas(data)
        self.is_control = _is_control_arm(data)


def _per_metric_verdict_lines(results: dict, ordered: list) -> list[str]:
    """Judge every metric against *its own* control floor, not one scalar.

    The executive summary above collapses each arm to a single average delta
    and one floor. Campaign 06 measured a 3.4x spread between the tightest
    metric floor and the loosest, so that collapse throws away most of the
    resolution the control arms were run to buy.
    """
    arms = [_ArmView(n, results[n]) for n in ordered]
    floors = measure_noise_floor_per_metric(arms)
    if floors:
        # Drop metrics whose measured floor is exactly zero. A control that did
        # not move on a metric has not measured its noise -- it means the sample
        # was too small to see any. Keeping the 0.0 would judge every later
        # movement against it and call all of them real, which is the failure
        # classify_deltas already refuses for *absent* metrics. Absent and
        # measured-as-zero deserve the same answer: uncalibrated.
        floors = {m: f for m, f in floors.items() if f > 0.0}
        floors = floors or None
    real = [a for a in arms if not a.is_control]
    if not real:
        return []

    lines = ["", "### Per-metric verdicts", ""]
    if floors is None:
        lines.append(
            "No control arm, so every metric below is **uncalibrated** — a movement "
            "cannot be told from noise without something measuring the noise."
        )
    else:
        lines.append(
            f"Each metric is judged against its own floor, from "
            f"{len(arms) - len(real)} control arm(s). A single scalar floor would "
            f"hold every metric to the loosest one."
        )
    lines.append("| arm | metric | Δ | floor | verdict |")
    lines.append("| --- | --- | --- | --- | --- |")
    for arm in real:
        for metric, verdict in classify_deltas(arm, floors).items():
            bar = (floors or {}).get(metric)
            bar_txt = f"{bar:.4f}" if bar is not None else "—"
            lines.append(
                f"| {arm.name} | {metric} | {arm.deltas[metric]:+.4f} | {bar_txt} | {verdict} |"
            )
    lines.append("")
    return lines


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

    # Which arms never had their prompt changed? Those are controls, and their
    # movement IS the noise floor -- not a result. Reporting a control's drift
    # as an improvement is the failure CLAUDE.md's control-arm rule exists to
    # prevent: on 2026-08-22 a byte-identical arm drifted +0.039 and nearly got
    # promoted as a clean win.
    controls = [n for n in ordered if _is_control_arm(results[n])]
    real = [n for n in ordered if n not in controls]
    floor = max((abs(agent_deltas[n]) for n in controls), default=None)

    if not real:
        # Nothing was optimized. Say what this run actually is.
        lines.append(
            f"**Control run — no optimization.** {len(controls)} arm(s) evaluated the same "
            f"prompt twice, so every movement below is measurement noise, not a result.\n"
        )
        lines.extend(f"- **{n}**: {agent_deltas[n]:+.3f} avg (noise)" for n in controls)
        if floor is not None:
            lines.append(
                f"\n**Observed noise floor: {floor:.3f}** — the bar a real delta must clear "
                f"before it means anything.\n"
            )
        return lines

    # A real sweep. Judge it against the control's floor rather than a
    # hardcoded epsilon: the old 0.005 threshold is ~13x smaller than the
    # floor measured on this pipeline, so it labelled pure noise "improved".
    bar = floor if floor is not None else 0.005
    improved = [n for n in real if agent_deltas[n] > bar]
    regressed = [n for n in real if agent_deltas[n] < -bar]
    stable = [n for n in real if abs(agent_deltas[n]) <= bar]

    best = max(real, key=lambda n: agent_deltas[n])
    worst = min(real, key=lambda n: agent_deltas[n])

    if floor is None:
        lines.append(
            f"**{len(improved)}/{len(real)} models improved** after GEPA optimization — but "
            f"this sweep carried **no control arm**, so these deltas are *uncalibrated* and "
            f"no result here can be distinguished from noise.\n"
        )
    else:
        lines.append(
            f"**{len(improved)}/{len(real)} models improved** after GEPA optimization, "
            f"judged against a measured noise floor of **{floor:.3f}** from "
            f"{len(controls)} control arm(s). "
        )
    if improved:
        lines.append(f"Best performer: **{best}** ({agent_deltas[best]:+.3f} avg). ")
    if regressed:
        lines.append(f"Largest regression: **{worst}** ({agent_deltas[worst]:+.3f} avg). ")
    if stable and floor is not None:
        lines.append(f"{len(stable)} arm(s) moved less than the floor and are within the noise. ")
    lines.append("")

    if improved:
        lines.append(
            f"- **Improved:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in improved)}"
        )
    if stable:
        lines.append(f"- **Stable:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in stable)}")
    if regressed:
        lines.append(
            f"- **Regressed:** {', '.join(f'{n} ({agent_deltas[n]:+.3f})' for n in regressed)}"
        )
    lines.append("")

    # The arm-level verdicts above use one average and one scalar floor. This
    # adds the per-metric view, which is where the resolution actually is.
    lines.extend(_per_metric_verdict_lines(results, ordered))

    # Averaged over `real`, not `ordered`. Folding controls in shifts the
    # figure toward their drift: a control moving +0.15 on one metric while a
    # flat optimized arm moves 0.000 reports as a +0.075 "gain" belonging to a
    # prompt nobody changed.
    metric_avg_deltas = {}
    for metric in METRIC_LABELS:
        vals = []
        for name in real:
            b = results[name].get("before", {}).get(metric, 0)
            a = results[name].get("after", {}).get(metric, 0)
            vals.append(a - b)
        metric_avg_deltas[metric] = sum(vals) / len(vals) if vals else 0

    best_metric = max(metric_avg_deltas, key=metric_avg_deltas.__getitem__)
    worst_metric = min(metric_avg_deltas, key=metric_avg_deltas.__getitem__)
    if metric_avg_deltas[best_metric] > bar:
        lines.append(
            f"**Strongest metric gain:** {METRIC_LABELS[best_metric]} ({metric_avg_deltas[best_metric]:+.3f} avg across models)"
        )
    if metric_avg_deltas[worst_metric] < -bar:
        lines.append(
            f"**Largest metric decline:** {METRIC_LABELS[worst_metric]} ({metric_avg_deltas[worst_metric]:+.3f} avg across models)"
        )
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
        lines.append(
            f"| {name} | `{model}` | {provider} | ${cost['input']:.2f} | ${cost['output']:.2f} | ${blend:.2f} |"
        )
    lines.append("")

    lines.append("**Metrics evaluated:**\n")
    for key, label in METRIC_LABELS.items():
        lines.append(f"- {label} (`{key}`)")
    lines.append("")

    return lines


def _scores_section(results: dict, ordered: list[str]) -> list[str]:
    """Combined before/after/delta table per model."""
    lines = []
    has_after = any(results[n].get("after") for n in ordered)

    lines.append("## Evaluation Results\n")

    for name in ordered:
        model = results[name].get("model", "")
        before = results[name].get("before", {})
        after = results[name].get("after", {})

        lines.append(f"### {name.title()} (`{model}`)\n")

        if has_after and after:
            lines.append("| Metric | Before | After | Delta | Change |")
            lines.append("|--------|--------|-------|-------|--------|")
            for key, label in METRIC_LABELS.items():
                b = before.get(key, 0)
                a = after.get(key, 0)
                d = a - b
                pct = f"{d / b * 100:+.1f}%" if b > 0 else "N/A"
                lines.append(f"| {label} | {b:.2f} | {a:.2f} | {d:+.2f} | {pct} |")
            avg_b = sum(before.values()) / max(len(before), 1) if before else 0
            avg_a = sum(after.values()) / max(len(after), 1) if after else 0
            avg_d = avg_a - avg_b
            avg_pct = f"{avg_d / avg_b * 100:+.1f}%" if avg_b > 0 else "N/A"
            lines.append(
                f"| **Average** | **{avg_b:.2f}** | **{avg_a:.2f}** | **{avg_d:+.2f}** | **{avg_pct}** |"
            )
        else:
            lines.append("| Metric | Score |")
            lines.append("|--------|-------|")
            for key, label in METRIC_LABELS.items():
                s = before.get(key, 0)
                lines.append(f"| {label} | {s:.2f} |")
            avg = sum(before.values()) / max(len(before), 1) if before else 0
            lines.append(f"| **Average** | **{avg:.2f}** |")

        lines.append("")

    return lines


def _threshold_section(results: dict, ordered: list[str]) -> list[str]:
    """GEPA threshold provenance — the sampler_config.json thresholds GEPA
    optimized against, vs before/after scores with pass/fail status."""
    lines = []
    if not any(results[n].get("thresholds") for n in ordered):
        return lines

    lines.append("## GEPA Threshold Alignment\n")
    lines.append(
        "Thresholds GEPA optimized against, sourced from each agent's "
        "`sampler_config.json` (the single source of truth). A metric below its "
        "threshold gives GEPA a gradient to improve; one already above has no pressure.\n"
    )

    for name in ordered:
        thr = results[name].get("thresholds") or {}
        if not thr:
            continue
        before = results[name].get("before", {})
        after = results[name].get("after", {})
        has_after = bool(after)
        model = results[name].get("model", "")
        lines.append(f"### {name.title()} (`{model}`)\n")
        if has_after:
            lines.append("| Metric | Threshold | Before | After | Status |")
            lines.append("|--------|-----------|--------|-------|--------|")
        else:
            lines.append("| Metric | Threshold | Baseline | Status |")
            lines.append("|--------|-----------|----------|--------|")
        for key, t in sorted(thr.items()):
            label = METRIC_LABELS.get(key, key)
            b = before.get(key)
            a = after.get(key)
            if has_after:
                ref = a if a is not None else b
                status = "PASS" if (ref is not None and ref >= t) else "BELOW"
                bcell = f"{b:.2f}" if b is not None else "—"
                acell = f"{a:.2f}" if a is not None else "—"
                lines.append(f"| {label} | {t:.2f} | {bcell} | {acell} | {status} |")
            else:
                status = "PASS" if (b is not None and b >= t) else "BELOW"
                bcell = f"{b:.2f}" if b is not None else "—"
                lines.append(f"| {label} | {t:.2f} | {bcell} | {status} |")
        lines.append("")

    return lines


def _significance_section(results: dict, ordered: list[str]) -> list[str]:
    """Flag which metric deltas are statistically significant."""
    lines = []
    has_after = any(results[n].get("after") for n in ordered)
    has_std = any(results[n].get("before_std") or results[n].get("after_std") for n in ordered)
    if not has_after or not has_std:
        return lines

    lines.append("## Statistical Significance\n")
    lines.append(
        "Pooled standard error: `se = sqrt(std_before² + std_after²) / sqrt(n)`. "
        "Significant if `|delta| > 2 × se` (approx. p < 0.05).\n"
    )

    header = "| Metric |" + " | ".join(n.title() for n in ordered) + " |"
    sep = "|--------|" + " | ".join("------" for _ in ordered) + " |"
    lines.append(header)
    lines.append(sep)

    sig_count = 0
    total_count = 0
    for key, label in METRIC_LABELS.items():
        row = f"| {label} |"
        for name in ordered:
            b = results[name].get("before", {}).get(key, 0)
            a = results[name].get("after", {}).get(key, 0)
            d = a - b
            b_std = results[name].get("before_std", {}).get(key, 0)
            a_std = results[name].get("after_std", {}).get(key, 0)
            num_runs = results[name].get("num_runs", 3)
            if num_runs < 2:
                num_runs = 3
            se = math.sqrt(b_std**2 + a_std**2) / math.sqrt(num_runs)
            total_count += 1
            if se > 0 and abs(d) > 2 * se:
                sig_count += 1
                row += f" {d:+.02f} ★ |"
            else:
                row += f" {d:+.02f} |"
        lines.append(row)
    lines.append("")
    lines.append(
        f"*★ = statistically significant. {sig_count}/{total_count} metric-model combinations showed significant change.*\n"
    )
    return lines


def _per_case_winners_losers(
    results: dict, ordered: list[str], case_metadata: list[dict] | None
) -> list[str]:
    """Show top improved and regressed test cases per model."""
    lines = []
    has_per_case = any(
        results[n].get("before_per_case") and results[n].get("after_per_case") for n in ordered
    )
    if not has_per_case:
        return lines

    lines.append("## Per-Case Winners & Losers\n")

    for name in ordered:
        before_cases = results[name].get("before_per_case", [])
        after_cases = results[name].get("after_per_case", [])
        if not before_cases or not after_cases:
            continue

        n_cases = min(len(before_cases), len(after_cases))
        case_deltas = []
        for i in range(n_cases):
            bc = before_cases[i]
            ac = after_cases[i]
            common = set(bc) & set(ac)
            if not common:
                continue
            avg_delta = sum(ac.get(m, 0) - bc.get(m, 0) for m in common) / len(common)
            best_m = max(common, key=lambda m: ac.get(m, 0) - bc.get(m, 0))
            worst_m = min(common, key=lambda m: ac.get(m, 0) - bc.get(m, 0))
            case_deltas.append((i, avg_delta, best_m, worst_m))

        if not case_deltas:
            continue

        case_deltas.sort(key=lambda x: x[1])
        top_improved = list(reversed(case_deltas[-3:]))
        top_regressed = [c for c in case_deltas[:3] if c[1] < -0.005]

        if not top_improved and not top_regressed:
            continue

        lines.append(f"### {name.title()}\n")

        if top_improved:
            lines.append("**Top Improved:**\n")
            lines.append("| Case | Category | Avg Delta | Best Metric | Worst Metric |")
            lines.append("|------|----------|----------|-------------|-------------|")
            for idx, delta, best_m, worst_m in top_improved:
                label = _case_label(idx, case_metadata)
                best_l = METRIC_LABELS.get(best_m, best_m)
                worst_l = METRIC_LABELS.get(worst_m, worst_m)
                lines.append(
                    f"| {label} | {_case_category(idx, case_metadata)} | {delta:+.3f} | {best_l} | {worst_l} |"
                )
            lines.append("")

        if top_regressed:
            lines.append("**Top Regressed:**\n")
            lines.append("| Case | Category | Avg Delta | Best Metric | Worst Metric |")
            lines.append("|------|----------|----------|-------------|-------------|")
            for idx, delta, best_m, worst_m in top_regressed:
                label = _case_label(idx, case_metadata)
                best_l = METRIC_LABELS.get(best_m, best_m)
                worst_l = METRIC_LABELS.get(worst_m, worst_m)
                lines.append(
                    f"| {label} | {_case_category(idx, case_metadata)} | {delta:+.3f} | {best_l} | {worst_l} |"
                )
            lines.append("")

    return lines


def _case_label(idx: int, case_metadata: list[dict] | None) -> str:
    if case_metadata and idx < len(case_metadata):
        prompt = case_metadata[idx].get("prompt", "")
        if prompt:
            return f"#{idx}: {prompt[:50]}..."
    return f"Case {idx}"


def _case_category(idx: int, case_metadata: list[dict] | None) -> str:
    if case_metadata and idx < len(case_metadata):
        cat = case_metadata[idx].get("category", "")
        if cat:
            return cat
    return "—"


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

        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        delta = avg_a - avg_b

        improved = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) > 0.01]
        regressed_m = [k for k in METRIC_LABELS if after.get(k, 0) - before.get(k, 0) < -0.01]

        verdict = "improved" if delta > 0.005 else ("regressed" if delta < -0.005 else "stable")

        lines.append(
            f"### {name.title()} (`{model}`, ${cost['input']:.2f}/${cost['output']:.2f} in/out per M)\n"
        )
        lines.append(f"**Overall:** {avg_b:.2f} → {avg_a:.2f} ({delta:+.3f}, {verdict})\n")

        if improved:
            lines.append(f"- **Gained:** {', '.join(METRIC_LABELS[k] for k in improved)}")
        if regressed_m:
            lines.append(f"- **Lost:** {', '.join(METRIC_LABELS[k] for k in regressed_m)}")

        opt_prompt = results[name].get("optimized_prompt", "")
        orig_prompt = results[name].get("original_prompt", "")
        if opt_prompt:
            lines.append(
                f"- **Prompt expansion:** {len(orig_prompt)} → {len(opt_prompt)} chars "
                f"({len(opt_prompt) / max(len(orig_prompt), 1):.0f}x)"
            )
        lines.append("")

    return lines


def _cost_benefit_section(results: dict, ordered: list[str]) -> list[str]:
    """Cost-benefit analysis with quality/$ ranking."""
    lines = []
    lines.append("## Cost-Benefit Analysis\n")
    # Two cost bases, deliberately side by side. Blended $/M is the price list
    # against an assumed 4:1 ratio -- arithmetic you can do without running
    # anything. Spend $ is what this run actually cost, from the tokens it used.
    # They answer different questions and reporting only one hides the other:
    # a cheap-per-token model that answers verbosely can outspend a dear terse
    # one, which the list-price column cannot show.
    lines.append(
        "| Agent | Model | Blended $/M | Spend $ | Before | After | Delta | Quality/$ | $/quality pt |"
    )
    lines.append(
        "|-------|-------|-------------|---------|--------|-------|-------|-----------|--------------|"
    )

    for name in ordered:
        model = results[name].get("model", "unknown")
        blend = blended_cost_for_report(model)
        before = results[name].get("before", {})
        after = results[name].get("after", before)
        avg_b = sum(before.values()) / max(len(before), 1) if before else 0
        avg_a = sum(after.values()) / max(len(after), 1) if after else 0
        delta = avg_a - avg_b
        qpd = avg_a / max(blend, 0.01)

        usage = results[name].get("token_usage") or {}
        if usage:
            spent = measured_cost(
                model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                custom_costs=results[name].get("costs_per_million"),
            )
            spend_col = f"${spent['total_usd']:.4f}" if spent["priced"] else "unpriced"
            per_pt = (
                f"${spent['total_usd'] / avg_a:.4f}" if spent["priced"] and avg_a > 0 else "n/a"
            )
        else:
            # No token data recorded. Printing $0.00 would read as "this run
            # was free" rather than "we did not measure it".
            spend_col, per_pt = "n/a", "n/a"

        lines.append(
            f"| {name.title()} | `{model}` | ${blend:.2f} | {spend_col} | "
            f"{avg_b:.2f} | {avg_a:.2f} | {delta:+.02f} | {qpd:.3f} | {per_pt} |"
        )

    lines.append("")
    lines.append(
        "*Blended $/M = list price at an assumed 4:1 input:output ratio. "
        "Spend $ = this run's actual cost from measured token usage. "
        "Quality/$ uses list price; $/quality pt uses measured spend — a model can "
        "look cheap on one and dear on the other, which is the point of showing both. "
        "`n/a` means no token usage was recorded, not that the run was free.*\n"
    )
    return lines


def _charts_section() -> list[str]:
    """Embed chart images with captions."""
    lines = []
    lines.append("## Visualizations\n")

    chart_info = [
        (
            "radar.png",
            "Metric Profiles",
            "Radar overlay showing each model's strength/weakness pattern across all metrics.",
        ),
        (
            "comparison.png",
            "Baseline Comparison",
            "Grouped bar chart of pre-optimization scores across all agents.",
        ),
        (
            "improvement_delta.png",
            "Optimization Impact",
            "Per-metric score change from GEPA optimization. Bars above zero = improved.",
        ),
        (
            "cost_quality.png",
            "Cost-Quality Tradeoff",
            "Model cost vs average quality. Arrows show before→after movement.",
        ),
        (
            "tier_breakdown.png",
            "Tier Performance",
            "Average scores by complexity tier (low/medium/high).",
        ),
        (
            "category_heatmap.png",
            "Category Capability",
            "Heatmap of per-category scores across models.",
        ),
        (
            "tier_improvement_heatmap.png",
            "Tier Improvement",
            "Optimization impact by complexity tier. Green=improved, red=regressed.",
        ),
        (
            "run_comparison.png",
            "Run Comparison",
            "Side-by-side comparison with previous experiment run.",
        ),
    ]

    for filename, title, caption in chart_info:
        if (CHARTS_DIR / filename).exists():
            lines.append(f"### {title}\n")
            lines.append(f"![{title}](charts/{filename})\n")
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
        lines.append(
            "GEPA optimization showed widespread regression — review sampler config thresholds and eval criteria alignment. "
        )
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
        worst = min(metric_deltas, key=metric_deltas.__getitem__)
        lines.append(
            f"1. **Investigate {METRIC_LABELS[worst]} regression** ({metric_deltas[worst]:+.3f} avg in regressed models) — "
            f"consider adding as explicit optimization target in sampler config"
        )

    lines.append(
        "2. **Re-run with tighter thresholds** — higher thresholds force GEPA to discover domain-specific content"
    )
    lines.append(
        "3. **Verify per-case scores** are being extracted correctly for tier/category analysis"
    )
    lines.append(
        "4. **Monitor deployed agents** with online evaluators to catch drift on real traffic"
    )
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

    # Ordered by descending decision-value, not by how the pipeline computed it.
    #
    # It used to run: summary, methodology, seven charts, scores, ..., conclusions.
    # So the verdicts -- the only part most readers need -- sat below roughly 200 lines
    # of setup, and the charts landed before the numbers they illustrate. Anyone
    # skimming for "did it work" met the experimental design first.
    #
    # Now each layer answers one question and every layer below it is evidence for the
    # one above. Stop reading at any point and you have a true, if coarser, picture.
    lines = []
    lines.append(f"# GEPA Prompt Wrangler — {experiment_name}\n")

    lines.extend(_executive_summary(normalized, ordered))
    lines.extend(_conclusions_section(normalized, ordered))

    lines.append("---\n")
    lines.append("## The numbers\n")
    lines.append("*Every score behind the findings above.*\n")
    lines.extend(_scores_section(normalized, ordered))

    lines.append("---\n")
    lines.append("## Is it real?\n")
    lines.append("*Thresholds, coverage and whether any delta clears the noise.*\n")
    lines.extend(_threshold_section(normalized, ordered))
    lines.extend(_significance_section(normalized, ordered))

    lines.append("---\n")
    lines.append("## Where it came from\n")
    lines.append("*Which cases and which models moved, and by how much.*\n")
    lines.extend(_per_case_winners_losers(normalized, ordered, case_metadata))
    lines.extend(_per_model_section(normalized, ordered))

    lines.append("---\n")
    lines.append("## What it cost\n")
    lines.extend(_cost_benefit_section(normalized, ordered))

    lines.append("---\n")
    lines.append("## Charts\n")
    lines.extend(_charts_section())

    lines.append("---\n")
    lines.append("## Methodology\n")
    lines.append("*How the run was configured. Read this to attack the result.*\n")
    lines.extend(_methodology_section(normalized, ordered, experiment_name))

    lines.append("---\n")
    lines.append("## Appendix — optimized prompts\n")
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

    # A self-contained sibling, because the markdown's relative chart paths resolve
    # nowhere except a full local copy of this directory -- and every real reader of
    # this report meets it in GCS. Never fatal: the markdown and summary.json are the
    # artifacts, this is a convenience rendering of them.
    try:
        from .html_report import write_self_contained_html

        html_path = write_self_contained_html(
            report_path, title=f"GEPA Prompt Wrangler — {experiment_name}"
        )
        print(f"Self-contained HTML saved to: {html_path}")
    except Exception as exc:  # pragma: no cover - exercised via the failure test
        print(f"HTML rendering skipped ({type(exc).__name__}: {exc}); markdown is unaffected")
