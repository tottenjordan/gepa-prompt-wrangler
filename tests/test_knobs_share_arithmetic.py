"""`num_runs` and `score_repeats` must average through the same function.

`combine_results()` says in its own docstring that it is "shared by the two knobs so they
cannot drift apart". Until 2026-09-17 that was aspirational: it had exactly one call site
(the `score_repeats` path in `run_batch_eval`), while `run_batch_eval_averaged` re-implemented
the same mean / stdev / `average_per_case` / token / coverage logic inline.

The two agreed, so nothing was broken. The reason to fix it is DOE 03: its analysis
reconstructs every (`num_runs`, `score_repeats`) cell offline by calling `combine_results`,
and claims the cells reproduce production. That claim should rest on production calling the
same function, not on two implementations happening to match today.
"""

from __future__ import annotations

import pytest

from wrangler.eval import evaluator
from wrangler.eval.evaluator import EvalResult


def _result(**scores: float) -> EvalResult:
    """An EvalResult with per-case rows consistent with its aggregate scores."""
    return EvalResult(
        scores=dict(scores),
        per_case=[{evaluator.CASE_INDEX_KEY: i, **scores} for i in range(3)],
        coverage=dict.fromkeys(scores, 3),
    )


@pytest.fixture
def fake_runs(monkeypatch):
    """Make `run_batch_eval` return canned results, so no service is touched."""

    def install(results: list[EvalResult]) -> list[dict]:
        calls: list[dict] = []
        queue = list(results)

        def fake_run_batch_eval(*args, **kwargs):
            calls.append(kwargs)
            return queue.pop(0)

        monkeypatch.setattr(evaluator, "run_batch_eval", fake_run_batch_eval)
        return calls

    return install


class TestTheOuterAverageGoesThroughCombineResults:
    def test_combine_results_is_actually_called(self, fake_runs, monkeypatch):
        """The point of the task: one averaging implementation, not two."""
        fake_runs([_result(safety_v1=0.8), _result(safety_v1=1.0)])
        seen: list[list[EvalResult]] = []
        real = evaluator.combine_results

        def spy(results, label="runs"):
            seen.append(list(results))
            return real(results, label=label)

        monkeypatch.setattr(evaluator, "combine_results", spy)

        evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2)

        assert seen, (
            "run_batch_eval_averaged averaged its runs without calling combine_results -- "
            "the two knobs have separate arithmetic again, and DOE 03's resampling no "
            "longer reproduces production"
        )
        assert len(seen[-1]) == 2

    def test_it_averages_the_scores(self, fake_runs):
        fake_runs([_result(safety_v1=0.8), _result(safety_v1=1.0)])
        out = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2)
        assert out.scores["safety_v1"] == pytest.approx(0.9)

    def test_a_scoreless_run_is_skipped_not_counted_as_zero(self, fake_runs):
        """silent-failures #6: coercing a missing score to 0.0 is how a floor gets faked."""
        fake_runs([_result(safety_v1=0.8), EvalResult(), _result(safety_v1=1.0)])
        out = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=3)
        assert out.scores["safety_v1"] == pytest.approx(0.9), (
            "the empty run was averaged in as zero rather than skipped"
        )

    def test_num_runs_reports_successful_runs_not_requested_ones(self, fake_runs):
        """A run that lost a third of its passes must not report the full count.

        The inline implementation returned the *requested* num_runs; combine_results
        returns the number that produced scores. Successful is the honest one -- num_runs
        is read downstream as "how much averaging is behind this number".
        """
        fake_runs([_result(safety_v1=0.8), EvalResult(), _result(safety_v1=1.0)])
        out = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=3)
        assert out.num_runs == 2, f"reported {out.num_runs} runs behind a 2-run average"

    def test_the_spread_survives_averaging(self, fake_runs):
        """A mean over N runs hides exactly the quantity DOE 03 is measuring."""
        fake_runs([_result(safety_v1=0.8), _result(safety_v1=1.0)])
        out = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2)
        assert out.scores_std["safety_v1"] > 0

    def test_score_repeats_is_forwarded_to_every_run(self, fake_runs):
        """The knobs nest: num_runs x [one inference + score_repeats scorings]."""
        calls = fake_runs([_result(safety_v1=0.8), _result(safety_v1=1.0)])
        evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2, score_repeats=4)
        assert [c.get("score_repeats") for c in calls] == [4, 4]


class TestAllRunsFailing:
    def test_it_returns_an_empty_result_rather_than_inventing_scores(self, fake_runs):
        fake_runs([EvalResult(), EvalResult()])
        out = evaluator.run_batch_eval_averaged("engine", [{}], num_runs=2)
        assert out.scores == {}
