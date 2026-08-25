# Campaign 04 — What does a GEPA budget buy, and is the holdout regression fixable?

**Status:** Not started · **Depends on:** Campaign 03 · **Expensive — run last**

Two questions that share a substrate and a control arm, so they are one campaign.

---

## Question A — the budget response curve

`max_metric_calls: 800` in the per-model manifests comes from a **single observation**:
generation 1 of one run used 102 calls in 602s, so 800 buys "roughly eight generations".
That is a reasonable guess and has never been tested. What does budget actually buy?

### Design

One model, one seed prompt, `max_metric_calls ∈ {200, 400, 800, 1600}`, plus a **mandatory
unchanged-prompt control arm**, all at whatever `num_runs` Campaign 03 recommends. Report
gain per metric against budget with the measured floor drawn on the chart, so a point below
the floor is visibly not a point.

Also record generations completed per arm, not just calls spent — the interesting quantity
is whether GEPA is still finding improvements when the budget runs out, or plateaued long
before.

### What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Gains flatten before 800** | 800 is overspend. Lower the manifest default and get wall-clock back. |
| **Still climbing at 1600** | 800 is underspend, and the 2026-08-22 sweep understated what GEPA can do. |
| **All arms inside the floor** | The substrate cannot resolve budget effects. Report that and stop — do not run Question B. |
| **Non-monotonic** | Search variance dominates budget at this scale, which means single-arm sweeps of any budget are not interpretable. |

---

## Question B — instruction-following as a criterion

Pro's optimized prompt regressed **−0.095 on `instruction_following`**, a metric GEPA never
optimizes. Its only pressure is indirect, through the `instruction_adherence` rubric inside
`rubric_based_final_response_quality_v1` — and at the time of the sweep that rubric was
gated at **0.85 for sonnet and 0.50 for pro**, which is the leading explanation for why the
two arms moved the metric in opposite directions.

Is the regression a designed-in consequence of leaving the metric out, and does adding it
fix it or just trade the loss somewhere else?

### Design

Same model, seed and budget across four arms:

1. criteria as they stand today
2. criteria with the FRQ `instruction_adherence` threshold equalised
3. criteria with instruction-following added as a first-class criterion
4. **unchanged-prompt control**

Sampler configs at `examples/multi_model_agents/agents/*_opt/sampler_config.json` are the
single source of truth — edit those, not the manifests, which do not override them.

### What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Equalising the threshold removes the regression** | It was a configuration asymmetry, not a property of the model. Fix the configs and move on. |
| **Only adding the criterion outright fixes it** | Anything not in the criteria is genuinely free to be traded away. That is a general rule about this harness and belongs in CLAUDE.md. |
| **Adding it fixes instruction-following but costs elsewhere** | Real multi-objective trade-off. Report the frontier rather than picking a winner. |
| **Nothing fixes it** | It is a property of what pro's search converges on, and pro's prompt should not be promoted. |

---

## Pre-registration (both questions)

- **The control arm runs first, as a gate** (CLAUDE.md). If its deltas exceed the effects
  being chased, stop and report that.
- **Arms run sequentially, never concurrently.** The 2026-08-22 sweep violated its own plan
  on this and paid in wall-clock and dropout.
- Same seed, same eval set, same `num_runs` across all arms within a question.
- Report every arm, including ones that return the seed unchanged — that is a legitimate
  result, and last time it was the only calibration the sweep had.

## Cost

~10 h per optimize arm. Question A ≈ 50 h, Question B ≈ 40 h. Money is trivial — the entire
three-arm 2026-08-22 sweep cost **$1.14** — the calendar is not. Anyone hesitating over spend
should hesitate over wall-clock instead.

## Result

_Not yet run._
