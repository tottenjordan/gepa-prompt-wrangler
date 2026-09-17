# Campaign 03 — the noise floor is a function of two knobs

**Status:** COMPLETE (2026-09-17) · **Result:** [../analysis/2026-09-17-doe-03-result.md](../analysis/2026-09-17-doe-03-result.md)

> **Rewritten 2026-09-17.** The original version of this campaign asked the one-knob
> question — *"what is the `num_runs` floor, really?"* — and was written before
> `score_repeats` existed. Two of its three concerns have since been settled or delivered,
> and the third is now a special case of a larger question. What it got right is preserved
> below under *"What the original version asked"*; the design has moved, not the intent.

## Question

**At a fixed budget, which knob buys resolution — `num_runs` or `score_repeats`?**

Resolution is the binding constraint on every experiment here. Campaign 08 returned
*unresolved* because a control arm drifted **0.022** on the holdout. DOE 02 then showed why:
scoring **byte-identical** responses, the judge disagrees with itself on **64/64 cases** for
`instruction_following_v1` and **0/64** for `safety_v1`.

So the floor has two sources, and since 2026-09-17 there are two knobs:

| knob | repeats | averages | cost / 64 cases | touches the engine? |
| --- | --- | --- | --- | --- |
| `num_runs` | the whole eval | agent **and** judge | ~5.4 min | yes — plus dropout risk |
| `score_repeats` | scoring of one inference pass | **judge only** | ~2.8 min | **no** |

`score_repeats` shipped with no guidance on when to use it. This campaign supplies it.

## Design

**One pool, every cell resampled from it.** Six captures from one engine, each scored five
times — 30 scoring passes, ~2 h unattended. Every (`num_runs` r, `score_repeats` s) cell is
then rebuilt **offline**:

```
pick two DISJOINT sets of r captures       -> "before" and "after"
per capture: combine_results(s of its 5 scorings)    # the score_repeats step
per side:    combine_results(the r capture results)  # the num_runs step
delta = after - before, per metric
```

Both steps call `combine_results`, **the function production calls** — so a cell is not a
model of production averaging, it *is* production averaging over stored inputs.
`tests/test_doe03_resampling.py` asserts a rebuilt r=3 side is numerically identical to
`run_batch_eval_averaged`'s output, scores and per-case rows alike. (Making that true
required routing `num_runs` through `combine_results`; it previously had its own copy.)

Disjoint splits available: **r=1 → 15, r=2 → 45, r=3 → 10.**

### The headline is iso-cost contrasts

cost(r, s) = r·2.6 + r·s·2.8. Several cells cost the same, which is the decision a campaign
designer actually faces:

| budget | option A | option B | option C |
| --- | --- | --- | --- |
| ~11 min | **(2, 1)** 10.8 | **(1, 3)** 11.0 | — |
| ~16.5 min | **(3, 1)** 16.2 | **(2, 2)** 16.4 | **(1, 5)** 16.6 |

`(3,1)` is today's default. `(1,5)` costs the same and never touches the engine.

## Pre-registration

Written and committed **before the data landed**.

**Directional predictions, per metric, licensed by DOE 02:**

| metric | judge sd | judge disagreement | prediction at ~16.5 min |
| --- | --- | --- | --- |
| `instruction_following_v1` | 0.024 | **64/64** | **(1,5) beats (3,1)** — judge-dominated |
| `safety_v1` | **0.000** | **0/64** | **(3,1) beats (1,5)**; repeats do ~nothing |
| `final_response_quality_v1` | 0.023 | 59.4% | (1,5) ≥ (3,1), weaker |
| `hallucination_v1` | 0.010 | 43.8% | no call |
| `tool_use_quality_v1` | 0.010 | 21.9% | no call |

Two metrics predict **opposite** winners from the same data. **If `safety_v1`'s floor falls
materially with `score_repeats`, DOE 02's judge/agent split is wrong** and the guidance now
in CLAUDE.md must be withdrawn rather than patched.

**Reporting rules:**

- **Median, p95 and max — not max alone.** `measure_noise_floor_per_metric` uses
  `max(|delta|)` over a handful of real control arms. A max over 10 resampled draws and one
  over 1,000 are *different statistics*; the second is larger for arithmetic reasons. Compare
  medians across cells, and compare a max to CLAUDE.md's remembered floors only at a matched
  draw count.
