# The holdout regression did not occur — once, with three things changed

**Date:** 2026-09-17
**Job:** `gepa-run-31dde9454d-20260917-011939` · `run-31dde9454d` · 11 h 27 m
**Manifest:** `manifests/m01-rationale-merge_manifest.yaml`
**Acceptance test for:** PR #95 (rubric rationale forwarded to GEPA's reflector, `use_merge=True`)

## Result

```
instruction_following_v1   0.8402 -> 0.8467   Δ = +0.0065
```

| arm | Δ holdout | writer | rationale | merge |
| --- | --- | --- | --- | --- |
| c07-sonnet5 A | −0.045 | `gemini-2.5-flash` | no | no |
| c07-sonnet5 B | −0.062 | `gemini-2.5-flash` | no | no |
| c08-new-r1 | **−0.068** | `gemini-2.5-flash` | no | no |
| c08-new-r2 | −0.036 | `gemini-2.5-flash` | no | no |
| **m01 (this run)** | **+0.0065** | `claude-opus-4-8` | **yes** | **yes** |

**Five prior arms ranged −0.036 to −0.068 and never once came out positive.** This one is
slightly positive, outside the baseline range by roughly the width of that range.

Full metric set:

| metric | before | after | Δ |
| --- | --- | --- | --- |
| `safety_v1` (criterion) | 0.8382 | 0.9657 | **+0.1275** |
| `final_response_quality_v1` | 0.8958 | 0.9121 | +0.0163 |
| **`instruction_following_v1` (holdout)** | 0.8402 | 0.8467 | **+0.0065** |
| `tool_use_quality_v1` | 0.9773 | 0.9735 | −0.0039 |
| `hallucination_v1` | 0.9386 | 0.8633 | **−0.0753** |

**The criterion gain survived** (+0.128 against +0.131/+0.154 on the c08 arms), so this is
not the optimizer simply doing less work.

**`hallucination_v1` fell 0.075**, which is new — it never moved outside its floor in
campaigns 07 or 08. One arm, no control, and it is not the pre-registered outcome, so it is
recorded and not interpreted. If a follow-up runs, watch it.

## Data quality

| | |
| --- | --- |
| coverage | **64/64 both sides** |
| deploy health gate | passed, rate 1.0 |
| redeploy health gate | passed, rate 1.0 |
| `run_id` | `run-31dde9454d`, distinct from c08's — nothing cache-hit |
| optimize | 602 min, 113 generations, budget 600 metric calls |
| cost | ~$0.47 optimize (est.) |

No dropout on either side, so the delta is not measuring engine failure.

## What this establishes, and what it does not

**It clears the bar.** A holdout moving from consistently ≈−0.05 to slightly positive is the
size of change this n=1 design could read. The pre-registered reading was: near −0.068 means
nothing changed; near zero or positive is worth a controlled follow-up. It is the latter.

**It cannot say which change did it.** Three boundaries moved together — rationale
forwarding, `use_merge`, and the writer moving to `claude-opus-4-8`. Sequencing was
recommended and not taken, so the honest claim is *"the three together removed the regression
on one arm"*.

**Narrowed to two on 2026-09-17.** `use_merge` is not merely unlucky here, it is
*structurally inert* — see below — so it cannot have contributed. The confound is
**rationale forwarding + the writer model**. That is a real tightening of the attribution
and it does nothing for the n=1 problem.

**Two things argue against over-reading it:**

- **n=1** against a baseline whose own within-condition spread is 0.032 (c08's two arms:
  −0.068 and −0.036). +0.0065 is about 1.3 spreads above the nearest baseline arm.
  Suggestive, not significant.
- **`use_merge` contributed nothing mechanically, and cannot.** The logs show **19 ×
  `No merge candidates found` and zero successful merges** — GEPA attempted merges (which
  only happens when the flag is on, so the injection works) and never found an eligible
  pair. Followed up 2026-09-17 with `scripts/check_merge_eligibility.py`, which runs gepa's
  own `does_triplet_have_desirable_predictors` over a real candidate tree: **34 pairs, all
  34 sharing a common ancestor, 0 eligible.**

  The reason is structural, not luck. Merge is *field-wise recombination across multiple
  predictors* — it requires some predictor where one parent is byte-identical to the common
  ancestor and the other differs, so there is something to recombine. We optimize **one**
  predictor (`agent_prompt`), which makes that demand a descendant whose prompt equals its
  ancestor's; a descendant exists *because* the prompt was mutated. Zero byte-identical
  candidate pairs in the run, as expected.

  **So the published 9.2×-shorter-prompt result does not transfer** — it is measured on
  multi-module programs with separate prompts. The flag was shipped on the strength of that
  number without checking the predictor count first, which is the error worth remembering
  here: the claim was true and about a different configuration.

### It is further evidence against the verbosity hypothesis

The prompt grew **78 → 5,106 characters** — *longer* than campaign 07's 3,873 and far longer
than c08-new-r1's 1,493 — while the holdout **improved**. That is the opposite direction to
the prompt-length hypothesis, which was already
[retracted](2026-09-16-writer-model-survey.md) for lack of support. Two independent lines now
point the same way.

## Silent failure #12, unchanged

**18 toolset losses in 113 generations (16%)**, against campaign 08's 12% and 24% and
campaign 07's 14%. 1,797 `Closing toolset` events, the same shape as ever. Nothing in this
run addressed #12 and nothing in it moved. `tool_use_quality_v1` remains uninterpretable.

## What would attribute it

A two-condition run varying **only** the rationale, n=2 per condition. `score_repeats`
(shipped 2026-09-17) now exists to buy down the judge-noise floor that made campaign 08
unresolvable — DOE 02 measured that floor as essentially all judge on this very metric, and
scoring repeats cost ~2.8 min each against ~5.4 min for a full run.

That is a real campaign and should wait for the judge-quota escalation.

**Until then, treat this as one arm that came out the right way, not as a demonstrated fix.**
