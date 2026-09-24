# Reported deltas are now paired per case — a comparison boundary

**Date of the switch: 2026-09-24.** Every delta and every noise floor that a report judges
against is now computed by pairing on `case_index` rather than subtracting two run means.
**Numbers on either side of this date are not directly comparable**, in the same way as the
2026-09-17 judge re-baseline.

## Why

`after_mean − before_mean` partly measures *which cases were scored*, not the prompt change. When
autorater errors drop a case from one side, its score leaves that side's mean and stays in the
other's. This repo has been bitten hard: on 2026-08-22 a control arm with a byte-identical prompt
on both sides produced +0.039 and +0.035 — pure dropout, nearly promoted as a win
(silent-failures #5).

`paired_deltas()` has existed in `wrangler/eval/evaluator.py` since that incident and `PairAnalysis`
has carried `before_per_case`/`after_per_case` all along. Nothing in the reporting path used
either. This connects them.

## What changed, measured on `tests/fixtures/c09/`

### Deltas

| arm | metric | unpaired (old) | paired (new) | shift |
| --- | --- | --- | --- | --- |
| control | `final_response_quality_v1` | +0.0188 | +0.0063 | −0.0125 |
| control | `hallucination_v1` | +0.0173 | +0.0157 | −0.0016 |
| control | `instruction_following_v1` | −0.0150 | −0.0133 | +0.0017 |
| control | `safety_v1` | +0.0732 | **+0.0794** | +0.0062 |
| control | `tool_use_quality_v1` | +0.0004 | +0.0044 | +0.0039 |
| rationale-on | `final_response_quality_v1` | −0.0954 | −0.0750 | +0.0204 |
| rationale-on | `hallucination_v1` | −0.0296 | −0.0142 | +0.0154 |
| rationale-on | `instruction_following_v1` | +0.0208 | +0.0310 | +0.0102 |
| rationale-on | `safety_v1` | +0.1607 | **+0.1746** | +0.0139 |
| rationale-on | `tool_use_quality_v1` | −0.0516 | −0.0530 | −0.0014 |
| rationale-off | `final_response_quality_v1` | −0.0127 | −0.0011 | +0.0116 |
| rationale-off | `hallucination_v1` | +0.0281 | +0.0332 | +0.0051 |
| rationale-off | `instruction_following_v1` | −0.0398 | −0.0515 | −0.0118 |
| rationale-off | `safety_v1` | +0.1651 | **+0.1719** | +0.0068 |
| rationale-off | `tool_use_quality_v1` | −0.0134 | −0.0117 | +0.0017 |

The three bolded `safety_v1` figures match the values computed by hand in
[2026-09-22-campaign-09-reanalysis.md](2026-09-22-campaign-09-reanalysis.md), which is the
acceptance test: `tests/test_paired_reporting.py::TestCampaign09Acceptance` asserts them to 5e-5.

### Floors — where the real movement is

Measured from the `c09-control` arm, n=63. Scalar floor +0.073 → **+0.079**.

| metric | floor old | floor new | shift |
| --- | --- | --- | --- |
| `final_response_quality_v1` | 0.0188 | 0.0063 | **−66.6%** |
| `hallucination_v1` | 0.0173 | 0.0157 | −9.1% |
| `instruction_following_v1` | 0.0150 | 0.0133 | −11.2% |
| `safety_v1` | 0.0732 | 0.0794 | +8.5% |
| `tool_use_quality_v1` | 0.0004 | 0.0044 | **+880%** |

CLAUDE.md previously put the value of pairing at *"~15%"* and a later note revised it to 44–64%.
Both understate it: quality's floor falls **67%**, and tool-use's near-zero floor was itself a
dropout artefact — 0.0004 is not a real measurement of noise, and pairing multiplies it tenfold
(to a still-small 0.0044). **Pairing does not uniformly shrink floors.** `safety_v1`'s grows.

### One verdict changed

`c09-rationale-on` / `hallucination_v1`: **`regressed` → `within-noise`** (Δ −0.0296 → −0.0142
against a floor 0.0173 → 0.0157). The other nine arm×metric verdicts on these fixtures are
unchanged.

This is the per-arm delta view, not the reanalysis document's difference-in-differences contrasts —
those are computed in `wrangler/reporting/inference.py` and are untouched.

## A second bug fixed on the way

A metric scored on only one side used to produce a delta equal to its **entire score**, via
`.get(m, 0)` in `PairAnalysis.deltas`. On a control arm that phantom became the bar every other
metric was judged against. `floor_from_control_arm` and `reporter._metric_deltas` already refused
it; `PairAnalysis` did not.

## What is *not* paired, deliberately

Descriptive score tables (`_scores_section`, `_per_model_analysis`) still print `after − before`,
so their delta column ties out against the Before and After columns printed beside it. Everything
**judged against a floor** is paired. Mixing the two in one table would be worse than either.

## How the two are kept on one basis

This was the main risk. A paired delta compared against an unpaired floor is apples to oranges and
would shift every verdict silently. `measure_noise_floor` and `measure_noise_floor_per_metric` are
unchanged in code — they read `pair.deltas`, which is now paired, so floors follow automatically
and **cannot drift apart from the deltas they judge**.

`tests/test_paired_reporting.py::test_the_floor_is_measured_the_same_way_the_delta_is` pins it with
a constructed control whose unpaired floor is 0.167 and paired floor 0.010: a +0.050 arm reads
`improved` against one and `within-noise` against the other. It fails the moment one side stops
pairing.

## Older artifacts still work

Artifacts predating per-case capture fall back to the aggregate, per metric where necessary
(`source` is `"paired"`, `"aggregate"` or `"mixed"`, with `aggregate_metrics` naming which). A
metric cannot silently vanish from a report table because the per-case rows never carried it.

`n_paired` travels with every paired number — a paired delta over 12 of 64 cases is a different
claim from one over 60.
