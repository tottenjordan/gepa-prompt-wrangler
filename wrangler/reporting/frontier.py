"""Per-metric cost-quality Pareto frontiers over a set of model tiers.

**What this replaces.** `generate_cost_quality_chart` plotted
``np.mean(list(before_scores.values()))`` -- all five metrics collapsed into one scalar --
against ``blended_cost(model)``, which is list price at an assumed 4:1 input:output ratio,
with no uncertainty at all. Per-metric floors span 3.4x and campaign 09 measured metrics
moving in *opposite* directions (holdout +0.077 against quality -0.075), so the average hides
exactly the tradeoff the chart existed to show.

**Membership counts are the primary readout, not the picture.** Reporting which metrics an
arm is on the frontier for keeps the disagreement between metrics visible; a pooled score
throws it away and is how a tier gets recommended on a number nobody can decompose.

**NO COST HERE IS METERED.** `evaluator.py:_estimate_token_usage` counts ``len(text) // 4``
and every stage artifact carries ``is_estimate: True``; ``usage_metadata`` is read nowhere in
this repo, and the managed ``run_inference()`` call returns no usage columns. Pricing those
estimated tokens still beats `blended_cost`, because it reflects how verbosely a model
actually answers -- which is a real part of what separates tiers -- but the output must say
*estimated* everywhere it shows a dollar. See ``is_estimate`` on :class:`ArmPoint`.

**Resolution, not a confidence interval.** Domination requires the quality gap to exceed what
the design can resolve. That threshold comes from
:func:`wrangler.reporting.inference.mde_for_design`, which is a property of the *design*, not
an interval around the observed gap. The distinction is deliberate: idea 2 explicitly deferred
choosing an interval method for these metrics, because CLT and bootstrap are both miscalibrated
below a few hundred datapoints (Bowyer et al., ICML 2025) and our metrics range from 4 distinct
values (`safety_v1`) to 187 (`instruction_following_v1`). Using the MDE says the honest thing --
"this design cannot tell these two apart" -- without inventing the interval that question needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ArmPoint",
    "cost_for_arm",
    "dominates",
    "frontier_for_metric",
    "frontier_membership",
]

#: What the cost figure covers. Artificial Analysis notes published model costs routinely
#: exclude judge/grader inference, so ours states its convention rather than leaving it to be
#: assumed. Judge spend is real and is reported separately by `stage_economics`.
COST_CONVENTION = "agent inference only, both eval sides; excludes judge/grader inference"


@dataclass
class ArmPoint:
    """One arm's position in cost-quality space.

    ``cost_usd`` is **estimated** agent-inference spend, never metered and never inclusive of
    judge/grader inference -- Artificial Analysis notes published costs routinely omit the
    latter, so ours states the convention rather than leaving it to be assumed.

    ``priced`` is False when the model is not in the registry. Such an arm renders ``n/a``,
    never ``$0.00``: a zero with no explanation is how an unpriced model gets read as a free
    one, which is the same reasoning `measured_cost` already documents.
    """

    arm: str
    model: str
    cost_usd: float
    quality: dict[str, float] = field(default_factory=dict)
    coverage: float = 1.0
    is_estimate: bool = True
    priced: bool = True


def dominates(a: ArmPoint, b: ArmPoint, metric: str, resolution: float) -> bool:
    """True when ``a`` is both strictly cheaper than ``b`` and better beyond `resolution`.

    Both halves are strict for a reason. Equal cost is not cheaper, or two identical arms
    dominate each other and the frontier empties. And a quality gap inside `resolution` is not
    a quality difference this design can demonstrate, so the cheaper arm has not earned the
    frontier to itself -- both stay, and the tie is visible.

    An arm missing the metric never dominates and is never dominated on it: absence is not a
    score, and letting it win by default would put an unscored arm on a frontier.
    """
    if metric not in a.quality or metric not in b.quality:
        return False
    if not a.cost_usd < b.cost_usd:
        return False
    return (a.quality[metric] - b.quality[metric]) > resolution


def frontier_for_metric(points: list[ArmPoint], metric: str, resolution: float) -> list[str]:
    """Arm names on the cost-quality frontier for one metric, sorted.

    Sorted so a rendered table does not reorder between runs on dict iteration order -- the
    same rule the rest of this package follows for tie-breaking.
    """
    # Only arms that scored the metric can be on its frontier -- absence is not a score.
    # An empty result is returned rather than raised: a report spanning ten tiers must not
    # be lost because one metric went unscored everywhere.
    scored = [p for p in points if metric in p.quality]
    return sorted(
        p.arm
        for p in scored
        if not any(other is not p and dominates(other, p, metric, resolution) for other in scored)
    )


def frontier_membership(points: list[ArmPoint], resolutions: dict[str, float]) -> dict[str, int]:
    """Per arm, the number of metrics it is on the frontier for.

    **This is the headline, not the chart.** HAL (arXiv 2025-10-13, 21,730 rollouts) reports
    frontier membership per benchmark rather than a pooled score, having measured that
    cost-accuracy frontiers are dominated by cheap tiers rather than flagship models. The same
    reasoning applies to metrics within one benchmark here.

    A metric with no entry in `resolutions` is **skipped, not assumed resolvable**. Defaulting
    to zero would let noise decide that metric's frontier, which is the failure this module
    exists to avoid.
    """
    counts = dict.fromkeys((p.arm for p in points), 0)
    for metric, resolution in resolutions.items():
        for arm in frontier_for_metric(points, metric, resolution):
            counts[arm] += 1
    return counts


def cost_for_arm(
    before: dict,
    after: dict,
    model: str,
    custom_costs: dict[str, float] | None = None,
) -> dict:
    """Estimated dollars for one arm, summed over both eval sides.

    Prices the token counts the artifacts carry. Those counts are `len(text) // 4`, so the
    result is an estimate of an estimate -- but it is still strictly better than
    `blended_cost`, which assumes a fixed 4:1 input:output ratio and therefore cannot see that
    a cheap-per-token model answering verbosely costs more per run than a terse expensive one.
    That difference is a real part of what separates tiers, and it is the thing a tier
    comparison is for.

    `is_estimate` defaults to **True** when a side does not record the flag. An unknown
    provenance must not be promoted to a metered one; the only safe default is the one that
    understates our confidence.

    A missing side contributes zero rather than raising, so an eval-only arm or one whose
    stage failed still prices what it did spend.
    """
    from ..core.models import measured_cost

    total_in = total_out = 0
    is_estimate = False
    saw_usage = False
    for side in (before, after):
        usage = (side or {}).get("token_usage") or {}
        if usage:
            saw_usage = True
            total_in += int(usage.get("input_tokens") or 0)
            total_out += int(usage.get("output_tokens") or 0)
            # Absent flag means unknown, and unknown is reported as estimated.
            is_estimate = is_estimate or bool(usage.get("is_estimate", True))

    priced_parts = measured_cost(model, total_in, total_out, custom_costs)
    return {
        "cost_usd": priced_parts["total_usd"],
        "input_tokens": total_in,
        "output_tokens": total_out,
        "priced": priced_parts["priced"],
        # No usage recorded at all is still not a metered zero.
        "is_estimate": is_estimate or not saw_usage,
        "convention": COST_CONVENTION,
    }


#: Metrics whose numbers are uninterpretable for runs predating the silent-failure-12 fix
#: (2026-09-18). Tool use is the one affected: 12-24% of cases in campaigns 07 and 08 were
#: scored against an agent that had been handed zero tools. CLAUDE.md records that those runs
#: are NOT retrospectively cleaned.
PRE_FIX_SUSPECT_METRICS = ("tool_use_quality_v1",)


def summarize_frontier(
    arms: dict[str, tuple[dict, dict]],
    models: dict[str, str],
    *,
    variance_source=None,
    pre_fix_arms: frozenset[str] | set[str] = frozenset(),
    custom_costs: dict[str, dict[str, float]] | None = None,
) -> dict:
    """Assemble a cross-run tier comparison from stage artifacts.

    Takes `campaign_floor.fetch_arms`' exact output shape, so the GCS reader is reused rather
    than reimplemented -- and, as there, every piece of arithmetic here stays testable without
    a bucket.

    `models` is a separate mapping because the eval artifacts do not carry a model id; it
    lives in the deploy stage artifact. Pricing the wrong id moves the answer by more than
    25% (`TestCostAgreesWithTheArtifact`), so it is read, never inferred.
    """
    from .campaign_floor import COVERAGE_GAP_LIMIT
    from .inference import campaign_09_variance, mde_for_design

    custom_costs = custom_costs or {}
    source = variance_source or campaign_09_variance()

    points: dict[str, list[ArmPoint]] = {"before": [], "after": []}
    excluded: list[str] = []
    warnings: list[str] = []
    n_cases = 0

    for arm in sorted(arms):
        before, after = arms[arm]
        cov_b, cov_a = before.get("coverage"), after.get("coverage")
        if cov_b is not None and cov_a is not None and abs(cov_b - cov_a) > COVERAGE_GAP_LIMIT:
            # Same rule as the noise floor: a delta across a coverage gap this wide measures
            # dropout, so the arm is dropped with its reason rather than quietly plotted.
            excluded.append(
                f"{arm}: coverage gap {abs(cov_b - cov_a):.0%} exceeds "
                f"{COVERAGE_GAP_LIMIT:.0%} — its scores describe dropout, not quality"
            )
            continue

        cost = cost_for_arm(before, after, models.get(arm, ""), custom_costs.get(arm))
        n_cases = max(n_cases, int(before.get("cases_total") or 0))
        for phase, side in (("before", before), ("after", after)):
            points[phase].append(
                ArmPoint(
                    arm=arm,
                    model=models.get(arm, "unknown"),
                    cost_usd=cost["cost_usd"],
                    quality=dict(side.get("scores") or {}),
                    coverage=side.get("coverage") or 0.0,
                    is_estimate=cost["is_estimate"],
                    priced=cost["priced"],
                )
            )

        if arm in pre_fix_arms:
            warnings.extend(
                f"{arm}: {metric} predates the 2026-09-18 silent-failure-12 fix — "
                f"12-24% of cases scored a toolless agent; not retrospectively cleaned"
                for metric in PRE_FIX_SUSPECT_METRICS
                if metric in (after.get("scores") or {})
            )

    design = mde_for_design(
        n_cases=n_cases or source.n_cases,
        num_runs=int(arms[next(iter(arms))][0].get("num_runs") or 1) if arms else 1,
        variance_source=source,
    )
    resolutions = dict(design.per_metric)

    return {
        "points_before": points["before"],
        "points_after": points["after"],
        "resolutions": resolutions,
        "frontier_before": {
            m: frontier_for_metric(points["before"], m, r) for m, r in resolutions.items()
        },
        "frontier_after": {
            m: frontier_for_metric(points["after"], m, r) for m, r in resolutions.items()
        },
        "membership": frontier_membership(points["after"], resolutions),
        "excluded": excluded,
        "warnings": warnings,
        "cost_note": (
            f"Costs are ESTIMATED, not metered: token counts are len(text)//4 and no "
            f"usage_metadata is available. Convention: {COST_CONVENTION}."
        ),
        "resolution_note": (
            f"An arm dominates another only if it is cheaper AND better by more than the "
            f"design's minimum detectable effect ({design.n_cases} cases, "
            f"num_runs={design.num_runs}, variance from {source.label}). Arms closer than "
            f"that both stay on the frontier."
        ),
        "caveats": list(source.caveats),
    }
