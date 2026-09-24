# The train/validation contrast is composition, not overfitting

**One-line result:** splitting campaign 09's per-case scores by GEPA's own train/validation
partition produces a large gap **in the arm that ran no optimization at all**, so the contrast
measures which cases are in which subset, not what the optimizer did to them.

This note exists because the number `−0.1146` was being cited in code and a plan with no source
behind it. It is measured here.

## Why anyone looked

`sampler_config.json` gives GEPA 49 of the 64 eval cases as `train_eval_case_ids` and 15 as
`validation_eval_case_ids`, while `eval_before`/`eval_after` score all 64. So every published
campaign number is in-sample. That raises a cheap question: **is the repo's signature finding —
GEPA improves its criteria and degrades the holdout — actually overfitting rather than
metric-Goodharting?**

The validation subset is a weak proxy for held-out (GEPA still uses it to *select* candidates),
but if overfitting dominated, treatment arms should improve more on train than on validation,
relative to a control that cannot overfit at all.

## Method

Campaign 09 stage artifacts, `gs://…/pipeline-runs/run-86239e1924/stages/eval_{before,after}/`,
which carry per-case scores for all six eval sides. Case ids reconstructed as
`case_{i+1}_{tier}_{category}` over `eval_cases.yaml` order — verified to reproduce all 64 ids in
every checked-in sampler config, and `case_index` in the artifacts is the 0-based position of the
same list.

Raw per-arm delta (`after − before`) averaged within each partition. **Raw, not
difference-in-differences, so all three arms are the same quantity.**

## Result

| arm | metric | train (n=49) | validation (n=15) | train − val |
| --- | --- | --- | --- | --- |
| **`c09-control`** (no optimize, no redeploy) | `safety_v1` | +0.0521 | +0.1667 | **−0.1146** |
| `c09-rationale-on` | `safety_v1` | +0.1667 | +0.2000 | −0.0333 |
| `c09-rationale-off` | `safety_v1` | +0.1378 | +0.2833 | −0.1456 |
| `c09-control` | `instruction_following_v1` | −0.0090 | −0.0272 | +0.0182 |
| `c09-rationale-on` | `instruction_following_v1` | −0.0035 | +0.1413 | −0.1448 |
| `c09-rationale-off` | `instruction_following_v1` | −0.0455 | −0.0712 | +0.0257 |

## Reading

**No evidence of overfitting, in either direction.**

On `safety_v1` all three arms have a **negative** gap — validation scored higher than train —
including the control, which ran no optimize stage and therefore cannot overfit by construction.
Overfitting predicts the opposite sign for treatments relative to control. The control's gap
(−0.1146) is larger in magnitude than one treatment's (−0.0333) and smaller than the other's
(−0.1456), i.e. the arms are not ordered by how much optimization they received.

The straightforward explanation is that the validation cases are simply easier to improve on
`safety_v1` than the training cases, for any arm. That is a property of the subsets.

**The subsets are not comparable.** The split is unstratified: train spans **18** distinct
`(tier, category)` cells, validation **11**. With n=15 on one side, a handful of cases in
easier categories moves the mean by more than any effect measured in this campaign.

## Consequences

1. **This proxy cannot answer the overfitting question**, and no amount of re-reading campaign 09
   will change that. Only a genuinely held-out, stratified test partition can.
2. **Any future train/test comparison must be stratified and control-corrected.** The readout is
   `(treatment train−test gap) − (control train−test gap)`; the control's own gap is the
   composition baseline and it is demonstrably not zero. Both constraints are built into
   [docs/plans/2026-09-23-held-out-test-set.md](../plans/2026-09-23-held-out-test-set.md).
3. **The in-sample critique still stands.** Nothing here shows the numbers are *sound* — it shows
   this particular cheap test cannot adjudicate. 77% of every reported number is still measured
   on cases the optimizer trained against.

## Caveat on an earlier presentation of these numbers

An earlier version of the plan's table reported the control arm as a raw delta and the two
treatment arms as differences-in-differences against that control, in one table without saying
so. The mixed framing did not change the conclusion — the control's own gap was the point in both
cases — but the numbers in that table are not comparable to each other. This note supersedes it;
the plan has been corrected to match.
