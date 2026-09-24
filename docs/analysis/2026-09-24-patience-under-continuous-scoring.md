# Patience under the continuous signal: 15 still holds, and 5 would do

**One-line result:** replaying the stopper against campaign 09's *continuous* validation scores —
recovered from the optimize logs, not re-run — shows **patience 5 is lossless on both arms** where
the binary signal needed 10. The shipped default of **15 remains lossless and is therefore kept**;
what changes is that it is now known to be conservative by a factor of three, worth 4–24 points of
extra saving once a run under the full new configuration confirms it.

**This is a provisional re-derivation.** It measures the continuous signal at the *old* 15-case
subset. The 34/30 split (#131) is not in it. A run under both changes is in flight
(`run-d55b159050`) and is what settles the default.

## Why this could be done without a new run

Patch 4 logs per-metric continuous means for every batch, and campaign 09's optimize logs are
still inside Cloud Logging's 30-day window. Filtering to `Eval batch (15 cases)` — the validation
subset size, so these are the scoring passes that pick the winner — yields **35 batches**: 20 for
`c09-rationale-on` against its 21 archived candidates, 15 for `c09-rationale-off` against its 16.
One short each, because the seed's validation pass predates the log window.

**The ordering is verified, not assumed.** Batches are ordered by timestamp and matched to
candidate discoveries. If that mapping were wrong the replay would be worthless, so it is checked
against an independent quantity: the **seconds between consecutive batches** against the
**metric calls between consecutive candidate discoveries**, taken from `gepa_state.bin`.

| arm | pearson r |
| --- | --- |
| `c09-rationale-on` | **+0.971** |
| `c09-rationale-off` | **+0.993** |

Two quantities from two independent sources agreeing that closely is what licenses the rest.

## Result

| patience | rationale-on: saved / returns | rationale-off: saved / returns |
| --- | --- | --- |
| 3 | 92.5% / same | 89.0% / **different** |
| **5** | **92.5% / same** | **85.0% / same** |
| 8 | 83.1% / same | 78.5% / same |
| 10 | 79.1% / same | 70.0% / same |
| **15** (shipped) | **79.1% / same** | **61.0% / same** |

Under the binary signal the same two arms needed patience 8 and 10 respectively
([the original replay](2026-09-24-stopping-replay.md)). Under the continuous signal they need 3
and 5. **Higher resolution identifies the winner sooner**, which is what more distinguishable
levels should do.

## The finding that matters more than the patience number

**The binary and continuous signals rank candidates almost independently.** Correlating the
archived per-candidate binary aggregate against the continuous composite over the same candidates:

| arm | pearson r |
| --- | --- |
| `c09-rationale-on` | **+0.071** |
| `c09-rationale-off` | **+0.274** |

This is not a data artifact — the ordering check above rules that out. It is the measured form of
the caveat that shipped with option A: *the unweighted mean does not preserve pass/fail ordering*.
It was written as a theoretical concern. It is not theoretical; on this campaign the two signals
agree on candidate ranking barely more than chance.

**So option A is a bigger change than "more resolution".** It selects substantially different
prompts, not merely the same ones more finely separated. That raises the value of the planned
validation — running one arm with continuous scoring beside one without, in a single job — from a
nice-to-have to the thing that should gate adopting it as a default.

## Decision

**Keep patience 15.** It is lossless under both signals on every arm measured — three binary
(including the unsaturated m01) and two continuous. Nothing here says it is wrong, only that it is
conservative.

**Do not cut it to 5 on this evidence.** Two reasons. The `rationale-on` arm is uninformative: its
best candidate is the first one in the log window, so every patience returns it and the arm cannot
discriminate. That leaves **one informative arm**. And this is the wrong validation subset — 15
cases, not the 30 that ship.

Revisit when `run-d55b159050` lands, with `scripts/replay_stopping.py` over its trajectory.

## Caveats

- **The seed candidate is excluded** from both reconstructions; its validation pass predates the
  log window. It is never the winner in either arm, but the trajectories are 20 and 15 candidates
  rather than 21 and 16.
- **Composite is an unweighted mean** of the four per-metric batch means, matching what option A
  implements — but it is a mean of *batch means*, one number per metric per batch, not per-case
  scores. It cannot distinguish a batch where one case failed badly from one where several drifted.
- **One campaign, two arms, one of them uninformative.**
