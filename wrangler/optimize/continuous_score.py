"""Recover GEPA's continuous per-case score from the binary one ADK hands it.

**The defect.** `local_eval_sampler.py` scores each case
``1.0 if final_eval_status == PASSED else 0.0``, and PASSED requires *every* criterion to
clear its threshold. So two collapses happen before the averaging: each metric's continuous
score is thresholded, then the criteria are ANDed. A candidate that moved response quality
from 0.50 to 0.84 scores identically to one that moved nothing.

**Measured cost of that, on campaign 09** (docs/analysis/2026-09-24-continuous-score-gradient.md):
all three archived runs selected their winning prompt from **six distinct values**, with
**29 of 37 candidates on a tied score**. A continuous composite over the same batches gives
**25 distinct values and 7 ties**. Since `gepa.core.result.best_idx` is the *first* argmax, a
tie is resolved by discovery order -- by the search's luck rather than by the prompt.

**Three of four criteria carry real gradient; `safety_v1` does not.** 54-77% of its batch means
are not expressible as k/n, proving per-case continuity, while safety's are 0% -- it is a
stable pass/fail judgement, consistent with DOE 02's 0/64 judge disagreement. So this improves
the signal GEPA *selects* with, not the metric the campaign reports.

**Unweighted mean across metrics, and that is a real choice.** The criteria carry different
thresholds (0.95 for safety, 0.85 for response quality) but no declared weights, so weighting
them would invent a preference nobody stated. The consequence worth knowing: this **does not
preserve the pass/fail ordering**. A candidate failing several thresholds narrowly can outrank
one passing them, because the mean does not know where the cliffs are. A lexicographic variant
(pass count first, continuous mean as tie-break) would preserve it and still break ties, and
is the obvious fallback if a campaign's results look driven by near-misses. It was not chosen
because the 3.1x resolution gain above was measured on the plain mean.

**Opt-in.** This changes what GEPA optimizes, so it is a comparability boundary of the same
kind as the 2026-09-17 judge re-baseline. Off unless a manifest asks.
"""

from __future__ import annotations

from typing import Any

__all__ = ["apply_continuous_scores", "continuous_case_scores"]


def _binary_fallback(case: Any) -> float:
    """ADK's own verdict, for a case that reported no metric scores at all.

    Better a coarse score than a dropped case: GEPA indexes scores by `eval_id`, so a missing
    key is not a lower score -- it is a `KeyError` partway through a nine-hour stage.
    """
    from google.adk.evaluation.eval_metrics import EvalStatus

    return 1.0 if getattr(case, "final_eval_status", None) == EvalStatus.PASSED else 0.0


def continuous_case_scores(eval_results: list[Any]) -> dict[str, float]:
    """Per case, the mean of its metrics' continuous scores, clamped to [0, 1].

    Reads the same field ADK patch 4 already reads -- `mr.score` on
    `eval_metric_result_per_invocation[].eval_metric_results[]` -- so the number this returns
    is one the repo was already computing and discarding.

    A `None` metric score counts as **0.0, not as absent**. That is patch 6's failure mode:
    an unserved metric returned `None` for every case, and averaging over the remaining
    metrics would have hidden the outage behind a plausible score. Counting it as zero makes
    a broken metric look broken.

    Clamped because GEPA compares against `perfect_score=1.0` and stops early on
    `skip_perfect_score`; a score above 1.0 would end a run for the wrong reason.
    """
    scores: dict[str, float] = {}
    for case in eval_results:
        values: list[float] = []
        for invocation in getattr(case, "eval_metric_result_per_invocation", None) or []:
            for result in getattr(invocation, "eval_metric_results", None) or []:
                raw = getattr(result, "score", None)
                values.append(0.0 if raw is None else float(raw))

        eval_id = getattr(case, "eval_id", None)
        if eval_id is None:
            continue
        scores[eval_id] = (
            min(1.0, max(0.0, sum(values) / len(values))) if values else _binary_fallback(case)
        )
    return scores


def apply_continuous_scores(scores: dict[str, float], eval_results: list[Any]) -> dict[str, float]:
    """ADK's per-case scores with the continuous ones substituted in where recoverable.

    Merged per key rather than replaced wholesale. GEPA indexes scores by `eval_id`, so a
    case the recovery could not score must keep ADK's verdict -- a dropped key is not a lower
    score, it is a `KeyError` partway through a nine-hour stage.

    Returns the input unchanged when nothing was recovered, so a batch that produced no
    metric results degrades to current behaviour instead of to an empty scoring. That falls
    out of the per-key merge rather than needing its own guard -- an explicit early return
    here was dead code, and a mutation test caught it.
    """
    recovered = continuous_case_scores(eval_results)
    return {key: recovered.get(key, value) for key, value in scores.items()}
