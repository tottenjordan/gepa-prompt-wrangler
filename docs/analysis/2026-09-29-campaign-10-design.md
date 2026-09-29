# Campaign 10 design: does the safety gain replicate, and how wide is the search spread?

**Manifest:** `manifests/campaign-10_manifest.yaml` · **Status: drafted, NOT submitted** — for
review first.

## The question

Two runs have measured GEPA beating an unoptimized control on `safety_v1`:

| run | arm | vs control | 95% CI |
| --- | --- | --- | --- |
| campaign 09 | both optimized arms | +0.0952 | excludes zero |
| on/off validation | continuous | +0.1310 | [+0.0317, +0.2302] |
| on/off validation | binary | +0.1905 | [+0.1071, +0.2738] |

Each is **one run per condition**. Those intervals bound case-sampling noise only, and they say
nothing about which prompt GEPA happened to find. CLAUDE.md puts that term at up to 12.3× the
control floor. So the current evidence cannot separate "GEPA improves safety on this agent" from
"GEPA found a good prompt twice".

**Primary question:** do three independent runs of one arm all beat the control on `safety_v1`?
And how far apart do they land?

**Secondary question:** does one lexicographic arm (#139) land outside the binary replicates'
spread?

## Arms

| arm | what | why |
| --- | --- | --- |
| `c10-binary-r1..r3` | binary selection (ADK default), `replicates: 3` | the replication, and the first direct measurement of search variance |
| `c10-lexicographic` | pass/fail first, continuous mean as tie-break | the remaining form of option A (on/off doc, "What this does not cover") |
| `c10-control` | `skip_optimize: true` | the floor, and the per-case baseline for every contrast |

All five use `claude-sonnet-5` and the on/off campaign's 78-character seed prompt, with
`max_metric_calls: 400`, `num_runs: 2`, `score_repeats: 2`, and no patience.

**The seed is deliberately NOT pinned.** Each replicate derives its GEPA seed from its own id. A
`gepa_seed` would put all three on one minibatch schedule, and they would stop being
independent draws of the search, which is the whole measurement. (The on/off campaign pinned
its seed for the opposite reason: a paired A/B needs one schedule.) One consequence: the
lexicographic arm is not paired to any binary replicate, so it is read against their range, not
against one of them.

**The canary is reused:** `data/canaries/onoff.json`. The responses are frozen, so the same file
read in two campaigns also measures judge drift *across* them. Readings now carry per-case
scores (#137), so drift within this campaign is paired.

## Power

`uv run wrangler preflight --manifest manifests/campaign-10_manifest.yaml`, against campaign
09's measured per-case variance, 64 cases at `num_runs=2`, target ±0.075:

| metric | MDE (one contrast) | verdict |
| --- | --- | --- |
| `final_response_quality_v1` | 0.0899 | underpowered |
| `hallucination_v1` | 0.0709 | resolves |
| `instruction_following_v1` | 0.0927 | underpowered |
| `safety_v1` | **0.1031** | underpowered |
| `tool_use_quality_v1` | 0.0602 | resolves |

- **One replicate against the control** resolves the on/off effects (+0.13, +0.19). It does not
  resolve campaign 09's +0.0952.
- **The mean of three replicates against the control** does better on case-sampling noise, but
  only partly, because the control is shared and its variance is not averaged. With equal
  per-arm variance, the MDE scales by √((1/3 + 1)/2) ≈ 0.82, to **~0.084** on `safety_v1`. That
  is an approximation, and it covers case sampling only. Search variance is what the
  replicates add, and it is the thing being measured, so there is no prior figure for it.
- The eval set cannot grow (idea 1 closed 2026-09-24), and more `num_runs` buys little at DOE
  03's exponents. So this is the design's ceiling.

## Pre-registered readings

All are per-case paired contrasts on `case_index`, over cases scored on every side, using the
bootstrap from `scripts/onoff_contrast.py` (10,000 resamples).

1. **Replication (primary).** Compute `c10-binary-rK − c10-control` on `safety_v1` for each
   replicate, plus the pooled contrast: the mean of the three per-case deltas, minus the
   control's.
   - **Replicates:** all three point estimates are positive, and the pooled CI excludes zero.
   - **Does not replicate:** the pooled CI includes zero.
   - Anything between these is reported as **mixed**, with the three intervals shown. It is
     not rounded to either verdict.
2. **Search spread.** For each metric, the range and standard deviation of the three
   replicates' `eval_after` means, set beside the control's |delta| and the canary drift. This
   is the number every earlier result carried as an unmeasured caveat.
3. **Holdout.** `instruction_following_v1` for each arm against the control. The on/off
   validation was the first run where it did not regress. A regression on any replicate is
   reported.
4. **Lexicographic (secondary).** Its contrast against the control, placed inside or outside
   the binary replicates' range on each metric.
   - Outside the range, in the good direction, is a **lead**. It needs its own replicates before
     it is a result.
   - Inside the range means **no evidence it differs** from binary.
5. **Judge drift gate.** Any arm contrast smaller than the paired canary drift over the
   same window is not a prompt effect, whatever the control says.

## Known confound: eval-side gaps grow with manifest position

`dag.py` runs every deploy, then every `eval_before`, and only then each arm's optimize →
redeploy → eval_after, one arm at a time (`parallelism=1`), in manifest order. Each optimize
takes ~5.5 h, so the time between an arm's two eval sides grows with its position. Assuming
the list order is kept:

| arm | approx. before → after gap |
| --- | --- |
| `c10-binary-r1` | ~7 h |
| `c10-binary-r2` | ~14 h |
| `c10-binary-r3` | ~21 h |
| `c10-lexicographic` | ~28 h |
| `c10-control` | ~28 h (last, no optimize) |

This is the same structure the on/off run had. Its control moved −0.0079 on `safety_v1`, while
campaign 09's moved +0.0732. What it means for the readings:

- Judge drift is **measured**: the canary is read on every side.
- Agent-side drift with engine age, the one hypothesis for campaign 09's drift still standing,
  is **not measured**. It would enter as a replicate-spread term correlated with position.
- Because the control spans the whole window, it is the **most conservative** baseline for
  common-mode drift. It is not a symmetric one.
- **It is also a free test of engine age.** Five arms at five different gaps give a rough dose
  axis. Five points cannot establish a trend, but a monotone drift in per-arm deltas along the
  gap would be the first positive evidence for that hypothesis. Record the eval-side timestamps
  from the stage artifacts when analysing.

**Alternative, for review:** move the control to the top of the list, which gives it the
shortest gap. That removes it as a full-window common-mode baseline. The current order is
recommended.

## Cost and teardown

- **Wall clock ≈ 29 h.** Four optimize stages at ~5.5 h each, plus five deploys (health-gated,
  ~12 min each), ten eval sides (~17 min each) and four redeploys. The on/off run took 15.8 h
  for two optimized arms and a control.
- **Five engines** are created, labelled `lifecycle: ephemeral, campaign: "10"`.
  - Teardown is the campaign's last step, but only on confirmation, since deleting is
    irreversible.
  - If the engine-age follow-up is wanted, a t+16 h capture from one of these engines should
    be taken **before** teardown.
- `cache_bust: "c10-v1"`. #139 changes `components.py`, so the optimize component misses the
  cache once anyway. The bust guarantees the eval stages do too.

## Preconditions for submitting

- #138 and #139 are merged. This branch sits on #139, because the manifest needs
  `lexicographic_val_score` to parse.
- No campaign driver is live when those merges land (silent-failures #13).
- `uv run wrangler preflight --manifest manifests/campaign-10_manifest.yaml` still passes.
- The confirmation step prints the lexicographic notice on `c10-lexicographic` only, and no
  `PINNED by manifest` line on any arm.
