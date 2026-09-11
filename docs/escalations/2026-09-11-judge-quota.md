# Quota increase: `gemini-3.5-flash` at 5 RPM is the ceiling on every campaign

**Status:** ready to file · **Filed:** _(not yet)_ · **Case:** _(none)_
**Type:** Google Cloud quota increase — not a defect report
**Requested by:** GEPA Prompt Wrangler team
**Date of measurement:** 2026-09-09 to 2026-09-11
**Supporting analysis:** [../analysis/2026-09-10-campaign-07-wrapup.md](../analysis/2026-09-10-campaign-07-wrapup.md)

---

## The ask, in one line

Raise online-prediction requests/minute for **`gemini-3.5-flash`** in project
`<GCP_PROJECT_ID>`, region **`global`**, from **5** to **60**.

Everything below is the justification. Nothing here is a bug report — the service is
behaving as configured; the configuration is the problem.

## Why 5 RPM is the binding constraint on the whole project

This repo optimizes agent prompts with GEPA. Each optimization run scores hundreds of
candidate prompts, and every score is an autorater call against `gemini-3.5-flash`. Three
measurements, all from production runs:

**1. Optimization is 87% of every run.** Measured per stage on campaign 07's `c07-pro` arm,
from its own artifacts:

| stage | wall clock | % of run |
| --- | --- | --- |
| deploy | 15m | 2% |
| eval_before | 41m 38s | 7% |
| **optimize** | **8h 32m** | **87%** |
| redeploy | 8m | 1% |
| eval_after | 34m 12s | 6% |

**2. Optimization is judge-throttled, not compute-bound.** A **single** arm logged
**70 × HTTP 429** across 52 generations. An earlier campaign running **two** arms logged 76
— barely more, because the quota is shared and fixed. The agent model
(`claude-sonnet-5`) sits at **2000 RPM** and is nowhere near its limit; all of the
throttling is on the judge.

**3. Concurrency cannot route around it.** Because throughput is fixed, running two arms in
parallel makes each take roughly twice as long. We measured this rather than assuming it,
and abandoned the parallel design as a result.

## What it costs us today

A campaign is 4 optimizing arms plus controls. At 5 RPM that is **~44 hours of wall clock**
for a single experiment, which forces the experiment to be smaller than the question
deserves — we have already had to drop a control condition from one design purely to fit
the clock, weakening the inference.

## Why 60

From measured demand, not a round number.

The 8h 32m optimize stage consumed a budget of 600 metric calls. Each metric call fans out
to roughly four autorater calls — two rubric-based metrics plus safety and hallucination —
so approximately **2,400 judge requests in 512 minutes ≈ 4.7 requests/minute**, which is
the 5 RPM limit, saturated, for eight and a half hours.

- **60 RPM** puts the same stage at roughly **45 minutes**, making a full campaign an
  afternoon instead of two nights.
- It also leaves headroom for the two-arm concurrency the current limit makes pointless.

We are not asking for a large absolute number. 60 RPM is a modest allocation; it is 12× only
because the starting point is unusually low for a production workload.

## Fallback we have already considered and rejected

`gemini-2.5-flash` carries **100 RPM** in the same project and is cheaper per token. We are
not switching to it, for two reasons worth stating so the ask is not answered with "use the
other model":

- It **retires 2026-10-16**, roughly five weeks out.
- In our current experiment the judge *scores the rubrics under test*, so changing the judge
  changes the instrument that reads the treatment. A less discriminating autorater shrinks
  the measured effect for reasons unrelated to what we are measuring — a false null, which
  is the one wrong answer that looks like a clean result.

Every other Gemini 3.x model available to us is also at 5 RPM, so there is no in-family
alternative.

## Where to file

Google Cloud Console → **IAM & Admin → Quotas & System Limits** → filter service
`aiplatform.googleapis.com`, metric *online prediction requests per minute per base model*,
region `global`, base model `gemini-3.5-flash` → **Edit Quotas**.

Paste "The ask" and "Why 60" into the justification field. The two tables are the evidence a
reviewer will want.
