# Campaign 08 — What does a GEPA budget buy?

**Status:** Conditional — runs only if Campaign 06 shows the floor sits below the
effects Campaign 07 finds.

## Question

`max_metric_calls: 800` came from a **single observation**: generation 1 of one
run used 102 calls in 602s, so 800 buys "roughly eight generations". That is a
reasonable guess that has never been tested.

## Design

`max_metric_calls ∈ {200, 600, 1200}` on the best model from Campaign 07, plus a
mandatory unchanged-prompt control. One optimize arm per batch, paired against a
Gemini eval-only control arm so the two concurrent pipelines still straddle both
publishers.

Report generations completed per arm, not only calls spent — the interesting
quantity is whether GEPA was still finding improvements when the budget ran out
or had plateaued long before.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| Gains flatten before 600 | 800 is overspend. Lower the manifest default and get wall-clock back |
| Still climbing at 1200 | 800 is underspend, and the 2026-08-22 sweep understated what GEPA can do |
| All arms inside the floor | The substrate cannot resolve budget effects. Report that and stop |
| Non-monotonic | Search variance dominates budget at this scale, so single-arm sweeps of any budget are not interpretable |

## Pre-registration

- Control arm **first, as a gate** (CLAUDE.md). If its deltas exceed the effects
  being chased, stop and report that.
- Arms sequential within a publisher; concurrency only ever across publishers.
- Same seed, same eval set, same `num_runs` across arms.

## Result

_Not yet run._
