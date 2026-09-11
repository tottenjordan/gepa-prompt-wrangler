# Campaign 08 — Screening the criteria fix

**Status:** Pre-registered 2026-09-10. **Rescoped to a screen 2026-09-11, before any data
existed.** Not yet run.
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

**Is there a large enough effect to be worth a controlled comparison?** That is a weaker
question than "does the fix work", and deliberately so. See below.

## Design

One model, **one** criteria condition, two repeats, a control per batch.

| arm | agent module | criteria | role |
| --- | --- | --- | --- |
| `c08-new-r1`, `c08-new-r2` | `sonnet_agent` | adherence **3 of 4** | treatment |
| `c08-ctrl-a`, `c08-ctrl-b` | `sonnet_agent` | — (`skip_optimize`) | floor, one per batch |

Held identical across all four: `claude-sonnet-5`, the 78-character generic seed,
`max_metric_calls: 600`, `num_runs: 3`, eval set, health gate required on deploy *and*
redeploy.

**Two repeats, not one.** Campaign 07 measured run-to-run spread at up to 12.3× the control
floor, with two runs of one manifest disagreeing on the *sign* of three metrics. A single
arm could not attribute anything.

**A control per batch, never carried over.** CLAUDE.md forbids reusing a floor measured
under different load. A control has no optimize stage, so it shares the Anthropic pool for
free and adds no wall clock.

### Why the concurrent baseline was dropped

The design was crossed: `new` and `old` criteria, two repeats each, with the conditions
differing only in `sampler_config.json` — selected by agent-module name, hence the
`sonnet_baseline_agent` twin.

**One optimizing arm per batch is forced, not chosen.** Both conditions run
`claude-sonnet-5`; that is the point, the model is held constant so criteria is the only
variable. So both optimize phases land on the Anthropic quota pool and
`run_campaign.validate()` rejects pairing them — it caught the crossed design before it
ran. Campaign 07 could pair its arms only because it varied the model.

Four arms therefore meant four *sequential* optimize phases: **~44 h**, two nights, with
`main` frozen throughout because `submit()` packages the working tree at each batch.
Halving the arms halves that.

**This is not a concurrency problem and cannot be scheduled away.** Optimize is 87% of a
run and is judge-bound: a *single* arm drew 70 × HTTP 429 in 52 generations against
`gemini-3.5-flash` at `rpm=5`, and two arms drew 76 — barely more, because the quota is
shared and fixed. There is no arrangement that runs four optimize phases in 22 h. The
standing fix is [the quota escalation](../escalations/2026-09-11-judge-quota.md); until it
lands, arm count is wall clock.

## What a screen can and cannot conclude

**It cannot rule the criteria change out.** The `old` comparison is now against campaign
07's recorded −0.045 / −0.062, measured on a substrate that has since moved: PR #59
(tool-list cache), #70 (redeploy health gate) and #75 (per-generation MCP refresh removed)
all landed in between. A difference against those numbers cannot be attributed cleanly to
the criteria.

**It can screen the change in.** An effect large enough to dwarf that ambiguity justifies
running the staged `old` arms as a proper controlled follow-up — and that follow-up is why
`sonnet_baseline_agent`, its `_opt` directory, the `c08-old-*` manifests and controls `-c`
and `-d` are all kept rather than deleted.

## Pre-registered decision rule

**Primary outcome:** Δ`instruction_following_v1` (after − before), per arm.
**Screening statistic:** mean Δ over the two `new` arms.

Fixed here, before any data:

| Reading | Condition | What follows |
| --- | --- | --- |
| **Screened in** | mean Δ IF ≥ 0, i.e. the regression is *gone or reversed*, and both arms agree in sign | Run the two staged `old` arms as a controlled comparison |
| **Unresolved** | mean Δ IF between campaign 07's −0.062 and 0 | The change may have reduced the regression or the substrate may have. **Not a negative result.** Report the number and stop |
| **Screened out** | mean Δ IF below −0.062, or the two arms disagree in sign | Rubric weighting is too indirect. The metric needs to be a criterion, which ADK does not allow — escalate upstream |

**Gate:** the controls run in the same batches. If a control's own IF drift exceeds
**0.017** — the within-condition spread from campaign 07's accidental repeat of
`c07-sonnet5` — stop and report that the substrate cannot resolve this effect at all.

**Secondary, reported either way:** Δ`safety_v1`. If the IF regression shrinks *and* the
safety gain shrinks, that is a trade, not a fix — and it is the more useful finding.

**Unresolved is the most likely outcome, and that is understood going in.** Campaign 07's
effect was −0.062 against a ~0.015 floor; a partial improvement lands in the band a screen
cannot read. Naming that band in advance is the reason for pre-registering it, rather than
deciding afterwards which side of it the number fell on.

## Batch 1 is also the silent-failure #12 gate

`c08-new-r1` is the validation arm, and its optimize log answers a question no test can.
Count `will run without the tools`:

| observation | reading |
| --- | --- |
| **0** | PR #75 fixed it. The per-generation MCP refresh was the cause |
| near **14–15%** | It was not the refresh either. Resume from `scripts/repro_mcp_refresh_hang.py` |

Campaign 07 lost 16 of 111 generations that way (14%); campaign 08's earlier validation arm
lost 3 of 20 (15%) *with* PR #59 in place, which is what ruled the tool-list cache out. The
reproduction now measures 9/18 hangs with a racing `close()` and cache off, 0/18 otherwise —
so the refresh is the remaining candidate, and this arm is the test.

## Known contamination

**`tool_use_quality_v1` is not interpretable in this campaign unless the count above comes
back at 0.** Recorded before any results exist so it cannot be discovered afterwards and
argued about. A tool-using agent evaluated with an empty toolset scores near zero on tool
use, and those candidates feed the objective GEPA is searching against.

It does not threaten the primary outcome — that is Δ`instruction_following_v1`, and the
secondary is Δ`safety_v1`. Campaign 07 reached the same conclusion for the same reason, and
its two surviving results were safety and instruction-following, not tool use.

**Do not report a tool-use result from this campaign in either direction**, including "no
change", unless the generation count is clean.

## Cost

~22 h wall clock, ~$2, against the crossed design's ~44 h. Optimize is ~87% of it; the
binding constraint is judge RPM, not budget.

## Result

_Not yet run._
