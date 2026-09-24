# The cost-quality tradeoff, as an instrument rather than a scatter plot

**One-line result:** replacing an averaged scalar with per-metric Pareto frontiers turns the
cost-quality chart from decoration into a readout — on campaign 07 the **cheapest arm is on 5 of 5
frontiers**, and the same command shows the optimized cheap tier crossing the expensive tier on a
criterion (`safety_v1`, +0.1668) while falling resolvably **behind** it on the holdout
(`instruction_following_v1`, −0.1088).

Reproduce with:

```bash
uv run wrangler frontier run-5aa73d6191 run-8a5905dee0 run-70166a6bc8 \
  --cheap c07-pro --expensive c07-sonnet5
```

## What was wrong

`generate_cost_quality_chart` plotted `np.mean(list(before_scores.values()))` — **five metrics
collapsed into one number** — against `blended_cost(model)`, which is **list price at an assumed
4:1 input:output ratio**, with **no uncertainty**. Per-metric floors span 3.4×, and campaign 09
measured metrics moving in opposite directions. The average hid precisely the tradeoff the chart
existed to show.

## Three things exploration changed about the plan

**1. Nothing in this harness is metered.** The research report recommended plotting "measured"
cost. But `evaluator.py:_estimate_token_usage` computes `len(text) // 4`, every artifact carries
`is_estimate: True`, and `usage_metadata` is read nowhere in `wrangler/` — the managed
`run_inference()` call returns no usage columns, and none are nested in `agent_data` or
`intermediate_events` either. Costs here are **estimates**, labelled as such in every table, axis
and payload. Pricing estimated tokens still beats list price, because it reflects how verbosely a
model actually answers. On campaign 09 that is worth 59%: three arms of **the same model** span
$0.40–$0.63, which `blended_cost` renders as three identical points.

**2. No pipeline job holds more than one tier.** Every `run-*` in GCS is a single pair, except
campaign 09's three arms of one model. So the instrument assembles **across run ids**, reusing
`campaign_floor.fetch_arms` rather than reimplementing it.

**3. There is no confidence-interval machinery, deliberately.** The only interval code is
`wilson_interval`, for binary reach-rate data. Idea 2 explicitly deferred choosing a method for
these metrics, because CLT and bootstrap are both miscalibrated below a few hundred datapoints
and our metrics span 4 distinct values (`safety_v1`) to 187 (`instruction_following_v1`). So
domination uses the **design's minimum detectable effect** instead — a property of the design, not
an interval around the observed gap. It says the honest thing ("this design cannot tell these
apart") without inventing the interval that question needs.

## The rule that makes the frontier mean something

**An arm dominates another only if it is cheaper AND better by more than the resolution.** Arms
closer than that both stay on the frontier.

Without this the frontier is decided by noise — the same error the averaged scalar made in
different clothing. It is also why the rendered output states the rule: a different threshold
gives a different table, and a reader must be able to see which one produced theirs.

## Campaign 07, measured

Three arms across three run ids, two publishers. `num_runs=3`, so resolutions differ from
campaign 09's.

| arm | model | est. cost | on frontier |
| --- | --- | ---: | ---: |
| `c07-sonnet5` | claude-sonnet-5 | **$0.326** | **5/5** |
| `c07-ctrl-sonnet5` | claude-sonnet-5 | $0.483 | 4/5 |
| `c07-pro` | gemini-3.1-pro-preview | $0.574 | 4/5 |

**The cheapest arm is on every frontier**, which is HAL's finding (arXiv 2025-10-13, 21,730
rollouts) that cost-accuracy frontiers are dominated by cheaper tiers rather than flagships,
reproduced here on our own data.

Two metrics have a real frontier contest: `safety_v1` excludes the control, and
`instruction_following_v1` excludes `c07-pro`. The other three keep all arms, because their
differences are inside what this design resolves.

### Does optimizing the cheap tier reach the expensive tier untuned?

The bar is the expensive arm's **before** score — the practical question is not whether a tuned
cheap model beats a tuned expensive one, which needs both budgets, but whether prompt work buys
what the next tier gives off the shelf.

| metric | verdict |
| --- | --- |
| `safety_v1` | **crossed** +0.1668, beyond the 0.0901 resolution |
| `instruction_following_v1` | **behind** −0.1088, beyond the 0.0904 resolution |
| other three | inside resolution; this design cannot call them |

That pair is this repo's signature finding — GEPA improves a criterion and degrades the holdout —
now visible from one command rather than a bespoke analysis.

### Complementarity

Per case, how often each arm beats the other (`c07-pro` vs `c07-sonnet5`, 63 common cases):

| metric | pro-only | sonnet-only |
| --- | ---: | ---: |
| `safety_v1` | 10% | 8% |
| `final_response_quality_v1` | 33% | 27% |
| `instruction_following_v1` | 41% | 57% |

**Read these against judge noise, never pooled.** DOE 02 measured per-case judge self-disagreement
on byte-identical responses at **0/64 for `safety_v1`** and **64/64 for
`instruction_following_v1`**. So safety's 18% combined is close to a real split between the two
models; instruction-following's 98% is mostly the judge disagreeing with itself. The function
documents this and a test pins the two metrics on opposite ends.

## Caveats on the c07 numbers

These qualify the *result*, not the instrument.

- **Campaign 07 predates the 2026-09-18 silent-failure-12 fix.** 12–24% of its cases scored an
  agent that had been handed zero tools, so `tool_use_quality_v1` is uninterpretable. The tool
  prints this warning beside the number rather than leaving it in a doc, because a caveat that
  does not travel with a copied table is not doing its job.
- **One run per condition.** The frontier reflects which prompt GEPA happened to find, and that
  term is measured at 12.3× the control floor. This is a demonstration, not a finding.
- **The cost estimate ignores tokenizer differences**, and this is a cross-publisher comparison.
  A systematic bias between Gemini and Claude tokenization would tilt the x-axis.
- **Resolutions come from campaign 09's variance**, which is one run per condition and a different
  campaign. A floor is not a property of a metric.

## What would make this a finding rather than a demonstration

A multi-tier campaign, post-2026-09-18, with replicates — which the budget freed by early stopping
is meant to pay for. The instrument is ready for it; the data is not there yet.
