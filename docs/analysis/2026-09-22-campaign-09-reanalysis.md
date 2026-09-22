# Campaign 09, re-analysed per case — three results that were already paid for

Campaign 09 was filed **UNRESOLVED**. Its stage artifacts carry per-case scores for all six
eval sides, and nothing had used them: every number in the write-up is an arm-level mean. Read
per case, the same run yields a confirmed null *with an interval*, a measured optimization
effect against a proper control, and a four-metric trade-off that the write-up could only call
"a lead".

No new compute. Source: `gs://…/pipeline-runs/run-86239e1924/stages/eval_{before,after}/`.

## Method

Every comparison is **paired on `case_index`** and restricted to cases present on all sides
involved, which is what the coverage correction of 2026-09-22 showed was necessary — two of
six sides were 63/64, not 64/64 as the write-up claimed.

- **DiD** (arm vs control): per case, `(treat_after − treat_before) − (ctrl_after − ctrl_before)`
- **Contrast** (on vs off): per case, `(on_after − on_before) − (off_after − off_before)`; the
  control cancels
- **95% CI**: bootstrap over cases, 10,000 resamples. `safety_v1` is quarter-valued
  (0.25/0.5/0.75/1.0), so the per-case distribution is lumpy and a t-interval would be
  optimistic.

## Result 1 — GEPA improved `safety_v1` by +0.095 over an unoptimized control

| arm | DiD vs control | 95% CI |
| --- | --- | --- |
| `rationale-on` | **+0.0952** | [+0.0119, +0.1786] |
| `rationale-off` | **+0.0952** | [+0.0079, +0.1825] |

At case level, which is the honest view of a 4-valued metric:

| arm | improved | worsened | net |
| --- | --- | --- | --- |
| control | 24 | 9 | **+15** |
| `rationale-on` | 37 | 4 | **+33** |
| `rationale-off` | 38 | 5 | **+33** |

The optimized arms fixed roughly twice as many net cases as the unoptimized control, and broke
**half as many**. **16 cases improved under both optimized prompts and not under the control.**

**This is the first optimization effect this project has measured against a control**, and the
campaign was not designed to produce it — the control was specified as a noise threshold, and
using it as a baseline is what the DiD does.

**The two arms are not independent replicates.** Both deltas are exactly `11/63` and the
control's exactly `5/63`; the arms moved the same net number of quarter-steps because they
fixed overlapping case sets (23 of ~37 shared). Read the agreement as "the optimizer finds the
same fix twice", not as two independent confirmations.

## Result 2 — the pre-registered primary is null, now with an interval

| | `safety_v1`, on − off |
| --- | --- |
| as reported (arm means) | −0.0044 |
| **per case, paired** | **+0.0000, 95% CI [−0.0714, +0.0714]** |

Campaign 09 called this unresolved because the *control* drifted. Per case it is simply
**null**, and the interval says how null: the data excludes an effect larger than ±0.071.
Given the pre-registered MDE reasoning, that is the answer to the question the campaign asked.

## Result 3 — rationale forwarding trades the holdout against everything else

The same contrast on the other four metrics. The write-up saw this pattern in arm means and
correctly called it "a lead, not a result". Per case, all four intervals exclude zero:

| metric | on − off | 95% CI | |
| --- | --- | --- | --- |
| `instruction_following_v1` | **+0.0768** | [+0.0122, +0.1416] | the holdout, **improved** |
| `final_response_quality_v1` | **−0.0752** | [−0.1380, −0.0131] | degraded |
| `hallucination_v1` | **−0.0499** | [−0.0996, −0.0007] | degraded |
| `tool_use_quality_v1` | **−0.0450** | [−0.0893, −0.0052] | degraded |

ADK patch 4b buys the metric GEPA is *not* scored on and pays for it on three that it is —
a coherent story rather than scattered noise, and the direction matches the write-up's
qualitative reading exactly.

## What these intervals do NOT cover — read this before acting

**The CIs bound case-sampling noise only.** They say a difference is not an artefact of which
64 cases were used. They say **nothing** about the dominant uncertainty, which is *which prompt
GEPA happened to find*: CLAUDE.md records two runs of one manifest, same seed and budget,
differing by up to **12.3× the control-arm floor**. There is one run per condition here.

So Result 3 is a **much stronger lead**, not a result. It still needs n=2 per condition, which
is exactly what the write-up asked for.

Three further limits:

- **Result 1 confounds optimize with redeploy.** The treatment arms redeployed; the control did
  not. Both redeploys gated at 1.0 reach so it is not dropout, but +0.095 is
  optimize-plus-redeploy versus neither.
- **`safety_v1` is quarter-valued.** Means over 63 cases land on exact fractions and small
  differences are discretisation, not signal. The case counts are the more trustworthy view.
- **Two of six sides were 63/64.** Pairing handles it; the unpaired numbers in the original
  write-up do not.

## What to do

1. **Campaign 09 should be re-filed as "primary null, secondary lead strengthened"**, not
   UNRESOLVED. The gate that produced UNRESOLVED — *control drift > floor* — discards a usable
   result when the drift is common-mode, which here it demonstrably was: all three arms rose
   together from ~0.81.
2. **Report per-case paired contrasts by default.** Every number in this note came from
   artifacts that already existed; the arm-level means threw the resolution away.
3. **Campaign 10 should test the trade-off directly**, n=2 per condition, with
   `instruction_following_v1` and `final_response_quality_v1` co-primary — which is what the
   original write-up recommended, now with effect sizes to power it: ±0.075 is the size to
   design for.
4. **Do not re-use the control as a threshold without checking whether its drift is
   common-mode.** If every arm moves together, the contrast removes it and the threshold is
   the wrong instrument.
