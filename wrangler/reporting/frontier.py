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


def crosses_tier(
    arms: dict[str, tuple[dict, dict]],
    cheap: str,
    expensive: str,
    metric: str,
    resolution: float,
) -> dict:
    """Does optimizing the cheap tier's prompt reach what the expensive tier gives untuned?

    The bar is the expensive arm's **before** score, deliberately. The practical question a
    tier choice turns on is not "can a tuned cheap model beat a tuned expensive one" -- that
    needs both budgets -- but "does prompt work on the cheap tier buy what the expensive tier
    gives you off the shelf". That is the question this harness can already answer and nothing
    asked.

    Returns ``crossed=None`` when either arm is absent, rather than raising: a frontier report
    over ten tiers must not die because one pair was not run.
    """
    if cheap not in arms or expensive not in arms:
        return {
            "crossed": None,
            "verdict": f"not comparable: {cheap!r} or {expensive!r} has no artifacts",
            "metric": metric,
        }

    reached = (arms[cheap][1].get("scores") or {}).get(metric)
    baseline = (arms[expensive][0].get("scores") or {}).get(metric)
    if reached is None or baseline is None:
        return {
            "crossed": None,
            "verdict": f"not comparable: {metric} missing on one side",
            "metric": metric,
        }

    gap = reached - baseline
    crossed = gap > resolution
    if crossed:
        verdict = (
            f"{cheap} optimized reaches {reached:.4f}, past {expensive} untuned "
            f"({baseline:.4f}) by {gap:+.4f}, beyond the {resolution:.4f} resolution"
        )
    elif gap < -resolution:
        # A loss larger than the resolution is a result, not a tie. Folding it into
        # "too close to call" would flatter the cheap tier on exactly the metric where
        # it lost -- and on campaign 07 that metric is the holdout.
        verdict = (
            f"{cheap} optimized reaches {reached:.4f}, BEHIND {expensive} untuned "
            f"({baseline:.4f}) by {gap:+.4f}, beyond the {resolution:.4f} resolution"
        )
    else:
        verdict = (
            f"{cheap} optimized reaches {reached:.4f} against {expensive} untuned "
            f"({baseline:.4f}); the {gap:+.4f} gap is inside the {resolution:.4f} "
            f"resolution, so this design cannot call it either way"
        )
    return {
        "crossed": crossed,
        "gap": gap,
        "reached": reached,
        "baseline": baseline,
        "resolution": resolution,
        "metric": metric,
        "verdict": verdict,
    }


def complementarity(
    arms: dict[str, tuple[dict, dict]],
    a: str,
    b: str,
    metric: str,
    phase: int = 1,
) -> dict:
    """Per case, how often each arm beats the other on one metric.

    A leaderboard says which model is better on average. This says whether the worse one is
    better *somewhere* -- FrugalGPT measured 13% of COQA items that GPT-4 got wrong and GPT-3
    got right, which is the argument for routing rather than picking. A near-zero number here
    means the cheap tier is strictly worse and the frontier is the whole story; a large one
    means the average is hiding a real split.

    Compared over cases **both** arms scored. Unmatched cases are exactly the dropout
    silent-failures #5 showed reads as a prompt effect, and including them would let coverage
    differences masquerade as complementarity.

    **THIS CONFLATES REAL DISAGREEMENT WITH JUDGE NOISE, and on one metric it is almost all
    noise.** A per-case win is only evidence about the models if the judge would score the
    same pair the same way twice. DOE 02 measured per-case judge self-disagreement on
    byte-identical responses at **64/64 for `instruction_following_v1`** and **0/64 for
    `safety_v1`**. Measured here on campaign 09's two treatment arms: 99% of cases have a
    "winner" on `instruction_following_v1` against 21% on `safety_v1` -- the first number is
    the judge, not the models. Read this metric by metric against those disagreement rates,
    never pooled.
    """
    from .inference import eval_side_from_payload

    if a not in arms or b not in arms:
        return {"n_common": 0, "a_only": 0, "b_only": 0, "a_only_frac": 0.0, "b_only_frac": 0.0}

    side_a = eval_side_from_payload(a, "x", arms[a][phase])
    side_b = eval_side_from_payload(b, "x", arms[b][phase])
    common = sorted(set(side_a.per_case) & set(side_b.per_case))

    a_only = b_only = 0
    for case in common:
        va = side_a.per_case[case].get(metric)
        vb = side_b.per_case[case].get(metric)
        if va is None or vb is None:
            continue
        if va > vb:
            a_only += 1
        elif vb > va:
            b_only += 1

    n = len(common)
    return {
        "metric": metric,
        "n_common": n,
        "a_only": a_only,
        "b_only": b_only,
        "a_only_frac": a_only / n if n else 0.0,
        "b_only_frac": b_only / n if n else 0.0,
    }


#: Runs whose optimize stage predates the silent-failure-12 fix (2026-09-18). Campaign 09
#: onward is clean; 07, 08 and m01 are not, and CLAUDE.md records they are not retrospectively
#: cleaned. Listed by arm-id prefix because that is what the artifacts are keyed by.
PRE_FIX_ARM_PREFIXES = ("c07-", "c08-", "m01-")


