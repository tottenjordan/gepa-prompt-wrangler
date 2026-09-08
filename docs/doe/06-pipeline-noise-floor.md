# Campaign 06 — What is the noise floor, on the pipeline?

**Status:** **Complete 2026-09-08** · floor measured, √n beaten, drift null

## Question

Campaign 07's entire output is *differences between models*. Until the floor is
known, a 0.04 gap between two tiers cannot be told apart from the same tier
measured twice.

The standing figure — ±0.059 at `num_runs: 1`, ±0.034 at 3 — was computed through
the positional case-pairing bug fixed in `average_per_case()`, which averaged
case 2's score with case 1's whenever two runs dropped different cases. It has to
be re-measured regardless of what 07 needs.

## Design

Control arms: prompt **byte-identical** on both sides, `skip_optimize: true`, so
there is no optimization between the two evaluations and every delta is noise.

`num_runs ∈ {1, 3, 5}`, one Anthropic and one Gemini arm at each level, run **two
at a time — one of each publisher**, so the two arms draw on separate Vertex
quota pools.

Six arms, ~1.5 h each, two concurrent ≈ **5 h**.

## Pre-registration

- Two replicates per level where the window allows; report both before pooling.
- With only two levels, the √n check is a two-point comparison rather than a
  fitted curve. Say so when reporting it — two points always look like a line.
- Per-metric floors, **paired and unpaired**, so the value of pairing is visible
  rather than asserted at "~15%".
- Do not drop a level for looking noisy. A noisy level is the finding.
- Check the √n prediction against the measured points rather than assuming it.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| Corrected 3-run floor differs materially from 0.034 | Update CLAUDE.md and re-read the 2026-08-22 sweep against the corrected number |
| Same as 0.034 | The bug did not bite in practice. Say so — a fix that changes nothing measurable is still worth having said |
| √n holds | `num_runs` is a predictable dial and can be budgeted arithmetically |
| √n does not hold | Something averaging cannot cross — almost certainly the judge — and raising `num_runs` is partly wasted spend |
| **Floor exceeds the between-tier gaps 07 finds** | **The eval set cannot resolve model quality. 07 must be redesigned, not reported.** That is the most valuable outcome here |

## Payoff

`minimum_detectable_effect(num_runs, n_cases)` in `wrangler/reporting/analyzer.py`,
wired into `classify_deltas()` so a report marks a sub-floor delta automatically
instead of relying on the reader to remember a constant.

## Result

**Run 2026-09-07/08. Four arms, all reaching SUCCEEDED end to end.**

Reproduce with:

```bash
wrangler floor run-3aa99b8293 run-a8d4d4a2a0 run-a8b66006db run-2308c24620
```

Every arm: health gate `rate=1.00` with zero rerolls, and **64/64 coverage on
both sides**. This is the first floor this project has measured that is not
partly dropout — the previous best was 88%, and earlier sweeps differenced eval
runs that scored different case subsets. `EVAL_MAX_RETRIES = 16` is what closed
it.

| arm | model | num_runs | coverage | scalar floor |
| --- | --- | --- | --- | --- |
| c06-ctrl-claude-n1 | claude-sonnet-4-6 | 1 | 100% / 100% | 0.0583 |
| c06-ctrl-gemini-n1 | gemini-3.5-flash | 1 | 100% / 100% | 0.0374 |
| c06-ctrl-claude-n3 | claude-sonnet-4-6 | 3 | 100% / 100% | **0.0112** |
| c06-ctrl-gemini-n3 | gemini-3.5-flash | 3 | 100% / 100% | **0.0141** |

### Per-metric pooled floor

Worst movement per metric across the arms — the max, not the mean, because two
arms disagreeing measures how variable the floor itself is and averaging that
away understates noise.

| metric | floor |
| --- | --- |
| safety_v1 | 0.0583 |
| instruction_following_v1 | 0.0514 |
| final_response_quality_v1 | 0.0316 |
| tool_use_quality_v1 | 0.0231 |
| hallucination_v1 | 0.0172 |

**Headline floor: 0.0583.** The 3.4x spread between the tightest and loosest
metric is why `classify_deltas` now accepts a per-metric mapping: holding
`hallucination_v1` to safety's floor would dismiss a real move as noise.

### Averaging beats √n, and it replicates

```
claude   n=1 0.0583  ->  n=3 0.0112     5.2x
gemini   n=1 0.0374  ->  n=3 0.0141     2.7x
√3 predicts                             1.73x
```

Both publishers beat the √n prediction independently. **This contradicts the
figure CLAUDE.md carried** (±0.059 → ±0.034, a 1.7x reduction): the n=1 end was
about right, the n=3 end was roughly 2.4-3x too pessimistic.

Report the **range**, not a point estimate. The two arms disagree on magnitude
(5.2x vs 2.7x) and this remains a two-point comparison per level — two points
cannot distinguish √n from any other decreasing relationship, so the honest
claim is "at least √n, plausibly better, measured twice" rather than a fitted
exponent.

Practical consequence: raising `num_runs` is a **stronger** lever than the docs
implied, not a weaker one.

### Pairing helps less than one arm suggested

The validation arm alone showed pairing cutting the floor 44-64%, and that did
not survive four arms. At n=3 paired and unpaired deltas are comparable and
paired is sometimes *worse* (`c06-ctrl-gemini-n3`, final_response_quality:
unpaired −0.0141, paired −0.0224).

That is itself the finding. With coverage at 100% there are no unmatched cases
for pairing to remove, so its benefit collapses — pairing was compensating for
dropout, and dropout is now gone. CLAUDE.md's "~15%" was measured through
dropout and describes a pipeline that no longer exists.

### Drift: the pre-registered null

**2 of 4 arms drifted negative. Not consistent.** Signs scatter, which is H₀.

The 5-of-5 negative observation that prompted the test was a coincidence — four
later measurements of that same arm gave 3-pos/2-neg, 4-pos/1-neg and
4-pos/1-neg. Per the decision rule fixed before any data landed, this records
the null and **unblocks campaign 07**. No order-swap experiment needed.

See [../analysis/2026-09-02-eval-order-drift.md](../analysis/2026-09-02-eval-order-drift.md).

### Caveat worth carrying

All four engines drew `rate=1.00` on the first attempt. At the ~55% healthy
fraction pooled from campaigns 01 and 09 that is `0.55⁴ ≈ 9%` — an unusually
good run of draws. **These floors are measured on unusually healthy engines and
plausibly sit at the optimistic end.** A campaign that draws a 75% engine and
proceeds anyway should expect a larger floor than the numbers above.

### Payoff, delivered

- `floor_from_control_arm()`, `measure_noise_floor_per_metric()`,
  `minimum_detectable_effect()` and `drift_sign_summary()` in
  `wrangler/reporting/analyzer.py`.
- `wrangler floor` so the numbers above are reproducible rather than
  hand-computed.
- `minimum_detectable_effect` deliberately refuses to assume √n — this campaign
  is why, and the measured ratios above are why that refusal was right.

