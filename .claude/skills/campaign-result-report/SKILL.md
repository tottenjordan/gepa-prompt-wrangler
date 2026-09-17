---
name: campaign-result-report
description: Use when writing up a finished DOE campaign, optimization sweep, or experiment arm in this repo — documenting results, comparing a run against prior campaigns, or turning stage artifacts into a report someone will act on.
---

# Campaign result report

## Overview

A result report turns one campaign's stage artifacts into a document that survives contact
with the next reader. The hard part is not assembling numbers — `wrangler report` does that.
The hard part is **saying what the numbers can and cannot support**, which is where every
retracted claim in `docs/analysis/` came from.

**The organising rule: a delta is not a result until it clears the floor.** This repo has
published a +0.039 "improvement" from an arm whose prompt was byte-identical on both sides
(silent-failures #5). Every number in a report needs its bar printed next to it.

## When to use

- A campaign or sweep has finished and its `stages/` artifacts exist
- Someone asks to document, write up, or interpret a run
- Comparing a run against prior campaigns
- A pre-registration (`docs/doe/*.md` or `<experiment>/doe_plan.md`) needs its Result section filled

**Not for** an in-flight or failed run — that is diagnosis, and it belongs to the
`inspecting-pipeline-runs` skill. Use that first, then come back here when the run finishes.

## Where the output goes

| Run type | Report | Images |
| --- | --- | --- |
| Has an experiment dir | `experiments/active/<name>/reports/` | `experiments/active/<name>/images/` |
| Cross-cutting analysis, DOE result, campaign write-up | `docs/analysis/YYYY-MM-DD-<slug>.md` | via PaperBanana; link from the doc |

`experiments/README.md` is authoritative on the experiment layout. Match the surrounding
`docs/analysis/` files for naming — dated, slugged, and listed in `docs/notes/README.md` if
it carries a lesson someone must not rediscover.

## Quick reference

| Step | Action |
| --- | --- |
| 1 | Find the run and confirm it actually finished — `inspecting-pipeline-runs` skill |
| 2 | Pull stage artifacts from `gs://{bucket}/pipeline-runs/{run_id}/` or the experiment's `stages/` |
| 3 | Compute per-metric deltas and classify — `scripts/summarize_arm_metrics.py` |
| 4 | Read the pre-registration **before** interpreting, and the prior reports it compares to |
| 5 | Figures with **PaperBanana** (`paperbanana-figures` skill), not raw matplotlib |
| 6 | Write it, using `references/report-template.md` |

## Step 3: compute, do not eyeball

```bash
uv run python .claude/skills/campaign-result-report/scripts/summarize_arm_metrics.py \
    experiments/active/<name>
```

Every statistic comes from `wrangler/reporting/analyzer.py` — the same functions
`wrangler report` uses, so the report and the generated artifacts cannot disagree. It prints
per-metric deltas, the per-metric floor from the control arm, a verdict per metric, and the
paired delta with its case count.

**It prints `UNCALIBRATED` and refuses to classify when there is no control arm.** That is
the correct output, not a failure — see below.

## Step 4: read the pre-registration first, and answer it

Campaigns here are pre-registered. Open `docs/doe/<n>-*.md` or the experiment's
`doe_plan.md` **before** looking at the results, and write the Result section **against its
own decision table**. A campaign that quietly answers a different question than the one it
registered is how a null becomes a finding.

If an outcome the pre-registration listed did not happen, say so explicitly rather than
omitting the row.

## Step 5: figures

CLAUDE.md: **use PaperBanana for charts, not raw matplotlib.** Invoke the
`paperbanana-figures` skill. Fall back to matplotlib only after PaperBanana fails twice, and
say in the report which was used.

Useful figures for a campaign, in rough order of value:

1. **Per-metric delta per arm, with the floor drawn as a band** — the single most useful
   chart this repo produces, because it makes "inside the noise" visible instead of asserted
2. Score trajectory across generations, if the optimize stage's `run_dir` was preserved
3. Coverage per metric per side (`cases_scored / total`) — dropout is a confound, not a detail
4. Prompt length before/after, when length is under discussion

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Reporting a delta without its floor | Print the per-metric floor beside every number. `measure_noise_floor_per_metric()` gives it |
| Treating "within-noise" as "no effect" | It means *this campaign could not resolve it*. State the MDE |
| Reusing a floor from an earlier run | Dropout varies with load and arm count. Measure it on **this** run's control arm |
| One pooled floor for all metrics | The 2026-09-02 control arm spanned 0.0028 to 0.0747 — a 27× range |
| Quoting a mean without coverage | A mean over 40 of 64 cases is a different claim from one over 64 |
| Clearing the floor and calling it a win | A delta must clear the floor **and** the run-to-run spread; GEPA's search is stochastic |
| Comparing `tool_use_quality_v1` per-case across 2026-09-17 | The judge prompt was re-baselined that day. Aggregate comparisons are fine; per-case are not |
| Writing the report before reading the pre-registration | Answer the question that was registered |
| Hardcoding a project id, bucket, or engine id | Read from `.env`. **Never commit real project ids, SA addresses, or endpoints** |

## Red flags — stop and re-check

- **No control arm.** The report cannot classify anything. Say `UNCALIBRATED` and explain.
- **A metric at exactly 0.0 or a suspiciously round score.** Historically a coerced `None`,
  not a measurement (silent-failures #6).
- **Coverage below the case count on one side only.** The delta is measuring dropout.
- **`tool_use_quality_v1` on a run before the #12 fix landed.** Treat as carrying 12–24%
  contamination and say so.
- **n=1.** Fine to report, not fine to conclude from. Name it in the same sentence as the number.
