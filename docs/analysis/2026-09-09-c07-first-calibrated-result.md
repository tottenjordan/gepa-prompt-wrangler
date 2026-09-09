# Campaign 07 — first calibrated result

**Arm:** `c07-sonnet5` (`claude-sonnet-5`, optimizing) · **Control:** `c07-ctrl-sonnet5`
(same model, byte-identical prompt, run concurrently) · **`num_runs`:** 3 · 64 cases

**Generated:** 2026-09-09 · **Corrected:** 2026-09-09, see *Correction* below.
**Status:** campaign stopped after batch 1; 1 of 4 optimizing arms complete.

![Per-metric deltas against their floor and against run-to-run spread](2026-09-09-c07-first-calibrated-result.png)

## Correction

An earlier version of this document reported **one metric improved and four regressed**,
from an average delta of -0.0109. Those numbers came from a run of this manifest whose
artifacts **no longer exist**: a second run of the same manifest overwrote them in place
at 17:11 and 17:52 UTC the same day (silent-failures #14, which fired *again* while this
document was being written).

Nothing was wrong with the original reading of the run it described. But that run is
unreproducible, and the artifacts a reader would check now say something materially
different. This version reports the **surviving** run as primary and keeps the lost run as
a second sample — which turns out to be the most useful thing in the whole result.

Both runs share `eval_before` (2026-09-08 14:56, a KFP cache hit that was never rewritten),
so they are directly comparable: same baseline, same seed, same model, same criteria, same
budget. **Only `optimize` and `eval_after` differ, and they differ because GEPA's search is
stochastic.** Both runs' artifacts are now archived under
`pipeline-runs/archive/run-8a5905dee0-2026-09-09/`.

## Headline

**One result survives: `safety_v1`, +0.157.** Everything else is either inside its noise
floor or inside the gap between two runs of the identical manifest.

The control-arm floor is **not sufficient** to judge a prompt-optimization result. It is
measured with the prompt held fixed, so it captures *evaluation* noise only. GEPA's search
is itself stochastic, and on three of five metrics the run-to-run spread is **larger than
the effect** — up to **12.3x the floor** on `hallucination_v1`.

## Per-metric: the surviving run, against two different rulers

| metric | before | after | delta | floor | Δ/floor | run-to-run spread | spread/floor | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `safety_v1` | 0.8063 | 0.9631 | **+0.1568** | 0.0417 | 3.8x | 0.0032 | 0.1x | **survives** |
| `tool_use_quality_v1` | 0.9693 | 0.9898 | **+0.0205** | 0.0164 | 1.2x | 0.0750 | 4.6x | not reproducible |
| `hallucination_v1` | 0.9414 | 0.9586 | **+0.0172** | 0.0077 | 2.2x | 0.0950 | 12.3x | not reproducible |
| `final_response_quality_v1` | 0.8902 | 0.8843 | **-0.0059** | 0.0108 | 0.5x | 0.0250 | 2.3x | within floor |
| `instruction_following_v1` | 0.8416 | 0.7801 | **-0.0615** | 0.0151 | 4.1x | 0.0167 | 1.1x | not reproducible |

*Floor* is the concurrent control arm's own movement (larger of paired/unpaired — see
Appendix). *Run-to-run spread* is |run A − run B| on `eval_after`. A delta only counts as a
result if it clears **both**.

Average delta: **+0.0254** (surviving run) against **-0.0109** (lost run). The
average is not a quantity worth reporting — the two runs disagree on its *sign*.

## The two runs, side by side

Identical manifest, identical 78-character seed prompt, identical budget
(`max_metric_calls: 600`), identical `num_runs: 3`, same cached `eval_before`.

| metric | run A delta (lost) | run B delta (surviving) | spread | spread / floor |
| --- | --- | --- | --- | --- |
| `hallucination_v1` | -0.0778 | +0.0172 | 0.0950 | 12.3x |
| `tool_use_quality_v1` | -0.0545 | +0.0205 | 0.0750 | 4.6x |
| `final_response_quality_v1` | -0.0309 | -0.0059 | 0.0250 | 2.3x |
| `instruction_following_v1` | -0.0448 | -0.0615 | 0.0167 | 1.1x |
| `safety_v1` | +0.1536 | +0.1568 | 0.0032 | 0.1x |

The optimized prompts differ as much as the scores: **6,067 characters (run A) against
3,873 (run B)**, from the same 78-character seed.

`safety_v1` is the exception that makes the rest legible — the two runs agree to within
0.0032, a tenth of its floor, on a +0.157 move. That is what a real effect looks like here.
`hallucination_v1` moved -0.078 in one run and +0.017 in the other; reporting either as a
finding would have been an artifact of which run happened to survive.

**This is n=2.** Two runs bound the variation better than one does, but they do not measure
it. The honest statement is that run-to-run variation on this configuration is *at least*
this large, not that it is this large.

## What this means for the method

- **Every optimizing arm needs repeats, not just a control.** CLAUDE.md's rule — that a
  sweep carries a control arm whose prompt does not change — is necessary and, on this
  evidence, insufficient. A control bounds evaluation noise; it says nothing about the
  optimizer's own variance, which is the larger term here.
- **`num_runs: 3` does not address this.** It averages the *evaluation* of one optimized
  prompt. It does not average over *which* prompt the search lands on.
- **Reporting an average across metrics is actively misleading.** The two runs disagree on
  its sign while agreeing closely on the one metric that actually moved.

## Caveats

- **Coverage.** eval_before 64/64; eval_after 63/64 (run B) and 62/64 (run A). The control
  was clean at 64/64 on both sides, so the floor omits a source of error the arms have.
- **~14% of GEPA's own generations scored a toolless agent.** The concurrent `c07-pro` arm
  logs `will run without the tools` 16 times across 111 generations, and its internal
  counter reports **zero** because it matches a different string (silent-failures #12).
  `tool_use_quality_v1` should be read with that in mind on any arm.
- **One control arm.** Campaign 06 ran four and found per-metric floors varying 3.4x
  between them.
- **`c07-pro` was still running** when this was written and would add a second model on the
  same seed and criteria.

## Appendix — `uv run wrangler floor run-70166a6bc8 --markdown`

Verbatim, so the floor traces to a command rather than to a paste. The control arm's
artifacts (written 14:03 and 14:28 UTC) were not affected by the overwrite.

```
(see wrangler floor run-70166a6bc8 --markdown)
```
