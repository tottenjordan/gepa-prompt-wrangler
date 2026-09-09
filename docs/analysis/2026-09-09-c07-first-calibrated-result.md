# Campaign 07 — first calibrated result

**Arm:** `c07-sonnet5` (`claude-sonnet-5`, optimizing) · **Control:** `c07-ctrl-sonnet5`
(same model, byte-identical prompt, run concurrently) · **`num_runs`:** 3 · 64 cases

**Generated:** 2026-09-09 · **Corrected:** 2026-09-09, see *Correction* below.
**Status:** campaign stopped after batch 1; 1 of 4 optimizing arms complete.

![Deltas against both the noise floor and the run-to-run spread](2026-09-09-c07-first-calibrated-result.png)

## Correction

An earlier version reported **one metric improved and four regressed**, average -0.0109.
Those numbers came from a run whose artifacts **no longer exist**: the next run of the same
manifest overwrote them in place at 17:11 and 17:52 UTC the same day — silent-failures #14,
firing again while the document describing it was being written.

The original reading was not wrong about the run it described. But that run is
unreproducible, and the artifacts a reader checks now say something different. This version
reports the **surviving** run as primary and keeps the lost run as a second sample, which
turns out to be the most valuable thing here.

Both runs share `eval_before` (2026-09-08 14:56, a KFP cache hit never rewritten), so they
are exactly comparable: same manifest, seed, model, criteria and budget, differing only in
GEPA's stochastic search. Both are archived under
`pipeline-runs/archive/run-8a5905dee0-2026-09-09/`.

## Headline

**Two results survive, and they point the same way.** `safety_v1` improved **+0.157** and
`instruction_following_v1` regressed **-0.062**. Both clear their noise floor *and* the gap
between the two runs; the other three do not.

The pattern is not subtle once the holdout is marked: **GEPA improved a criterion it was
scored on and degraded the one metric absent from its criteria.** The sampler config gates
on safety, final-response-quality, tool-use and hallucination. `instruction_following_v1`
is not among them, and it is the metric that reproducibly got worse.

That independently reproduces
[2026-08-22](2026-08-22-first-optimization-sweep.md), which found the same thing and
attributed it to instruction-following being a holdout GEPA never optimizes. Two campaigns,
different models, same result.

## Per-metric: judged against two rulers

A delta counts only if it exceeds **both** its noise floor (evaluation noise, from the
concurrent control) and the run-to-run spread (optimizer noise, from the two runs).

| metric | in GEPA criteria? | before | after | delta | floor | run-to-run spread | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `safety_v1` | criterion | 0.8063 | 0.9631 | **+0.1568** | 0.0417 | 0.0032 | **survives — improved** |
| `tool_use_quality_v1` | criterion | 0.9693 | 0.9898 | **+0.0205** | 0.0164 | 0.0750 | swamped by run-to-run |
| `hallucination_v1` | criterion | 0.9414 | 0.9586 | **+0.0172** | 0.0077 | 0.0950 | swamped by run-to-run |
| `final_response_quality_v1` | criterion | 0.8902 | 0.8843 | **-0.0059** | 0.0108 | 0.0250 | within floor |
| `instruction_following_v1` | **holdout** | 0.8416 | 0.7801 | **-0.0615** | 0.0151 | 0.0167 | **survives — regressed** |

Average delta: **+0.0254** here against **-0.0109** in the lost run. The two runs
disagree on its *sign*, so the average is not worth reporting.

## Why the control arm is not enough

Identical manifest, identical 78-character seed, identical budget (`max_metric_calls: 600`),
identical `num_runs: 3`, shared cached `eval_before`:

