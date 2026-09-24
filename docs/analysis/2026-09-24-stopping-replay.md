# Early stopping would have been free on campaign 09 — and the reason is a defect

**One-line result:** replaying gepa's own `NoImprovementStopper` against campaign 09's archived
optimize runs shows patience ≥ 10 returns a **byte-identical prompt** while spending **57.5% and
79.1% less** of the metric-call budget — because both arms hit the ceiling of their 15-case
validation subset early and then searched for another 50–77 iterations against a signal that could
no longer rank anything.

Reproduce with `uv run python scripts/replay_stopping.py` (no GCS needed; trajectories are
committed at `tests/fixtures/gepa_trajectories/`).

## Method

`wrangler/pipeline/components.py` archives GEPA's run_dir for every arm, so
`gs://…/pipeline-runs/run-86239e1924/stages/optimize/gepa_run/{arm}/gepa_state.bin` still exists
for both of campaign 09's optimizing arms. (The control ran no optimize stage.)

The replay **drives gepa's real stopper against gepa's real state** rather than imitating either:

- `gepa_state.bin` is a plain pickle of `GEPAState.__dict__` (everything except `_budget_hooks`),
  so it rehydrates via `object.__new__` + `__dict__.update`.
- `program_full_scores_val_set` — the only attribute `NoImprovementStopper` reads — is a *property*
  over `prog_candidate_val_subscores`, so the score list is recomputed by gepa's code.
- gepa polls the stopper at the **top** of the loop (`while not self._should_stop(state)`), so at
  iteration `i` only candidates discovered strictly before `i` are visible. Candidates are
  appended in discovery order, so truncating the score list to a prefix reproduces the state
  exactly.

Getting that last point wrong by one iteration moves every number below, so
`tests/test_trajectory.py` pins it, along with the fact that the stopper's internal `try/except`
would **silently return False forever** if the state object stopped exposing the attribute it reads
— a bug that would have looked like a legitimate "never fires" result.

## Result

All three archived runs in the project — campaign 09's two arms and m01. Campaigns 07 and 08
predate run_dir archiving, so this is every optimize trajectory that still exists.

| arm | iterations | metric calls | candidates | best candidate | first reached |
| --- | --- | --- | --- | --- | --- |
| `c09-rationale-on` | 64 | 603 | 21 | 4 (score 1.0000) | iteration 7, 102 calls |
| `c09-rationale-off` | 91 | 600 | 16 | 6 (score 1.0000) | iteration 14, 159 calls |
| `m01-rationale-merge` | 57 | 603 | 22 | 4 (score **0.9333**) | iteration 5, 150 calls |

| patience | rationale-on | rationale-off | m01 |
| --- | --- | --- | --- |
| 3 | 92.5% / **different** | 85.0% / **different** | 89.6% / **different** |
| 5 | 87.1% / **different** | 85.0% / **different** | 75.1% / same |
| 8 | 79.1% / same | 78.5% / **different** | 75.1% / same |
| **10** | **79.1% / same** | **57.5% / same** | **68.2% / same** |
| **15** | **66.7% / same** | **57.5% / same** | **68.2% / same** |

"Same" means the candidate GEPA returns is the same index, hence the same prompt, hence the same
campaign result. This is not a statistical claim — it is an identity.

**Why it is an identity.** `gepa.core.result.best_idx` is
`max(range(len(scores)), key=scores.__getitem__)`, and Python's `max` returns the **first** maximal
element. So the winner is fixed the moment the best score is first achieved. Everything after that
can only tie, never displace.

## The finding that qualifies the result

**Two of the three runs saturated their validation subset.** The subset is 15 cases, so it
resolves 1/15 = 0.0667 and tops out at 1.0000 — which both campaign 09 arms reached, at iteration
7 of 64 and 14 of 91. **73–83% of those two budgets was spent in a regime where the selection
signal could not distinguish one candidate from another.**

**m01 is the counter-example, and it is a near miss rather than a refutation**: it topped out at
0.9333, one case short of the ceiling, having reached that at iteration 5 of 57. So the signal was
not formally saturated, but it still could not improve for 52 of 57 iterations. Across the whole
useful range a 15-case subset admits only about six distinct values, which is the underlying
problem in both shapes.

That reframes the headline. Early stopping is not detecting convergence here; it is detecting that
the instrument ran out of range. The 62% pooled saving is real and bankable, but it is the size of
a measurement defect, not evidence that GEPA converges early in general.

Two consequences worth carrying forward:

1. **This plausibly feeds the run-to-run spread.** CLAUDE.md records two runs of one manifest
   differing by up to **12.3× the control floor**, with no explanation. On a saturated subset the
   returned prompt is whichever candidate *first* scored 15/15 — and which candidate that is, is
   stochastic. A tie-break on a coarse, saturated signal is an amplifier for exactly that kind of
   spread. This is a hypothesis, not a measurement; it is testable with the replicates this work
   is funding.
2. **The unsaturated case now has one data point, and the default survives it.** m01 never
   reached the ceiling and patience 15 still returns its winning candidate, at 68.2% saved. That
   was the gap this analysis originally flagged as having no evidence behind it; one run is not
   many, but it is no longer zero.

## Decision

**Default patience = 15**, and it stays opt-in.

Both 10 and 15 are lossless on all three runs. 15 is preferred because the observed threshold is
**run-dependent** — 5 on m01, 8 on one campaign 09 arm, 10 on the other — so patience 10 cleared
one run with zero margin. 15 clears all three by 5 to 10 iterations and costs about 4 percentage
points of pooled saving (64.1% versus 68.3%). With three runs behind the number, margin is worth
more than the five points.

Pooled across all three: 1,158 of 1,806 metric calls saved, **64.1%**.

## Caveats

- **Three runs, two campaigns.** Every archived trajectory in the project is used here, which is
  as much evidence as exists rather than as much as one would want. Re-run the replay once
  replicated runs exist; the script takes new trajectories as arguments.
- **The saving is optimize-stage only.** A replicate also costs a deploy, a health gate, a
  redeploy and two eval sides, so this does not fund a blanket n=2. See the plan for the
  arithmetic.
- **`gepa_state.bin` is a pickle of gepa's internals** and may not survive a gepa bump. The
  extracted trajectories are JSON for that reason, and are what future comparisons should use.
