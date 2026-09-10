# Campaign 08 — Did the criteria fix work?

**Status:** Pre-registered 2026-09-10, not yet run.
**Follows:** [07-cost-quality-frontier.md](07-cost-quality-frontier.md) and
[../analysis/2026-09-10-campaign-07-wrapup.md](../analysis/2026-09-10-campaign-07-wrapup.md)
**Took slot 08 from** the budget curve, now [10](10-gepa-budget-curve.md).

## Question

Campaign 07's one replicated finding: on both `claude-sonnet-5` and
`gemini-3.1-pro-preview`, GEPA improved `safety_v1` — a criterion it is scored on — and
degraded `instruction_following_v1`, the metric absent from its criteria. Those were the
only two effects that cleared both the noise floor and the run-to-run spread.

PR #69 acted on it. `instruction_following_v1` cannot be a GEPA criterion — it is not in
ADK's metric registry and naming it raises `NotFoundError` — so the pressure went into
`rubric_based_final_response_quality_v1`, where instruction adherence moved from **1 of 2
rubrics to 3 of 4**.

**Does that remove the regression, reduce it, or merely move it?**

## Design

One model, two criteria conditions, two repeats each, plus a control per batch.

| arm | agent module | criteria | role |
| --- | --- | --- | --- |
| `c08-new-r1`, `c08-new-r2` | `sonnet_agent` | adherence **3 of 4** | treatment |
| `c08-old-r1`, `c08-old-r2` | `sonnet_baseline_agent` | adherence **1 of 2** | baseline |
| `c08-ctrl-a` … `-d` | `sonnet_agent` | — (`skip_optimize`) | floor, one per batch |

Held identical across all eight: `claude-sonnet-5`, the 78-character generic seed,
`max_metric_calls: 600`, `num_runs: 3`, eval set, health gate required on deploy *and*
redeploy. The two sampler configs are asserted byte-identical apart from the two rubrics
(`tests/test_sampler_configs.py`).

**Two repeats per condition, not one.** Campaign 07 measured run-to-run spread at up to
12.3x the control floor, with two runs of one manifest disagreeing on the *sign* of three
metrics. One arm per condition could not attribute a difference to the criteria.

**One optimizing arm per batch, not two.** Both conditions run Claude — that is the point —
so pairing them would put two Anthropic optimize phases on one quota pool.
`run_campaign.validate()` rejects it. Campaign 07 could pair arms only because it varied the
model. The honest cost is four sequential optimize phases, **~44 h**, against campaign 07's
~28 h.

**Conditions alternate across batches** so a substrate drift over two days cannot land
entirely on one condition.

## Pre-registered decision rule

**Primary outcome:** Δ`instruction_following_v1` (after − before), per arm.
**Effect:** mean Δ over the two `new` arms − mean Δ over the two `old` arms.

The effect counts only if it clears **both**:

1. **> 0.017** — the within-condition spread from campaign 07's accidental repeat of
   `c07-sonnet5` (IF: −0.045 vs −0.062).
2. **> this campaign's own IF control floor**, from its four control arms.

**Stopping rule:** run the controls as a gate. If a control's own IF drift exceeds 0.017,
stop and report that the substrate cannot resolve this effect.

**Secondary, reported either way:** Δ`safety_v1`. If the new criteria shrink the IF
regression *and* shrink the safety gain, that is a trade, not a fix — and it is the more
useful finding.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| IF regression shrinks, safety holds | The criteria were miscalibrated. Adopt 3/4 and re-baseline |
| IF regression shrinks, safety shrinks too | A real trade. Weighting relocates the cost, it does not remove it |
| No difference beyond 0.017 | Rubric weighting is too indirect. The metric needs to be a criterion, which ADK does not allow — escalate upstream |
| Within-condition spread exceeds between-condition | n=2 is still too few. Report the variance and stop designing around single runs |

**If the bar turns out to be wrong:** if c08's own within-condition spread comes out above
0.017, the pre-registered threshold was too low and the answer is *unresolved*. Say that
rather than re-deriving the bar from the data it is judging.

## Cost

~44 h wall clock, ~$4. Optimize is ~87% of it; the binding constraint is judge RPM and
Anthropic quota, not budget.

## Result

_Not yet run._
