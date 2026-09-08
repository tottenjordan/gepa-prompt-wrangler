# Do second evals score lower? A pre-registered sign test

**Status:** **Resolved 2026-09-08 — null.** Registered 2026-09-07 before any arm landed.
**Gated:** Campaign 07 — now released.

## The observation

Campaign 06's validation arm evaluated a **byte-identical prompt twice**, with
`skip_optimize: true`, so every delta it produced is noise by construction. All
five metrics moved the same way:

| metric | unpaired Δ | paired Δ |
| --- | --- | --- |
| hallucination_v1 | −0.0747 | −0.0420 |
| final_response_quality_v1 | −0.0554 | −0.0197 |
| safety_v1 | −0.0104 | +0.0000 |
| tool_use_quality_v1 | −0.0050 | −0.0070 |
| instruction_following_v1 | −0.0028 | +0.0050 |

Coverage was 64/64 on both sides, so this is not dropout. Pure noise should
scatter in sign; five of five negative does not look like scatter.

## Why it matters enough to gate a campaign

Campaign 07's entire output is before/after deltas per model tier. If the second
of two evals systematically scores lower, then **every optimized arm is
penalised by roughly the drift**, and the penalty is the same order as the
effects 07 exists to measure. A real improvement of +0.05 measured against a
−0.04 drift reads as +0.01 — indistinguishable from nothing.

The direction matters too. This bias understates improvement, so it produces
false *negatives*: real gains discarded as noise. That is less alarming than a
false win and easier to miss, because nobody investigates a null result.

## Pre-registration

Written before any Campaign 06 arm reported. That ordering is the entire value
of this document.

### Hypothesis

- **H₀:** the sign of `after − before` is random per metric per arm.
- **H₁:** second evals score systematically lower.

### The statistic, and what is *not* independent

Count negative deltas per arm, unpaired and paired reported separately.

**The five metrics are not independent.** All five score the same 64 agent
responses via the same autorater in the same call. A binomial p-value over 20
metric-arm cells would treat them as 20 independent draws and badly overstate
significance — that is the mistake this section exists to prevent.

**The arms are the independent units.** Four arms, so the strongest honest claim
available from a clean sweep is *4/4 arms drifted the same way*, which under a
sign test on arms alone is `2 × 0.5⁴ = 12.5%` two-sided. That is suggestive, not
conclusive, and the write-up must say so rather than quoting a p-value over
cells.

### Decision rule, fixed now

| Outcome | Reading | Action |
| --- | --- | --- |
| All 4 arms negative on a majority of metrics | Consistent with real drift | Escalate to the order-swap experiment below; **07 stays gated** |
| 3 of 4 negative | Ambiguous at n=4 | Report as unresolved; do not proceed on it either way |
| Signs scatter across arms | The validation arm was a coincidence | Record the null, **unblock 07** |

No extending the sweep to chase a promising pattern. If four arms cannot settle
it, the answer is "four arms cannot settle it".

### Known limitation, stated up front

`per_case` rows carry only `case_index` and metric scores — **no timestamps**.
Nothing in these artifacts can correlate drift with wall-clock, engine age, or
position within the run. Any temporal explanation is therefore untestable from
the campaign data alone, and this analysis must not imply otherwise.

One further confound to record rather than resolve: the two evals of an arm run
minutes apart on the **same engine**, so engine-state drift and judge drift are
not separable here either.

## If confirmed: the discriminating experiment

Run one control arm with the two evals **swapped in time**, so the arm labelled
`eval_after` executes first.

- **Earlier eval scores higher regardless of label** → temporal or engine-state
  effect. The pipeline is fine; the measurement needs a correction term.
- **`before`-labelled eval scores higher regardless of order** → the two code
  paths differ. That is a defect, not a measurement property, and the fix is in
  the code rather than the statistics.

This needs a manifest and a small DAG change, so it is scoped only if the
decision rule above fires.

## Candidate mechanisms, none yet tested

Listed so the eventual investigation does not start from a blank page, and
flagged as speculation:

- **Autorater non-determinism with a directional component.** Per-case deltas
  reach the full 0→1 range between identical runs, so the judge is very noisy;
  whether that noise is centred is unknown.
- **Engine state.** The second eval hits an engine that has served ~64 more
  requests. Campaign 09 found single-attempt *reach* is flat with age, but said
  nothing about answer *quality*.
- **MCP session degradation** over the life of a run, which would plausibly hit
  tool-dependent metrics hardest — though here `tool_use_quality_v1` drifted
  least, which argues against it.

## Result

**Null. 2 of 4 arms drifted negative — signs scatter. Campaign 07 is unblocked.**

Campaign 06's four control arms, each a byte-identical prompt evaluated twice at
100% coverage on both sides:

| arm | negative | positive | direction |
| --- | --- | --- | --- |
| c06-ctrl-claude-n1 | 1 | 4 | positive |
| c06-ctrl-gemini-n1 | 4 | 1 | negative |
| c06-ctrl-claude-n3 | 2 | 3 | positive |
| c06-ctrl-gemini-n3 | 4 | 1 | negative |

Two negative, two positive. Under the decision rule fixed before any of this
data existed — *"signs scatter across arms → the validation arm was a
coincidence; record the null and unblock campaign 07"* — this is the third row
of the table, and it is recorded as such.

### The original observation was a coincidence

The 5-of-5 negative result that prompted this document came from a single arm.
That same arm, re-measured four more times as the pipeline was fixed and
re-run:

| measurement | signs | scalar floor |
| --- | --- | --- |
| 2026-09-02 (the observation) | 5 neg | 0.0747 |
| re-run, 100% engine | 3 pos / 2 neg | 0.0685 |
| re-run | 4 pos / 1 neg | 0.0670 |
| re-run | 4 pos / 1 neg | 0.0583 |

The signs flipped immediately and never returned. The **floor magnitude**, by
contrast, held between 0.058 and 0.075 across all four — the measurement is
stable, its sign is not, which is exactly what noise looks like.

Worth saying plainly: 5-of-5 on five metrics that are not independent was
always weak evidence. The document said so at registration. It was still worth
four arms to check, because the alternative — a systematic bias against every
optimized arm — would have quietly understated every result campaign 07
produces.

### One pattern deliberately not claimed

The two n=3 arms are 4-of-5 and 2-of-5 negative, and both Gemini arms are
4-of-5 negative while both Claude arms lean positive. A publisher effect is a
tempting read.

It is not one this design can support. The pre-registration fixed the arm as
the unit of analysis and unanimity as the bar; with one arm per
publisher-per-level, "Gemini drifts negative" rests on two arms that differ in
`num_runs` as well as publisher. Reporting it would be exactly the
after-the-fact pattern-finding the registration exists to prevent. Recorded
here as an observation for a future design to test properly, not as a result.

### Consequences

- **Campaign 07 proceeds.** Its before/after deltas need no drift correction.
- The order-swap experiment in the next section is **not** run.
- `drift_sign_summary()` stays in `analyzer.py` and runs on every future
  campaign's controls, so a real drift would surface rather than being assumed
  absent.

