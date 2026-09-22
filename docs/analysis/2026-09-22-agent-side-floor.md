# The agent-side floor does not grow with the gap between eval sides

**Companion to** [2026-09-22-canary-retrospective-drift.md](2026-09-22-canary-retrospective-drift.md),
which ruled out the judge. This rules out elapsed time. **Both were predictions I made and
both were wrong, which is the useful part.**

## Question

The canary showed `safety_v1`'s judge is flat over five days (≤0.005). Campaign 09's control
moved **+0.0732** in ~16 h. So the noise is agent-side — and the obvious next hypothesis was
that the **agent-side floor grows with the gap**: DOE 03 measured its floors on captures
minutes apart, while a real campaign puts 12–20 h between eval sides.

## Part 1 — Splitting DOE 03's variance, for free

DOE 03 took **six captures minutes apart** and scored **each five times**. That decomposes
cleanly and nobody had done it:

- **judge-side** = sd across re-scorings of *one* capture, averaged over captures
- **agent-side** = sd across captures of each capture's judge-averaged mean

| metric | judge sd | agent sd | agent/judge |
| --- | --- | --- | --- |
| `safety_v1` | 0.0047 | **0.0124** | **2.6** |
| `hallucination_v1` | 0.0067 | 0.0106 | 1.6 |
| `tool_use_quality_v1` | 0.0058 | 0.0066 | 1.1 |
| `instruction_following_v1` | 0.0212 | 0.0187 | 0.9 |
| `final_response_quality_v1` | 0.0160 | **0.0087** | **0.5** |

**This independently reproduces DOE 03's exponent table from a different angle**, which is
the best evidence that the decomposition is sound:

| metric | this decomposition | DOE 03 exponents |
| --- | --- | --- |
| `safety_v1` | agent-dominated (2.6) | `num_runs` 0.58, `score_repeats` 0.02 |
| `final_response_quality_v1` | judge-dominated (0.5) | `score_repeats` 0.66, `num_runs` 0.34 |

A metric whose noise is agent-side responds to re-running inference; one whose noise is
judge-side responds to re-scoring. Both tables say the same thing about both metrics.

## Part 2 — The five-day test

A fresh capture from the **same engine** (`gepa-c08-new-r1`, `4023875557346246656`, created
2026-09-11, last updated 2026-09-12 — a settled engine) against the **same 64-case eval set**,
scored three times and judge-averaged, compared to the six 2026-09-17 captures.

| metric | 2026-09-17 | agent sd | 2026-09-22 | Δ | in sd | coverage |
| --- | --- | --- | --- | --- | --- | --- |
| `safety_v1` | 0.9825 | 0.0124 | 0.9919 | **+0.0095** | **0.8** | 48 → 64 |
| `tool_use_quality_v1` | 0.9762 | 0.0066 | 0.9721 | −0.0041 | 0.6 | 64 → 64 |
| `final_response_quality_v1` | 0.9136 | 0.0087 | 0.9067 | −0.0068 | 0.8 | 58 → 61 |
| `hallucination_v1` | 0.9554 | 0.0106 | 0.9428 | −0.0126 | 1.2 | 63 → 64 |
| `instruction_following_v1` | 0.7803 | 0.0187 | 0.7428 | −0.0375 | 2.0 | 60 → 62 |

**Nothing moved more than 2.0 within-day sd, and `safety_v1` moved 0.8.** Five days of
elapsed time cost about as much as a few minutes did.

## What this means

**The hypothesis is refuted.** The agent-side floor does **not** grow materially with the gap
between eval sides. `safety_v1` over five days: **+0.0095**. Campaign 09's control over
sixteen hours: **+0.0732**, nearly eight times larger over a much shorter interval.

So campaign 09's control drift is explained by **neither**:

| candidate | measured | verdict |
| --- | --- | --- |
| autorater drift | ≤0.005 over 5 days | ruled out |
| agent drift with elapsed time | +0.0095 over 5 days | **ruled out** |
| dropout | campaign 09 was 64/64 both sides | ruled out by the campaign itself |
| engine change | control skips redeploy; same engine both sides | ruled out by the DAG |

Something specific to campaign 09's *configuration* produced it. Two candidates were raised;
**one has since been tested and dropped.**

### Prompt specificity — TESTED AND CONTRADICTED, 2026-09-22

The hypothesis: campaign 09's control ran the **78-character seed** on both sides, and a prompt
that short leaves enormous latitude, so its response distribution should be far wider than a
specified one's. If true, the agent-side floor would be a property of the *prompt*, and every
control arm in this repo would be measuring the floor in the highest-variance regime available.

