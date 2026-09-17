"""`score_repeats` buys judge-noise reduction without paying for inference.

DOE 02 measured the two sources of the noise floor separately, on byte-identical responses:

    instruction_following_v1   judge sd 0.024, disagrees on 64/64 cases
    safety_v1                  judge sd 0.000, disagrees on  0/64 cases

So the two metrics need opposite things, and `num_runs` was buying both at one price. A full
run costs inference **and** scoring (measured 2.6 + 2.8 min for 64 cases); a scoring repeat
costs scoring alone and touches no engine. For the holdout that is the same variance
reduction at roughly half the cost and none of the deployment risk; for safety it is worthless
and `num_runs` remains the only lever.

Hence a separate knob rather than a bigger `num_runs`.

A second effect, measured rather than assumed: different scoring passes lose *different*
cases (DOE 02 arm 4 — five passes of one prompt lost 8 cases, no two the same). Averaging
passes therefore also **recovers coverage**, because a case scored in any pass survives.
"""

from __future__ import annotations

import pytest

from wrangler.eval.evaluator import CASE_INDEX_KEY, EvalResult, combine_results


def _r(scores: dict[str, float], per_case: list[dict] | None = None) -> EvalResult:
    return EvalResult(scores=scores, per_case=per_case or [])


class TestCombineResults:
    def test_scores_are_averaged(self):
        out = combine_results([_r({"a": 0.8}), _r({"a": 1.0})], label="passes")
        assert out.scores["a"] == pytest.approx(0.9)

    def test_spread_is_kept_not_just_the_mean(self):
        """A mean over N scorings hides the quantity DOE 02 exists to measure."""
        out = combine_results([_r({"a": 0.8}), _r({"a": 1.0})], label="passes")
        assert out.scores_std["a"] > 0

    def test_a_result_with_no_scores_is_skipped_not_counted(self):
        out = combine_results([_r({"a": 1.0}), _r({})], label="passes")
        assert out.scores["a"] == pytest.approx(1.0)

    def test_all_empty_returns_an_empty_result_rather_than_raising(self):
        out = combine_results([_r({}), _r({})], label="passes")
        assert out.scores == {}

    def test_coverage_is_recovered_across_passes(self):
        """Passes drop different cases, so a case scored in ANY pass should survive.

        Measured in DOE 02 arm 4: five passes of one prompt lost 8 cases and no two passes
        lost the same one. If combining kept only the intersection it would throw away the
        main incidental benefit of scoring repeats.
        """
        a = [{CASE_INDEX_KEY: 0, "m": 1.0}, {CASE_INDEX_KEY: 1, "m": 0.5}]
        b = [{CASE_INDEX_KEY: 0, "m": 1.0}, {CASE_INDEX_KEY: 2, "m": 0.0}]
        out = combine_results([_r({"m": 0.75}, a), _r({"m": 0.5}, b)], label="passes")
        assert {row[CASE_INDEX_KEY] for row in out.per_case} == {0, 1, 2}


class TestTheKnobIsSeparateFromNumRuns:
    def test_run_batch_eval_accepts_score_repeats(self):
        import inspect

        from wrangler.eval.evaluator import run_batch_eval

        assert "score_repeats" in inspect.signature(run_batch_eval).parameters

    def test_averaged_accepts_and_forwards_it(self):
        import inspect

        from wrangler.eval.evaluator import run_batch_eval_averaged

        assert "score_repeats" in inspect.signature(run_batch_eval_averaged).parameters

    def test_default_is_one_so_nothing_changes_silently(self):
        """Turning this on re-baselines a campaign's floor; it must be opt-in."""
        import inspect

        from wrangler.eval.evaluator import run_batch_eval, run_batch_eval_averaged

        for fn in (run_batch_eval, run_batch_eval_averaged):
            assert inspect.signature(fn).parameters["score_repeats"].default == 1

    @staticmethod
    def _hermetic(monkeypatch, calls):
        """Stub everything that needs GCP. This test must not need credentials."""
        import wrangler.eval.evaluator as ev

        def fake_infer(*a, **k):
            calls["infer"] += 1
            return object(), 0.0  # (dataset, t0)

        def fake_score(*a, **k):
            calls["score"] += 1
            return _r({"m": 0.5 + 0.1 * calls["score"]}, [{CASE_INDEX_KEY: 0, "m": 1.0}])

        monkeypatch.setattr(ev, "vertex_init", lambda **k: None)
        monkeypatch.setattr(ev, "agent_client", lambda **k: object())
        monkeypatch.setattr(ev, "_resolve_resource_name", lambda e: f"projects/p/x/{e}")
        monkeypatch.setattr(ev, "_infer_for_eval", fake_infer)
        monkeypatch.setattr(ev, "_score_dataset", fake_score)
        return ev

    def test_scoring_happens_n_times_for_one_inference_pass(self, monkeypatch):
        """The whole point: N judge passes, ONE trip to the engine."""
        calls = {"infer": 0, "score": 0}
        ev = self._hermetic(monkeypatch, calls)
        ev.run_batch_eval("eng", [{"prompt": "p"}], score_repeats=3)
        assert calls == {"infer": 1, "score": 3}

    def test_one_repeat_scores_once_and_does_not_take_the_averaging_path(self, monkeypatch):
        """The default must behave exactly as before, byte for byte in call counts."""
        calls = {"infer": 0, "score": 0}
        ev = self._hermetic(monkeypatch, calls)
        ev.run_batch_eval("eng", [{"prompt": "p"}])
        assert calls == {"infer": 1, "score": 1}

    def test_num_runs_multiplies_inference_but_score_repeats_does_not(self, monkeypatch):
        """num_runs=2 with score_repeats=3 is 2 engine trips and 6 scorings, not 6 trips."""
        calls = {"infer": 0, "score": 0}
        ev = self._hermetic(monkeypatch, calls)
        ev.run_batch_eval_averaged("eng", [{"prompt": "p"}], num_runs=2, score_repeats=3)
        assert calls == {"infer": 2, "score": 6}
