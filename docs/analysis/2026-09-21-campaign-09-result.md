# Campaign 09 — the primary readout is unresolved, and the control arm is why

**Date:** 2026-09-21
**Pre-registration:** [../../experiments/active/c09-rationale/doe_plan.md](../../experiments/active/c09-rationale/doe_plan.md)
**Pipeline job:** `gepa-run-86239e1924-20260917-213907` (SUCCEEDED, ~23 h)
**Engines:** three, all health-gated at reach **1.0**, all `claude-sonnet-5`
**Eval set:** 64 cases · **Knobs:** `num_runs=2`, `score_repeats=2`
**Coverage:** ~~64/64 on every arm, both sides~~ — **WRONG, corrected 2026-09-22.** Two of three arms scored **63/64** on their before side. See the correction at the end.

## Headline

**The primary contrast is null and the campaign is UNRESOLVED on it.** Rationale forwarding
on minus off, on `safety_v1`, is **−0.0044** — against a control arm that drifted **+0.0732**
on the same metric without its prompt changing. The difference is **17× inside the floor**.

The floor is the finding. DOE 03 measured `safety_v1`'s floor at **0.0082**; this control arm
produced **9× that**, and **3.3×** the 0.022 drift that made campaign 08 unresolved.

## Results

Δ = eval_after − eval_before. The control arm's prompt is byte-identical on both sides, so
its column **is** the noise floor for this run.

| metric | `on` Δ | `off` Δ | **control Δ (floor)** | `on − off` | verdict |
| --- | --- | --- | --- | --- | --- |
| `safety_v1` | +0.1607 | +0.1651 | **+0.0732** | **−0.0044** | **unresolved** — 17× inside the floor |
| `instruction_following_v1` | **+0.0208** | **−0.0398** | −0.0150 | **+0.0605** | 4.0× floor |
| `final_response_quality_v1` | −0.0954 | −0.0127 | +0.0188 | −0.0827 | 4.4× floor |
| `hallucination_v1` | −0.0296 | +0.0281 | +0.0173 | −0.0577 | 3.3× floor |
| `tool_use_quality_v1` | −0.0516 | −0.0134 | +0.0004 | −0.0382 | large, tiny floor |

## The control arm's safety drift is the most important number here

All three arms' `safety_v1` rose sharply between the two eval sides:

| arm | before | after | Δ |
| --- | --- | --- | --- |
| `rationale-on` | 0.8064 | 0.9671 | +0.161 |
| `rationale-off` | 0.8116 | 0.9767 | +0.165 |
| **`control`** | **0.8238** | **0.8970** | **+0.073** |

The control's prompt did not change. **+0.073 of that movement is not the prompt**, and it
is the largest control drift this project has measured.

Two readings, and the campaign cannot separate them:

- **A service-side shift.** The before side ran ~22:00 on 2026-09-17 and the after side
  12–20 h later. CLAUDE.md already warns that the batch-eval autorater is the Vertex service
  default — not ours, and **not recorded** — so "a silent service-side model change would
  look like a result." Every arm moving the same direction on one metric is that shape.
- **A genuine but shared effect of redeploy.** Every arm is redeployed before `eval_after`,
  including the control. Redeploy redraws the reach rate; all three still gated at 1.0, so
  dropout does not explain it, but something else about the fresh deployment might.

Either way the consequence is the same: **`safety_v1` was the wrong primary readout for this
campaign**, chosen on DOE 03's 0.0082 floor which did not hold here. That choice is mine and
it is the design error worth recording.

## The secondary metrics show a coherent trade-off — n=1, and read it as a lead

Pre-registered as secondary and under-powered, so this is a **hypothesis for the next
campaign, not a result**. Every one of these exceeds its own control drift:

**Rationale forwarding buys holdout adherence and pays for it everywhere else.**

- `instruction_following_v1`: **on +0.0208 vs off −0.0398.** The off arm reproduces the
  classic criterion/holdout regression — the pattern that is now 5-for-5 across two model
  families. **The on arm does not.** Relative to control, on sits +0.036 above and off
  −0.025 below.
- `final_response_quality_v1` (−0.083), `hallucination_v1` (−0.058), `tool_use_quality_v1`
  (−0.038): all worse with forwarding on.

If that is real, patch 4b does not make the search better — it **redirects** it, trading the
metrics GEPA is scored on for the one it is not. That would be a genuinely useful thing to
know, and it is exactly the shape a reflection signal should produce if the rationale text
tells the writer *which rubric failed*.

**Why it is only a lead:** n=1 per condition. Campaign 07 measured two runs of one manifest
differing by up to 12.3× the floor on non-safety metrics. `instruction_following_v1` was the
stable one there (−0.045 vs −0.062, spread 0.017), so a 0.0605 difference is ~3.5× that
spread — suggestive, not established.

## Against the pre-registration's decision table

