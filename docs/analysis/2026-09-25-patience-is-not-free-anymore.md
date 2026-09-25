# Fixing the selection signal removed the free lunch that justified early stopping

**One-line result:** on the first real run under the new signal — 30-case stratified validation
subset plus continuous per-case scoring — **every patience value tested returns a different, worse
prompt**. The "62% of budget saved, byte-identical prompt" result that set the default at 15 was an
artifact of the defect options A and B removed. **Patience stays off.**

Measured on `run-d55b159050` (2026-09-24, Sonnet 5, 400-call budget, no patience set).

## Why the old result no longer applies

The original replay ([2026-09-24-stopping-replay.md](2026-09-24-stopping-replay.md)) found early
stopping *provably* free: patience ≥ 10 returned a byte-identical prompt while saving 57–79% of the
budget. That proof rested on one fact — the 15-case binary validation subset **saturated at
1.0000** early, so once the ceiling was hit no later candidate could beat it, and `best_idx` being
the first argmax fixed the winner permanently.

That write-up said so at the time: *"early stopping is not detecting convergence here; it is
detecting that the instrument ran out of range."* Options A and B restored the range.

## What the new signal does

| | old (15-case, binary) | new (30-case, continuous) |
| --- | --- | --- |
| candidates | 16–22 | 10 |
| distinct scores | 6 | **9 of 10** |
| best score | **1.0000 (saturated)** | 0.9512 (headroom) |
| best found at | candidate 4–6 of 16–21 | **candidate 9 of 10 — the last** |

Running best over the run:

```
cand 0  0.9298   <- seed
cand 1  0.9493   <- new best
cand 2..8        eight candidates, no improvement
cand 9  0.9512   <- new best, at the very end
```

A long plateau followed by a late gain. That is the shape early stopping is worst at, and the old
signal could not produce it because it had already hit its ceiling.

| patience | stops at | saved | returns | verdict |
| --- | --- | ---: | --- | --- |
| 3 | iter 5 | 71.6% | candidate 1 | **different prompt** |
| 5 | iter 7 | 71.6% | candidate 1 | **different prompt** |
| 8 | iter 10 | 61.0% | candidate 1 | **different prompt** |
| 10 | iter 12 | 49.6% | candidate 1 | **different prompt** |
| **15** (the shipped default) | iter 17 | 40.4% | candidate 1 | **different prompt** |

## Decision

**Patience stays off**, which is already the default. Do not enable it under the new signal.

The case for 15 was never "it is a good trade" — it was "it is *free*", provably, because the
returned prompt was byte-identical. That argument is dead. What replaces it is a genuine trade with
one run behind it, and this analysis does not have the evidence to price it.

## What this does not say

**It does not say stopping would have cost much.** The margin between the patience-15 pick and the
true best is **+0.0019** on the validation score — about 0.2%, far below any per-metric resolution
in this repo (0.058–0.103). By the selection signal's own measure the concession is negligible, and
40% of an optimize budget is not.

The honest position is that the two numbers cannot be compared: the val score ranks candidates, it
does not estimate the quality difference between them, and only an `eval_after` on both prompts
would. This run produced one prompt, so there is no such comparison.

**The run was budget-limited, not converged.** It used 423 of a 400-call budget and was still
improving at the last candidate. Whether a longer run plateaus, and where, is unmeasured — a
600-call run is the obvious follow-up and would cost one arm.

**n=1**, one model, one seed. GEPA's search is stochastic and CLAUDE.md measures run-to-run spread
at 12.3× the control floor; a second run could plateau anywhere.

## The general lesson

Two changes that each looked independently good — a bigger stratified validation subset, and
recovering the continuous score — **jointly invalidated a third** that had been measured, written
up, defaulted and shipped a day earlier. The early-stopping result was never wrong about its own
data; it was measuring an instrument defect, and the measurement stopped applying the moment the
defect was fixed.

Worth carrying into campaign 10's design: **any result derived from the selection signal has to be
re-derived when the selection signal changes**, and that includes results that looked like clean
wins.
