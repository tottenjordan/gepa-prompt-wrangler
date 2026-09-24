# The continuous score has real gradient: 3.1x the resolution, ties down from 29 to 7

**One-line result:** the per-case gradient ADK throws away is not a rounding detail. Recovering it
gives GEPA's selection signal **25 distinct values instead of 8**, and cuts the candidates sitting
on a tied score from **29 of 37 to 7**. Three of the four criteria carry genuine per-case
continuity; `safety_v1` does not and never will.

**Verdict: GO on option A** of
[the validation-subset scoping](2026-09-24-validation-subset-scoping.md), which gated implementation
on this measurement.

## Why this needed measuring

The scoping note found that ADK collapses each case to `1.0 if PASSED else 0.0`
(`local_eval_sampler.py:358`) before averaging, so every run in the project's history selected its
winning prompt from six distinct scores. It recommended recovering the continuous score — but
**explicitly refused to recommend building it**, because only the binary scores were ever archived:
if the underlying metrics were themselves near-binary, the change would buy nothing.

## Method

ADK patch 4 (`optimizer.py:286`) already computes per-metric continuous means per batch and logs
them. Those lines survive in Cloud Logging for campaign 09's pipeline job
(`gepa-run-86239e1924-20260917-213907`, 2026-09-17, inside the 30-day window):

```
resource.type="ml_job" AND jsonPayload.message:"Eval batch"
```

251 batches recovered across both optimizing arms — 35 validation passes (n=15, the signal that
picks the winner) and 216 reflection minibatches (n=2 or 3).

**The test is arithmetic, not judgement.** A batch mean over *n* cases whose per-case scores are
all 0 or 1 must equal *k/n*. Any printed mean that is not *k/n* proves the per-case score is
continuous. `hallucinations_v1=0.79` over 3 cases implies a sum of 2.37, which no combination of
zeros and ones produces.

## Result 1 — three of four criteria are continuous per case

Validation batches (n=15), 35 of them:

| criterion | means that are **not** k/n | distinct values | range |
| --- | --- | --- | --- |
| `rubric_based_final_response_quality_v1` | **77%** | 6 | 0.92 – 1.00 |
| `hallucinations_v1` | **63%** | 18 | 0.63 – 1.00 |
| `rubric_based_tool_use_quality_v1` | **54%** | 7 | 0.80 – 1.00 |
| `safety_v1` | **0%** | 2 | 0.93 – 1.00 |

The reflection minibatches agree (15–31% non-binary at n=3, where a smaller *n* makes the test less
sensitive, and 0% for safety).

**`safety_v1` is genuinely binary at the case level**, and this is consistent rather than
surprising: DOE 02 measured its per-case judge disagreement at **0/64**, and DOE 03 put its
`score_repeats` exponent at **0.02**. It is a stable pass/fail judgement. A continuous score cannot
help it, and since `safety_v1` carries this repo's headline results, **option A does not improve
the metric the campaign reports on** — it improves the signal GEPA *selects* with.

## Result 2 — the resolution gain

| | distinct values | candidates on a tied score | range | sd |
| --- | --- | --- | --- | --- |
| binary aggregate (what GEPA used) | **8** over 37 candidates | **29** | 0.5333 – 1.0000 | 0.1006 |
| continuous composite | **25** over 35 batches | **7** | 0.8675 – 0.9875 | 0.0289 |

**3.1x more distinguishable levels, and ties fall from 29 to 7.**

The tie count is the part that matters for selection. `gepa.core.result.best_idx` is the *first*
argmax, so a tie is broken by discovery order — which is a property of the search's luck, not of
the prompt. Under the binary signal 78% of candidates were tied with something; under a continuous
one, 20% are.

Note the continuous composite has a *smaller* standard deviation while having far more levels.
That is not a contradiction: the binary signal moves in coarse 1/15 jumps, which inflates spread
without adding information. For ranking, the number of distinguishable levels is the relevant
quantity.

## Caveats

- **The composite is a placeholder.** It is an unweighted mean of the four per-metric means. A real
  implementation has to decide how to weight criteria whose thresholds already differ (0.95 for
  safety, 0.85 for response quality). The resolution gain is robust to that choice; the *ranking*
  is not.
- **The join is distributional, not per-candidate.** 35 validation batches against 37 candidates —
  close to 1:1, but the logs do not carry a candidate index, so this compares distributions rather
  than pairing rows. It cannot say which specific candidate would have won.
- **The gradient lives in a narrow band.** Every criterion is compressed near its ceiling on
  validation (response quality 0.92–1.00, safety 0.93–1.00). Continuity buys resolution; it does
  not buy headroom. This is an argument for *also* doing option B — a subset with more and harder
  cases — rather than treating A as sufficient.
- **Two arms of one campaign**, both of which saturated the binary signal. m01's optimize logs were
  not queried; it ran 2026-09-17 and is also in window if a second opinion is wanted.

## What this changes in the plan

Step 1 of the scoping note is now answered, and options A and B are both live. A costs no extra
metric calls and triples selection resolution; B costs ~70% more per candidate, fits the budget
early stopping frees, and is the only one of the two that addresses the 7 strata absent from
validation and the compression against the ceiling. They remain complementary, and the
recommendation to land them as **one** re-baselining boundary before campaign 10 stands.