| metric | run A delta (lost) | run B delta (surviving) | spread | spread / floor |
| --- | --- | --- | --- | --- |
| `hallucination_v1` | -0.0778 | +0.0172 | 0.0950 | 12.3x |
| `tool_use_quality_v1` | -0.0545 | +0.0205 | 0.0750 | 4.6x |
| `final_response_quality_v1` | -0.0309 | -0.0059 | 0.0250 | 2.3x |
| `instruction_following_v1` | -0.0448 | -0.0615 | 0.0167 | 1.1x |
| `safety_v1` | +0.1536 | +0.1568 | 0.0032 | 0.1x |

On `hallucination_v1` the two runs differ by **12.3x the control-arm floor** — one says
-0.078, the other +0.017. The optimized prompts differ as much: **6,067 characters against
3,873**, from the same 78-character seed.

The control arm holds the prompt fixed, so it measures *evaluation* noise. It cannot see
optimizer variance, and here optimizer variance is the larger term on three of five metrics.
`num_runs: 3` does not help either — it averages the evaluation of one optimized prompt, not
the choice of prompt. **An optimizing arm needs a repeat.**

**This is n=2.** Two runs bound the variation; they do not measure it. The honest claim is
that run-to-run variation is *at least* this large.

## Caveats

- **Coverage.** eval_before 64/64; eval_after 63/64 (run B), 62/64 (run A). The control was
  clean 64/64 both sides, so the floor omits a source of error the arms have.
- **~14% of GEPA's generations scored a toolless agent.** The concurrent `c07-pro` arm logs
  `will run without the tools` 16 times over 111 generations while its internal counter
  reports **zero**, because the counter matches a different string (silent-failures #12).
  Read `tool_use_quality_v1` accordingly — though here it is swamped by run-to-run spread
  anyway.
- **One control arm.** Campaign 06 ran four and saw per-metric floors vary 3.4x between them.
- **`c07-pro` was still running** when this was written; a second model on the same seed and
  criteria would test whether the holdout regression generalises.

## Appendix — `uv run wrangler floor run-70166a6bc8 --markdown`

The control arm's artifacts (written 14:03 and 14:28 UTC) were not affected by the overwrite.

```
Reading 1 run(s) from gs://gepa-prompt-wrangler-staging-bucket-v1/pipeline-runs/
Found 1 arm(s): c07-ctrl-sonnet5

### Per-arm floors

| arm | coverage before/after | n paired | scalar floor |
| --- | --- | --- | --- |
| c07-ctrl-sonnet5 | 100% / 100% | 64 | 0.0346 |

### Per-metric, unpaired and paired

Both, always. They disagree substantially, and reporting only the
smaller flatters the pipeline while only the larger hides a real lever.

| arm | metric | unpaired Δ | paired Δ |
| --- | --- | --- | --- |
| c07-ctrl-sonnet5 | safety_v1 | -0.0346 | -0.0417 |
| c07-ctrl-sonnet5 | instruction_following_v1 | +0.0151 | +0.0085 |
| c07-ctrl-sonnet5 | tool_use_quality_v1 | -0.0099 | -0.0164 |
| c07-ctrl-sonnet5 | hallucination_v1 | -0.0077 | -0.0013 |
| c07-ctrl-sonnet5 | final_response_quality_v1 | -0.0020 | -0.0108 |

### Pooled floor, worst movement per metric

| metric | floor |
| --- | --- |
| safety_v1 | 0.0346 |
| instruction_following_v1 | 0.0151 |
| tool_use_quality_v1 | 0.0099 |
| hallucination_v1 | 0.0077 |
| final_response_quality_v1 | 0.0020 |

**Headline floor: 0.0346** (worst pooled metric).

### Drift sign test

1/1 arms drifted negative overall; consistent = **True**.

The five metrics are not independent — all score the same responses via the same autorater — so the **arms** are the unit. With four arms, unanimity is 12.5% two-sided: suggestive, not conclusive.

**On √n:** with only two `num_runs` levels this is a two-point comparison, not a fitted curve. Two points cannot distinguish √n from any other decreasing relationship; report the ratio, do not draw a line through it.
```
