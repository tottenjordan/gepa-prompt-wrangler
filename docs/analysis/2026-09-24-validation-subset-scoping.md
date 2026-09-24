# Scoping: GEPA's validation subset selects on a 6-value signal, blind to 7 of 18 strata

**One-line result:** the 15-case validation subset is the *smaller* of two defects. Each case is
collapsed to pass/fail before averaging, so every run in the project's history picked its winning
prompt from **exactly 6 distinct scores**; and 7 of 18 `(tier, category)` strata never appear in
validation at all, so no candidate is ever selected on them. Fixing the collapse costs **no extra
metric calls**; enlarging and stratifying the subset costs ~50–70% more per candidate, which the
budget early stopping frees will cover.

This is a scoping document. It says what is broken, what each fix costs, and what still has to be
measured before committing. It does not change any behaviour.

## Why anyone looked

[The stopping replay](2026-09-24-stopping-replay.md) found early stopping to be free — patience 15
returns a byte-identical prompt on all three archived runs while saving 64.1% of the budget. The
reason it is free is that GEPA's selection signal stops improving very early: two of three runs
saturated at 1.0000, the third topped out one case short and then could not improve for 52 of its
57 iterations. That is an instrument problem, and this note measures it.

## What was measured

All three archived `gepa_state.bin` files (campaign 09's two arms and m01; 07 and 08 predate
run_dir archiving), plus the shipped `sampler_config.json` and ADK 2.8.0's source.

## Finding 1 — the per-case score is binary by construction

`prog_candidate_val_subscores` contains **only 0.0 and 1.0**. Across c09-rationale-on that is
269 ones and 46 zeros over 21 candidates × 15 cases; m01 is 253 and 77. There are no intermediate
values anywhere.

The cause is in ADK, at `local_eval_sampler.py:358`:

```python
scores = {
    eval_result.eval_id: (1.0 if eval_result.final_eval_status == EvalStatus.PASSED else 0.0)
    for eval_result in eval_results
}
```

So there are **two collapses before the averaging**:

1. Each metric's continuous score → pass/fail at its `sampler_config.json` threshold
   (`safety_v1: 0.95`, `rubric_based_final_response_quality_v1: 0.85`).
2. All criteria ANDed into one case-level `final_eval_status`.

A candidate that moves response quality from 0.50 to 0.84 scores **identically** to one that
does not move it at all. A candidate that moves safety 0.96 → 1.00 likewise. Every gradient
inside the thresholds is discarded before GEPA ever sees it.

**The continuous score is available at that point, and this repo already reads it.** ADK patch 4
(`optimizer.py:286`) iterates `case_result.eval_metric_result_per_invocation[].eval_metric_results[]`
and uses `mr.score` to log per-metric means. The number needed to replace the binary collapse is
one the repo is already computing and throwing away.

## Finding 2 — six values, every run

| arm | candidates | distinct aggregate scores | best | ties at best |
| --- | --- | --- | --- | --- |
| `c09-rationale-on` | 21 | **6** | 1.0000 (saturated) | 2 |
| `c09-rationale-off` | 16 | **6** | 1.0000 (saturated) | 1 |
| `m01-rationale-merge` | 22 | **6** | 0.9333 | 1 |

Twenty-odd candidates per run, landing on six rungs. With `best_idx` being the *first* argmax, a
tie is resolved by discovery order — so on a saturated run the returned prompt is whichever
candidate first reached 15/15, which is a property of the search's luck rather than of the prompt.

**This is a candidate explanation for the run-to-run spread** that CLAUDE.md records at 12.3× the
control floor and which nothing has explained. It remains a hypothesis: it predicts that the spread
shrinks when the signal is given resolution, and that is a campaign-sized test.

## Finding 3 — 7 of 18 strata never appear in validation

The split is 49 train / 15 validation, and it is unstratified. Train covers 18 distinct
`(tier, category)` cells, validation 11. The seven cells **absent from validation entirely**:

`high/booking`, `high/cancellation`, `low/cancellation`, `low/expense`, `low/policy`,
`medium/error_handling`, `medium/planning`

Those behaviours can influence reflection (they are in train) but can **never** influence which
candidate is returned. This compounds Finding 1 rather than duplicating it: even with a perfectly
continuous score, 39% of the behaviour space would still be invisible to selection.

It also re-reads an earlier result. [The train/validation composition
note](2026-09-23-train-validation-composition.md) found a −0.1146 `safety_v1` gap between the two
subsets **in the control arm**, which runs no optimizer — attributing it to composition. This is
the mechanism behind that number.

## Cost model

