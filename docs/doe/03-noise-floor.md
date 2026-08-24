# Campaign 03 — What is the noise floor, really?

**Status:** Not started · **Depends on:** Campaign 02, and the multi-run averaging fix

## Question

CLAUDE.md instructs every sweep to clear a noise floor of **~0.059 at `num_runs: 1`** and
**~0.034 at 3**, and says the improvement comes from averaging cutting variance by √n. Two
problems with that as it stands.

**The 3-run figure was computed through a bug.** `run_batch_eval_averaged` paired per-case
rows across runs by *list position*, and runs drop different cases, so position k was a
different case in each run. It also averaged `case_index` itself. Fixed now, but the number
measured through it has to be re-measured before it is trusted.

**And a floor should be a function, not a remembered constant.** Nobody reading a report
should have to recall ±0.059. `classify_deltas()` already exists to mark a delta against a
floor; it should be able to compute the floor from the run's own configuration.

## Design

Control arms — the prompt **byte-identical** on both sides, no optimize stage between them —
at `num_runs ∈ {1, 2, 3, 5}`, **two replicates each**, paired on `case_index` via the
existing `paired_deltas()` (`wrangler/eval/evaluator.py`).

Decompose each level using Campaign 02's judge/agent split, so the floor is not just
measured but attributed. Check the √n prediction explicitly against the measured points
rather than assuming it — if averaging does not buy √n, the guidance in CLAUDE.md is wrong
in a way that changes how sweeps are budgeted.

Where a level can be served from a capture rather than fresh inference, do so; that isolates
the scoring contribution and cuts cost. State which levels were captured and which were
live, because they are not the same measurement.

## Pre-registration

- **Two replicates per `num_runs` level.** Report both separately before pooling.
- Do not drop a level because it looks noisy — a noisy level is the finding.
- Report paired *and* unpaired floors at every level, so the value of pairing is visible
  rather than asserted at "~15%".
- Report per-metric floors, not one overall number. The 2026-08-22 sweep's apparent wins
  were metric-specific and so is the noise.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Corrected 3-run floor materially different from 0.034** | Update CLAUDE.md, and re-read the 2026-08-22 sweep's conclusions against the corrected number. |
| **Same as 0.034** | The bug did not bite in practice. Say so plainly; a fix that changes nothing measurable is still worth having said. |
| **√n holds** | `num_runs` is a predictable dial and can be budgeted arithmetically. |
| **√n does not hold** | There is a floor that averaging cannot cross — almost certainly the judge, per Campaign 02 — and the guidance to raise `num_runs` is partly wasted spend. |
| **Floor exceeds typical effects** | The most important possible result: it would mean the sweeps this repo runs cannot resolve what they are trying to measure, and Campaign 04 must not run until the design changes. |

## Repo payoff

`minimum_detectable_effect(num_runs, n_cases)` in `wrangler/reporting/analyzer.py`, wired
into `classify_deltas()` so a report marks a sub-floor delta automatically instead of relying
on the reader to remember. Then update the CLAUDE.md numbers with the corrected
measurement — and date them.

## Cost

Moderate. Several control evals; partly served from captures, which is the cheap path.

## Result

_Not yet run._