def pre_fix_arms_in(arms) -> frozenset[str]:
    """Arms whose tool-use numbers predate the 2026-09-18 fix.

    Derived from the arm id rather than a blob timestamp: the id is what a reader sees in the
    table, and a prefix they can check beats a date they have to trust us about.
    """
    return frozenset(a for a in arms if a.startswith(PRE_FIX_ARM_PREFIXES))


def fetch_models(run_ids: list[str], bucket_name: str) -> dict[str, str]:
    """Each arm's model id, from its deploy stage artifact.

    Separate from `campaign_floor.fetch_arms` because that returns only the two eval
    artifacts, and the model id is not in them. Kept as thin as that one is -- it maps run ids
    to a field and does nothing else -- so the arithmetic stays testable without a bucket.
    """
    import json as _json

    from google.cloud import storage

    bucket = storage.Client().bucket(bucket_name)
    models: dict[str, str] = {}
    for run_id in run_ids:
        for blob in bucket.list_blobs(prefix=f"pipeline-runs/{run_id}/stages/deploy/"):
            if not blob.name.endswith(".json"):
                continue
            arm = blob.name.rsplit("/", 1)[-1][: -len(".json")]
            payload = _json.loads(blob.download_as_text())
            if payload.get("model"):
                models[arm] = payload["model"]
    return models


def render_markdown(summary: dict) -> str:
    """The frontier report. Membership table first, chart nowhere.

    Ordered so the thing that survives being copied into a doc is the thing that is true: the
    per-metric membership counts, then the caveats that qualify them. A pooled score would fit
    on one line and would be the wrong number.
    """
    points = {p.arm: p for p in summary["points_after"]}
    metrics = sorted(summary["resolutions"])
    lines: list[str] = ["## Cost-quality frontier", ""]

    lines += [f"_{summary['cost_note']}_", "", f"_{summary['resolution_note']}_", ""]

    lines += ["| arm | model | est. cost | " + " | ".join(metrics) + " | on frontier |"]
    lines += ["| --- | --- | ---: | " + " | ".join(["---:"] * len(metrics)) + " | ---: |"]
    for arm in sorted(points):
        p = points[arm]
        cost = f"${p.cost_usd:.3f}" if p.priced else "n/a"
        cells = []
        for m in metrics:
            v = p.quality.get(m)
            mark = "*" if arm in summary["frontier_after"].get(m, []) else ""
            cells.append(f"{v:.4f}{mark}" if v is not None else "—")
        lines.append(
            f"| {arm} | {p.model} | {cost} | "
            + " | ".join(cells)
            + f" | {summary['membership'].get(arm, 0)}/{len(metrics)} |"
        )
    lines += ["", "`*` = on that metric's frontier.", ""]

    if summary["excluded"]:
        lines += ["### Excluded", ""] + [f"- {e}" for e in summary["excluded"]] + [""]
    if summary["warnings"]:
        lines += ["### Warnings", ""] + [f"- {w}" for w in summary["warnings"]] + [""]

    lines += ["### Caveats", ""] + [f"- {c}" for c in summary["caveats"]] + [""]
    return "\n".join(lines)


def points_from_results(results: dict, phase: str = "after") -> list[ArmPoint]:
    """`ArmPoint`s from the report's in-memory `results` dict, for the chart path.

    The report builds `results` itself and already carries `token_usage` per arm, so the
    charts can price from tokens without reaching back to GCS -- and without touching a KFP
    component body to add a field.

    **A different cost convention from `cost_for_arm`, deliberately.** The report's
    `token_usage` is `_summed_usage(eval_before, optimize, eval_after)`, so it *includes* the
    optimize stage; `cost_for_arm` sums the two eval sides only. Both are defensible and they
    are not interchangeable, so each says which it is rather than being quietly averaged into
    one "cost". `conv` on the returned points records it.
    """
    from ..core.models import measured_cost

    points: list[ArmPoint] = []
    for arm, data in results.items():
        if arm.startswith("_"):
            continue
        model = data.get("model", "")
        usage = data.get("token_usage") or {}
        priced = measured_cost(
            model,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            data.get("costs") if isinstance(data.get("costs"), dict) else None,
        )
        scores = data.get(phase) or data.get("before") or {}
        points.append(
            ArmPoint(
                arm=arm,
                model=model or "unknown",
                cost_usd=priced["total_usd"],
                quality=dict(scores),
                coverage=data.get(f"{phase}_coverage") or 1.0,
                is_estimate=bool(usage.get("is_estimate", True)),
                priced=priced["priced"],
            )
        )
    return points


def default_resolutions(points: list[ArmPoint], n_cases: int = 64, num_runs: int = 1) -> dict:
    """Per-metric resolution for a set of points, from campaign 09's measured variance.

    Only metrics that variance source actually covers get a resolution -- an unmeasured metric
    is skipped by `frontier_membership` rather than being handed a zero, which would let noise
    decide its frontier.
    """
    from .inference import campaign_09_variance, mde_for_design

    design = mde_for_design(
        n_cases=n_cases, num_runs=num_runs, variance_source=campaign_09_variance()
    )
    seen = {m for p in points for m in p.quality}
    return {m: r for m, r in design.per_metric.items() if m in seen}