It predicts something directly checkable in campaign 09's own artifacts. `scores_std` records
the spread across the two inference runs *within* each eval side at `num_runs: 2`. Four sides
ran the seed (control both sides, both treatments' before sides) and two ran 5–6k-character
optimized prompts. **Seed sides should be wider.**

| metric | seed (n=4) | optimized (n=2) | ratio | |
| --- | --- | --- | --- | --- |
| `safety_v1` | 0.0110 | 0.0280 | **2.55×** | contradicted |
| `instruction_following_v1` | 0.0032 | 0.0144 | **4.52×** | contradicted |
| `hallucination_v1` | 0.0070 | 0.0124 | **1.78×** | contradicted |
| `tool_use_quality_v1` | 0.0060 | 0.0077 | **1.29×** | contradicted |
| `final_response_quality_v1` | 0.0180 | 0.0078 | 0.43× | consistent |
| **pooled** | **0.0090** | **0.0141** | **1.57×** | **contradicted** |

**The direction is wrong on four of five metrics, including the one in question.** The
78-character seed produced the *narrowest* within-side spread in the campaign — the control's
before side was **0.0018** on `safety_v1`, the smallest number anywhere in the run, while that
same arm moved **+0.0794** across the 16-hour gap. A prompt whose vagueness supposedly widens
the response distribution cannot also produce its tightest measurement.

**Dropped.** Caveats worth keeping: n=4 against n=2, and a two-sample `scores_std` is a noisy
estimate of spread. But this was never a close call in a direction that favours the
hypothesis — it is wrong-signed and consistent about it.

### Engine age / platform state — the surviving candidate

Campaign 09's engines were **hours old** and freshly health-gated; the engine measured in this
note had been up and unchanged for ten days. A new engine's output distribution may not be
stationary across its first day — instance churn, autoscaling, cold paths — or the platform may
have rolled a new Claude build underneath it.

**This is no longer separable from campaign 09's own data.** Its three engines were reaped on
2026-09-21; the capture that would settle it does not exist. Testing it now needs a fresh
deploy captured at t=0 and t+16 h, and the engine kept —
[capture-before-reap](../notes/engine-lifecycle.md) exists so the next one survives.

## CAVEAT added after the fact — this was measured near the ceiling

Pulling campaign 09's stage artifacts off GCS afterwards showed the two situations are not
comparable in a way this note originally glossed:

| | `safety_v1` level | within-side spread (minutes) | across-gap Δ |
| --- | --- | --- | --- |
| c08 engine (this note) | **0.98** | 0.0124 | +0.0095 over 5 days |
| campaign 09 control | **0.82** | **0.0018** | **+0.0794 over 16 h** |

**The engine measured here sits at 0.98 on `safety_v1`, with 0.02 of headroom.** Campaign 09's
control sat at 0.82. A shift that is invisible near the ceiling can be large in mid-range, so
"the floor does not grow with the gap" is established **for a near-saturated metric** and does
not transfer to the regime campaign 09 was actually in.

The two also disagree in a way no single explanation covers yet: at 0.82 the *short*-timescale
spread is tiny (0.0018 across two runs minutes apart) while the 16-hour gap is enormous; at
0.98 the short-timescale spread is larger (0.0124) and the five-day gap is not. That is the
shape of a **state change** in the engine between campaign 09's two eval sides rather than
noise of any kind — and the control arm does not redeploy, so whatever changed was not ours.

## What to do

1. **Stop attributing control drift to elapsed time.** Neither the judge nor the agent drifts
   materially over days. `num_runs` remains the right lever for `safety_v1` — the within-day
   agent sd of 0.0124 is real and is what it averages down.
2. ~~Test the prompt hypothesis~~ — **done, and dropped** (above). It was contradicted by
   campaign 09's own `scores_std`, at no cost, and the planned 2×2 would have spent two hours
   confirming a null. The surviving candidate is engine age / platform state, which needs a
   fresh deploy captured at t=0 and t+16 h.
3. **Use the variance split, not just the exponents.** The judge/agent decomposition above is
   computable from any DOE that takes multiple captures and scores each multiple times, and it
   says directly which lever to buy. It cost nothing here — the data was already on disk.
4. **Treat DOE 03's coverage as a caveat on its floors.** `safety_v1` scored as low as **48/64**
   on one 2026-09-17 pass. Floors computed across case sets varying by 16 cases are softer
   than the quoted figures suggest.

## Artifacts

- Fresh capture: `outputs/captures/agentfloor-d5_20260922_033310.pkl` (64/64)
- Scorings: `outputs/eval_agentfloor-d5-pass{1,2,3}_scored_20260922_034127.json`
- Baseline: `outputs/doe03/manifest.json` → six captures × five scorings
- Engine: `gepa-c08-new-r1` — **retain until this line is removed**; it is the only comparator
  for the 2026-09-17 baseline, and deleting it makes this measurement unrepeatable.
