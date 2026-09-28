# On/off validation: continuous scoring does not select better prompts — do not adopt

**Run:** `run-413630e488` (`manifests/onoff-validation_manifest.yaml`), submitted 2026-09-25
13:20 UTC, succeeded 2026-09-26 05:10 UTC. Three arms of `claude-sonnet-5` from one 78-character
seed prompt: `onoff-continuous` (GEPA selects on continuous per-case scores, option A),
`onoff-binary` (ADK's pass/fail collapse) and `onoff-control` (no optimize stage). Both optimized
arms share the pinned GEPA seed `20260925`, `max_metric_calls: 400`, `num_runs: 2`,
`score_repeats: 2`, and no patience.

**Verdict: do not adopt continuous scoring as a default.** The pre-registered rule was that only a
clear win justifies the re-baselining boundary adoption costs. There is no win on any quality
metric. The primary contrast is null on three metrics, leans *against* continuous on `safety_v1`
and favours it on `tool_use_quality_v1` only because the binary arm regressed there. Option A
stays in the code, opt-in, as it was.

Reproduce every number below with `scripts/onoff_contrast.py`.

## Setup checks: the contrast is the one intended

- Both optimize stages logged `GEPA seed: 20260925 (PINNED by manifest)`. Only
  `onoff-continuous` logged `Continuous per-case scoring ON`.
- The control's optimize artifact carries `control_arm: True` and a byte-identical 78-character
  prompt.
- All three deploys and all three redeploys passed the health gate with no rerolls.
- Coverage: 63/64 on the two optimized arms' `eval_before`, 64/64 on the other four sides. 63 cases
  are common to all six, and every contrast below is paired over them.
- Zero `will run without the tools` losses on either optimize stage.

**One departure from CLAUDE.md:** the control arm *was* redeployed, in place on the same engine
(`optimize` 2 min, `redeploy` 4.9 min, same prompt). CLAUDE.md records that the control branch
skips redeploy, which was true of campaign 09. Here it makes the control symmetric with the treated
arms — its after side is a redrawn engine too — which is the better design, but it means this
control's delta includes an engine redraw that campaign 09's did not.

## Method

Same as the campaign 09 reanalysis: pair on `case_index`, restrict to cases on all six sides.

- **Primary contrast** (continuous vs binary): per case,
  `(cont_after − cont_before) − (bin_after − bin_before)`.
- **Difference-in-differences** (arm vs control): the same, with the control as baseline.
- **95% CI**: bootstrap over cases, 10,000 resamples. `*` marks an interval excluding zero.

## Result 1: the primary contrast finds no win

| metric | continuous − binary | 95% CI | cases better / worse / tied |
| --- | --- | --- | --- |
| `final_response_quality_v1` | +0.0061 | [−0.0731, +0.0864] | 24 / 34 / 5 |
| `hallucination_v1` | −0.0126 | [−0.0607, +0.0376] | 26 / 37 / 0 |
| `instruction_following_v1` (holdout) | +0.0022 | [−0.0755, +0.0783] | 28 / 35 / 0 |
| `safety_v1` | **−0.0595** | [−0.1349, +0.0159] | 13 / 28 / 22 |
| `tool_use_quality_v1` | **+0.0377 \*** | [+0.0040, +0.0734] | 20 / 9 / 34 |

Interval half-widths are 0.034–0.080, consistent with the preflight MDE of 0.060–0.103: this
design could see a large effect and did not.

**The tool-use gain is not a continuous-scoring win.** It comes entirely from the binary arm
getting worse: continuous vs control is +0.0016, binary vs control is −0.0361
[−0.0774, +0.0020]. Its lower bound is +0.004 in one of five metrics, so it would not survive
any correction for multiple comparisons. Read it as "binary may have cost a little tool use", not
as "continuous improves tool use".

**Safety leans the other way.** The difference-in-differences interval includes zero, but the
direct after-side comparison — valid here because both arms start from the same prompt — is
−0.0317 [−0.0556, −0.0119] and excludes it. The binary arm's `eval_after` scored `safety_v1` 1.0
on every case, and continuous lost 28 cases to binary's 13 on the paired contrast.

## Result 2: GEPA beat the control on safety, with either signal

| metric | continuous − control | binary − control |
| --- | --- | --- |
| `safety_v1` | **+0.1310 \*** [+0.0317, +0.2302] | **+0.1905 \*** [+0.1071, +0.2738] |
| `final_response_quality_v1` | +0.0358 [−0.0335, +0.1104] | +0.0298 [−0.0361, +0.0985] |
| `instruction_following_v1` | +0.0337 [−0.0441, +0.1079] | +0.0315 [−0.0319, +0.0971] |
| `hallucination_v1` | +0.0090 [−0.0312, +0.0498] | +0.0216 [−0.0294, +0.0694] |
| `tool_use_quality_v1` | +0.0016 [−0.0381, +0.0397] | −0.0361 [−0.0774, +0.0020] |

This is the second measurement of an optimization effect against a control, after campaign 09's
+0.0952 on both arms. It is larger here and resolves on both arms. **The holdout did not regress**:
`instruction_following_v1` is +0.03 on both arms against the control, interval spanning zero —
unlike campaigns 07 and 09, where GEPA improved safety at the holdout's expense.

The control moved −0.0079 on `safety_v1` across its two eval sides. Campaign 09's control moved
+0.0732, with all three arms rising together. There was no common-mode drift this time.

## Result 3: the two searches, from the optimize logs

| | binary | continuous |
| --- | --- | --- |
| elapsed | 328 min | 341 min |
| iterations | 40 | 32 |
| candidates promoted to full validation | 7 | 8 |
| iterations skipped, every minibatch case already perfect | **26** | **10** |
| new bests found at | iterations 1, 12, 31 | iterations 1, 2; unbeaten for 30 more |
| winning prompt | 7,216 chars | 5,191 chars |

Continuous scoring did what #133 said it would to the *search*. All-perfect skips fell from 26 to
10. Binary ran more iterations only because a skip is cheap, so it spent 26 of its 40 iterations
learning nothing. **It reduced saturation but did not remove it.** Ten iterations were still
skipped because all three minibatch cases scored 1.0 on every metric: a case with no headroom has
none under either signal. And continuous found its winner at iteration 2, while binary made its
biggest step late, at iteration 31. That late gain is the shape `run-d55b159050` showed under
continuous scoring.

So the finer signal changed how GEPA searched and which prompt it kept, and the kept prompt was
not better on the 64-case eval. That is the question this run existed to answer, and it matches
the `r = +0.071 / +0.274` finding: a different ranking, not a sharper one.

## The canary, and why it is only approximate here

The canary re-scores the same frozen 64 responses on every eval side. Its six readings span
0.951–0.991 on `safety_v1` and 0.811–0.857 on `instruction_following_v1`, but coverage varied
from 36 to 64 cases per metric and **the artifact stores per-metric means, not per-case scores**,
so the readings cannot be restricted to common cases. The four readings with full 64/64 `safety_v1`
coverage span 0.959–0.979, a spread of 0.020 over 14 h. That is far below the +0.13 and +0.19 safety
effects, so judge drift does not explain them. It cannot be stated more precisely than that.

**Follow-up:** record per-case canary scores so drift can be paired, as every other comparison
here is. Scoring the canary at `score_repeats: 2` would also recover the coverage, the same way it
does for the eval sides.

## What this does not cover

- **One run per arm.** The pinned seed gives both arms the same minibatch schedule, but different
  scores send the searches down different paths from iteration 1. The run-to-run spread CLAUDE.md
  measures at 12.3× the control floor is not averaged out by a single pair. A replicate could move
  the safety lean either way. The absence of a win is the robust part.
- **One model and one seed prompt.** A 78-character seed saturates quickly under both signals. A
  richer seed prompt, or a harder eval set with more headroom, is where a finer selection signal
  would have the most to gain.
- **The lexicographic variant** (pass count first, continuous mean as tie-break) was not tested.
  It preserves binary ordering while breaking ties, and is the remaining version of option A with a
  plausible case. It would need its own validation run.
