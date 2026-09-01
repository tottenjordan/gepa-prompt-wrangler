# Campaign 06 — What is the noise floor, on the pipeline?

**Status:** Ready to run · **Runs first** · Everything else depends on it

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

_Not yet run._