Minimum metric calls between candidate discoveries is **21 in all three runs**, of which 15 are
the validation pass. So cost per candidate ≈ `val_size + 6`, the +6 being empirical overhead
(ADK's default `reflection_minibatch_size` is 3, so it is not purely that — do not over-read it).

Where patience 15 would stop, scaled by that relation:

| val size | c09-on | c09-off | m01 |
| --- | --- | --- | --- |
| 15 (today) | 201 calls (33%) | 255 (42%) | 192 (32%) |
| 25 | 297 (49%) | 376 (63%) | 283 (47%) |
| 30 | 345 (57%) | 437 (73%) | 329 (55%) |

**Doubling the validation subset fits inside the budget early stopping frees**, with headroom on
every run. That is the whole affordability argument, and it is why these two pieces of work belong
together.

## Options

| | change | metric-call cost | resolution gained | risk |
| --- | --- | --- | --- | --- |
| **A** | Score cases on the mean continuous metric score instead of pass/fail | **none** | 6 values → effectively continuous | changes what GEPA selects on; an ADK behaviour change |
| **B** | Enlarge validation to ~30 and stratify across all 18 cells | +71%/candidate, affordable | step halves (1/15 → 1/30); 18/18 strata visible | fewer train cases (49 → ~34) |
| **C** | Retune the thresholds | none | **none** — moves the cliff, does not remove it | — |

**C is rejected**: a threshold change relocates which candidates tie, without increasing the number
of distinguishable outcomes.

**A and B are independent and address different halves.** A restores gradient *within* a case; B
restores coverage *across* cases. Neither substitutes for the other.

## Recommendation

**Do A and B together, as one re-baselining boundary, before campaign 10.** Both change the
selection signal, so each is a comparability boundary of the same kind as the 2026-09-17 judge
re-baseline; taking them separately spends two boundaries for one benefit. Campaign 10 has not run,
so the window is open now and closes when it starts.

Sequence:

1. ~~**Measure A's benefit before building it.**~~ **DONE 2026-09-24 — GO.** Patch 4's logged
   per-metric means were recovered from campaign 09's optimize stage via Cloud Logging. Three of
   four criteria are continuous per case (54–77% of batch means are not expressible as k/n); a
   continuous composite gives **25 distinct values instead of 8** and cuts tied candidates from
   **29 of 37 to 7**. `safety_v1` is the exception and is genuinely binary, so A improves the
   signal GEPA *selects* on, not the metric it reports.
   [2026-09-24-continuous-score-gradient.md](2026-09-24-continuous-score-gradient.md)
2. **Implement A at the existing hook.** `optimizer.py:881–922` already wraps
   `sampler.sample_and_score` on the instance for MCP session refresh, so this needs no new
   class-level monkey-patch. Per CLAUDE.md, anything touching the patch set re-runs the per-patch
   probe in `docs/notes/adk-patch-status.md`.
3. **Implement B by generalising the existing splitter.** `wrangler/core/partitions.py`
   has `stratified_split()`, but it is **hardcoded to three partitions at 40/12/12**
   (`PARTITIONS = ("train", "validation", "test")`) and generates `eval_data/partitions.yaml`
   for the closed Idea 1 plan. GEPA needs a *two*-way split at a chosen size, so this needs a
   sizes parameter rather than a call. `validation_eval_case_ids` is a top-level key in
   `sampler_config.json`, so the write side is a config change plus a generator.

   **Coordinate with `partitions.yaml` before choosing a size.** It reserves 12 of the 64 cases
   as a held-out test partition (40/12/12). GEPA's current 15-case validation **already overlaps
   that test partition by 1 case**; at 30 cases the expected overlap is ~6, which would quietly
   un-hold-out half of it. Idea 1 closed without the eval set growing, so whether that partition
   is still meant to bind is a decision to make explicitly rather than discover later. If it is,
   B must draw from the 52 non-test cases, which caps validation at a size that keeps a usable
   training pool.
4. **Re-run the stopping replay afterwards.** Patience 15 was chosen against the binary signal. A
   continuous signal plateaus differently, so the default must be re-derived, not assumed —
   `scripts/replay_stopping.py` takes new trajectories as arguments.

## What this analysis cannot say

- ~~**Whether A helps, and by how much.**~~ Settled 2026-09-24: 3.1x the distinguishable levels,
  ties down from 29 to 7. What remains unknown is how to *weight* the criteria in the composite —
  the resolution gain is robust to that choice but the resulting ranking is not.
- **Whether either fix reduces the run-to-run spread.** That is the motivating hypothesis and it
  needs replicates, which the same freed budget pays for.
- **What B costs in optimization quality.** Moving 15 cases from train to validation shrinks the
  reflection pool by 31%. No measurement here bounds that, and it is the main argument for 25
  rather than 30.
