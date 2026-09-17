"""DOE 03's resampled cells must BE production averaging, not a model of it.

The campaign's whole efficiency argument is that one pool of captures answers the entire
(`num_runs`, `score_repeats`) surface, because each cell is rebuilt offline. That is only
legitimate if a rebuilt cell is numerically identical to what `run_batch_eval_averaged`
would have produced live from the same inputs.

If the resampler and production ever disagree, every number DOE 03 reports is a fact about
`scripts/analyze_doe03.py` rather than about the knobs -- which is the same class of error
as `scripts/repro_mcp_refresh_hang.py` measuring its own under-modelling and reporting it
as a property of production.
"""

from __future__ import annotations

import random

import pytest

from scripts.analyze_doe03 import cost_min, side, splits
from wrangler.eval import evaluator
from wrangler.eval.evaluator import EvalResult


def _result(value: float, n_cases: int = 4) -> EvalResult:
    return EvalResult(
        scores={"safety_v1": value, "instruction_following_v1": value / 2},
        per_case=[
            {evaluator.CASE_INDEX_KEY: i, "safety_v1": value, "instruction_following_v1": value / 2}
            for i in range(n_cases)
        ],
        coverage={"safety_v1": n_cases, "instruction_following_v1": n_cases},
    )


class TestTheResamplerReproducesProduction:
    def test_a_num_runs_3_side_equals_run_batch_eval_averaged(self, monkeypatch):
        """The load-bearing equivalence, at the production default."""
        runs = [_result(0.6), _result(0.8), _result(1.0)]

        queue = list(runs)
        monkeypatch.setattr(evaluator, "run_batch_eval", lambda *a, **k: queue.pop(0))
        live = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=3)

        # Same three results, arranged as a pool of 3 captures x 1 scoring each.
        grid = [[r] for r in runs]
        rebuilt = side(grid, (0, 1, 2), s=1, rng=random.Random(0))

        assert rebuilt.scores == pytest.approx(live.scores), (
            "a resampled num_runs=3 cell does not match what production would produce "
            "from the same inputs -- DOE 03's cells would be about the script"
        )
        assert rebuilt.num_runs == live.num_runs

    def test_the_per_case_rows_match_too(self, monkeypatch):
        """Paired floors read per-case rows, so those have to agree as well."""
        runs = [_result(0.6), _result(1.0)]
        queue = list(runs)
        monkeypatch.setattr(evaluator, "run_batch_eval", lambda *a, **k: queue.pop(0))
        live = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2)

        rebuilt = side([[r] for r in runs], (0, 1), s=1, rng=random.Random(0))
        assert len(rebuilt.per_case) == len(live.per_case)
        for got, want in zip(rebuilt.per_case, live.per_case, strict=True):
            assert got == pytest.approx(want)

    def test_score_repeats_averages_within_a_capture_before_across_captures(self):
        """The knobs nest, and the order is not interchangeable in general.

        Production is num_runs x [one inference + score_repeats scorings], so scorings of
        ONE capture are averaged first. With equal case coverage the two orders coincide
        numerically; this pins the structure so a future change to combine_results that
        breaks the equivalence is caught here rather than in a campaign.
        """
        from wrangler.eval.evaluator import combine_results

        grid = [[_result(0.6), _result(0.8)], [_result(1.0), _result(0.4)]]
        rebuilt = side(grid, (0, 1), s=2, rng=random.Random(0))

        inner = [combine_results(row, label="scoring passes") for row in grid]
        expected = combine_results(inner, label="runs")
        assert rebuilt.scores == pytest.approx(expected.scores)


class TestSplits:
    def test_splits_are_disjoint(self):
        for before, after in splits(6, 3):
            assert not set(before) & set(after)

    def test_the_documented_split_counts_hold(self):
        """The plan budgets on these; a change means the pool size was mis-sized."""
        assert len(splits(6, 1)) == 15
        assert len(splits(6, 2)) == 45
        assert len(splits(6, 3)) == 10

    def test_each_unordered_pair_appears_once(self):
        """before/after is a symmetric comparison -- counting both orders doubles nothing."""
        seen = {frozenset((b, a)) for b, a in splits(6, 2)}
        assert len(seen) == len(splits(6, 2))


class TestCostModel:
    def test_the_iso_cost_cells_really_are_iso_cost(self):
        """The campaign's headline contrast is only meaningful if the costs match."""
        assert cost_min(2, 1) == pytest.approx(10.8)
        assert cost_min(1, 3) == pytest.approx(11.0)
        for r, s in [(3, 1), (2, 2), (1, 5)]:
            assert cost_min(r, s) == pytest.approx(16.4, abs=0.3), (
                f"(r={r},s={s}) costs {cost_min(r, s):.1f} min, outside the ~16.5 min set"
            )

    def test_cost_scales_with_both_knobs(self):
        assert cost_min(2, 1) > cost_min(1, 1)
        assert cost_min(1, 2) > cost_min(1, 1)
