# Campaign 09 (DOE 12) — does forwarding the judge's rationale help?

**Status:** RUNNING — submitted 2026-09-17 21:39 UTC
**Pipeline job:** `gepa-run-86239e1924-20260917-213907`  ·  run id `run-86239e1924`
**Manifest:** [`manifests/c09-rationale_manifest.yaml`](../../../manifests/c09-rationale_manifest.yaml)
**Plan:** [`docs/plans/2026-09-17-campaign-09.md`](../../../docs/plans/2026-09-17-campaign-09.md)

**Written and committed before the run.** Everything below is a commitment, not a summary.

## Question

On 2026-09-17, m01 moved the holdout +0.0065 while shipping three changes at once:
rationale forwarding (ADK patch 4b), `use_merge=True` (patch 7), and the writer moving to
`claude-opus-4-8`. Patch 7 has since been shown **structurally inert** — merge recombines
fields across *multiple* predictors and we optimize one — so the confound is down to two.

**Does patch 4b do anything?**

## Design

One Vertex AI Pipelines job, three arms, serialized by `ParallelFor(parallelism=1)` so they
never contend for the 5 RPM judge.

| arm | `forward_rationale` | optimize? | purpose |
| --- | --- | --- | --- |
| `c09-rationale-on` | true | yes | reproduces m01's configuration |
| `c09-rationale-off` | **false** | yes | the contrast — upstream ADK behaviour |
| `c09-control` | n/a | **no** | noise floor; prompt byte-identical both sides |

Held constant: agent (`sonnet_agent`, `claude-sonnet-5`), writer (`claude-opus-4-8`), judge,
sampler config, eval set (64 cases), seed prompt (byte-identical across arms),
`max_metric_calls: 600`, `num_runs: 2`, `score_repeats: 2`, `use_merge` on (inert; leaving it
keeps the comparison to m01 exact).

**One factor, deliberately.** The writer move was justified independently — ADK's implicit
default was inside 30 days of retirement, and the writer must differ from both the judge and
every enabled agent — so we would not revert it whatever a 2×2 showed. A factor nobody will
act on is not worth doubling 24 h of compute.

## Pre-registered outcomes

**Primary: the difference in `safety_v1` Δ between `rationale-on` and `rationale-off`,**
against the control arm's floor.

Resolvable: floor **0.0082**, measured effects **+0.154/+0.131**, reproduced within **0.003**
across two runs of one manifest. A between-arm difference above ~0.01 is real at n=1.

| outcome | reading |
| --- | --- |
| on − off > +0.01 on `safety_v1` | Patch 4b helps. Keep it, and the m01 delta is at least partly attributable to it. |
| \|on − off\| ≤ 0.01 | No detectable effect **at this power**, on this metric. Patch 4b's justification reverts to its mechanism, not its measured effect. |
| on − off < −0.01 | Patch 4b *hurts*. Withdraw it; the rationale text is noise in the reflection prompt rather than signal. |
| control drift > the DOE 03 floors | **Unresolved.** Report it as campaign 08 was, and do not read the arms. |

**Secondary, and pre-registered as UNDER-POWERED: the same difference on
`instruction_following_v1`.** MDE ≈ **0.0152** against an expected effect of ~0.0065 — DOE 03
measured its scaling exponents at 0.20 (`num_runs`) and −0.06 (`score_repeats`), so no budget
these knobs buy resolves it. **A null here is not evidence of no effect** and must not be
written up as one. State the MDE beside the number every time it appears.

**Silent failure #12 acceptance**, on both optimize stages:

1. `will run without the tools` = **0**, counted with
   `scripts/analyze_toolset_loss.py --freshness 90d`, against a 12–24% baseline.
2. **The printed deferred-close count must be non-zero.** `Deferred 0 toolset close(s)` means
   patch 8 never engaged and the #12 result is **void**, not clean.
3. Do **not** read the surviving `Closing toolset` lines as a failure — they persist by design.

## Rules

- **n=1 per condition.** Justified only because `safety_v1` reproduced at ±0.003; it is not
  justified for anything else, which is why the holdout is secondary.
- **No arm is extended, re-run, or dropped because it looks interesting.**
- The control arm **gates** the read.
- Report **per-metric** floors, never one pooled number.

## Operational notes

- `cache_bust: "c09-v1"` is **required**. `optimizer.py` rides in the code tarball, not a
  component body, so an unchanged `run_id` would cache-hit a pre-patch-8 optimize stage and
  the #12 measurement would measure the cache.
- Engines carry `lifecycle: ephemeral`, `campaign: "09"`. **Reap them as the last step.**
- Expect ~24 h: ≈11 h per optimize arm (577 min GEPA, ~12 min deploy gate, ~28 min per eval
  side at (2,2), ~12 min redeploy gate) plus ~1.3 h for the control, serialized.

## Result

_Pending._