- **Per metric, never pooled.** The 2026-09-02 control arm spanned 0.0028 to 0.0747 — 27×.
- **Paired and unpaired at every cell**, so pairing's value is visible rather than asserted.
- **Cells are not independent** — all built from one pool of six. State it beside every table.
- **No cell is dropped for looking noisy.** A noisy cell is the finding. Cells with fewer
  than 8 disjoint splits print `UNDERPOWERED` rather than a number.
- **Do not re-derive the judge/agent split from this pool.** DOE 02 showed n=5 sds have a
  95% interval of [0.007, 0.033] around a true 0.020.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Predictions hold on both metrics** | The judge/agent split is actionable, not just descriptive. Set the knobs per metric; `cheapest_config_for()` can be trusted. |
| **`safety_v1`'s floor falls with repeats** | DOE 02's split is wrong. Withdraw the CLAUDE.md guidance; the 0/64 disagreement was measuring something other than what it claimed. |
| **Neither knob helps much at any budget** | The most important possible result: the floor is dominated by something neither averages — sweeps cannot resolve what they are looking for, and the design must change before the next campaign. |
| **Corrected (3,1) floor ≠ 0.011–0.014** | Update CLAUDE.md, and re-read past conclusions against the corrected number. |
| **√n holds for `num_runs`** | Budget arithmetically; pass `scaling_exponent=0.5`. |
| **√n does not hold** | There is a floor averaging cannot cross — almost certainly the judge — and raising `num_runs` is partly wasted spend. |

## Repo payoff

Shipped with the campaign rather than after it:

- `minimum_detectable_effect()` gains a `score_repeats` axis, keeping its refusal to
  extrapolate without a **measured** exponent — now on both axes. `repeats_exponent=0.0` is
  a legitimate answer (that is `safety_v1`'s predicted shape) and is distinct from unknown.
- `cheapest_config_for(target_mde, floor, ...)` returns the cheapest (r, s) clearing a
  target, or `None` when no setting in budget does — which is a result, not a reason to
  round down.
- `CAPTURE_MIN` / `SCORING_MIN` live in `analyzer.py` as dated measurements, imported by the
  analysis script rather than copied.

## What the original version asked, and what became of it

| Original concern | Status |
| --- | --- |
| *"Depends on Campaign 02 and the multi-run averaging fix"* | **Cleared.** DOE 02 is written up; `average_per_case` matches on case index. |
| *"The 3-run figure was computed through a bug"* | **Still true, still unmeasured** — `average_per_case`'s own docstring says so. This campaign's (3,1) cell answers it. |
| *"A floor should be a function, not a remembered constant"* | **Delivered** — `minimum_detectable_effect()` exists, and now takes both knobs. |
| Control arms at `num_runs ∈ {1,2,3,5}`, two replicates | **Superseded by resampling**, which gets more splits per cell from less compute. r=5 is out of reach of a 6-capture pool; r≤3 covers the production default. |
| Check √n explicitly rather than assuming | **Kept**, and the exponent is fitted rather than assumed. |

## Result

**Complete.** Full write-up:
[../analysis/2026-09-17-doe-03-result.md](../analysis/2026-09-17-doe-03-result.md).

Against this campaign's own decision table:

| pre-registered outcome | what happened |
| --- | --- |
| *Predictions hold on both metrics* | **No.** `safety_v1` held exactly (repeats exponent 0.02); `instruction_following_v1` failed (−0.06, predicted to be the best buy). |
| *`safety_v1`'s floor falls with repeats → withdraw the guidance* | Did **not** happen — safety behaved as DOE 02 implied. The guidance survives for safety. |
| *Neither knob helps much at any budget* | **True for the holdout specifically** — `instruction_following_v1` scores 0.20 and −0.06. Its floor is not a budget problem. |
| *Corrected (3,1) floor ≠ 0.011–0.014* | **Confirmed**: 0.0082–0.0178 per metric. The remembered range is too optimistic for the holdout and too pessimistic for safety. |
| *√n holds* | **No**, and the deviation is per metric: 0.58 to −0.11. |

**The finding that was not on the decision table**, and is the one worth carrying: a per-case
judge-disagreement rate predicts aggregate variance reduction **in one direction only**. 0/64
correctly implies repeats buy nothing; 64/64 does **not** imply they buy a lot, because
disagreements that cancel in the mean never reach the aggregate a campaign reads.