| pre-registered outcome | what happened |
| --- | --- |
| `on − off` > +0.01 on `safety_v1` → patch 4b helps | **No.** −0.0044. |
| \|`on − off`\| ≤ 0.01 → no detectable effect at this power | **This one**, on the primary metric. |
| `on − off` < −0.01 → patch 4b hurts | No. |
| **control drift > the DOE 03 floors → UNRESOLVED** | **YES — +0.0732 on safety against a 0.0082 floor.** The gate tripped. |
| holdout under-powered, null expected | **Violated in the useful direction**: the holdout moved 4× its floor while the primary did not. |

**The campaign is unresolved by its own gate.** The secondary pattern is reported because
the pre-registration required reporting it, not because the gate was passed.

## What this cannot say

- **n=1 per condition.** No repeat, so GEPA's stochastic search is not separated from the
  factor. CLAUDE.md is explicit that an optimizing arm needs a repeat, not just a control.
- **One control arm, one floor.** The +0.0732 is a single measurement of a quantity that
  DOE 03 put at 0.0082 — those disagree by 9×, and this campaign cannot say which is typical.
- **Confounded with nothing else, at least.** Writer, judge, agent, seed prompt, sampler
  config, budget and knobs were held identical across arms. The one factor really is the
  one factor.
- **Prompt length is not the story.** on → 5,486 chars, off → 6,194, from the same 78-char
  seed. The arm with the *shorter* prompt did worse on three metrics.

## Infrastructure: everything worked

- Three health gates at reach **1.0**, two redeploy gates at 1.0.
- ~~**64/64 coverage on all six eval sides** — the first campaign with no dropout anywhere~~
  **This is wrong; see the correction at the end.** Four of six sides were 64/64; the
  before sides of `c09-control` and `c09-rationale-on` were **63/64**.
- **Silent failure #12: 0 losses in 253 generations, 3,576 deferred closes.** Separate
  report: [2026-09-18-silent-failure-12-fixed.md](2026-09-18-silent-failure-12-fixed.md).
- The control arm ran in the same pipeline job as the arms it calibrates, for the first time.

## What to do with this

1. **Do not use `safety_v1` as a primary readout again without re-measuring its floor on the
   day.** DOE 03's 0.0082 was measured on captures hours apart; this campaign's eval sides
   were ~16 h apart and the floor was 9× larger. The floor is not a property of the metric.
2. **Record which autorater scored each run.** This campaign cannot distinguish a service-side
   shift from a real effect, and that is now the second time it has mattered. Nothing records
   it today.
3. **The trade-off hypothesis deserves a real test**: rationale on/off, **n=2 per condition**,
   with `instruction_following_v1` and `final_response_quality_v1` as co-primary — both moved
   4× their floors here. That is a 4-arm campaign, ~40 h, and it should wait for the
   judge-quota escalation.
4. **Patch 4b stays on** in the meantime. It is not shown to help, but it is not shown to hurt
   the criterion either, and the holdout signal points the right way.

---

## Correction, 2026-09-22 — the coverage claim was wrong, the verdict is not

Re-read from this run's own stage artifacts on GCS
(`pipeline-runs/run-86239e1924/stages/eval_{before,after}/`).

**The error.** This write-up claims 64/64 on all six eval sides. Two before sides were
**63/64**:

| arm | before | after | |
| --- | --- | --- | --- |
| `c09-control` | **63/64** | 64/64 | uneven |
| `c09-rationale-on` | **63/64** | 64/64 | uneven |
| `c09-rationale-off` | 64/64 | 64/64 | even |

That matters because dropout was ruled out *on this basis* — and silent-failures #5 is
precisely about two sides scoring different case subsets producing a spurious delta.

**The correction does not change the conclusion.** Recomputing **paired over the cases present
on both sides**:

| arm | as reported | paired | shared |
| --- | --- | --- | --- |
| `c09-control` | +0.0732 | **+0.0794** | 63 |
| `c09-rationale-on` | +0.1607 | +0.1746 | 63 |
| `c09-rationale-off` | +0.1651 | +0.1719 | 64 |

Primary contrast (on − off) on `safety_v1`: **+0.0027** paired, against **−0.0044** unpaired.
Either way it is ~30x inside the control's drift. **The campaign stays UNRESOLVED**, and the
pre-registered gate still fires.

**What it does change:** the control drift is now *confirmed* to be real rather than dropout —
pairing makes it slightly larger, not smaller — so "the two sides scored different cases" is
eliminated as an explanation on correct evidence instead of incorrect evidence.

**One further number, not previously extracted.** The control's across-run spread *within*
each side, from `scores_std` at `num_runs: 2`, is **0.0018** (before) and **0.0086** (after) on
`safety_v1`. Two inference passes minutes apart agree to 0.002; the two sides sixteen hours
apart differ by 0.079 — a factor of ~44. Whatever moved was not run-to-run noise.

Follow-on analysis:
[2026-09-22-agent-side-floor.md](2026-09-22-agent-side-floor.md).
