"""Tests for recovering GEPA's continuous per-case score.

ADK collapses every case to `1.0 if PASSED else 0.0` before averaging, where PASSED requires
every criterion to clear its threshold. Measured on campaign 09: that left all three archived
runs selecting a winning prompt from six distinct values, with 29 of 37 candidates sitting on
a tied score. Recovering the underlying continuous scores gives 25 distinct values and 7 ties.
See docs/analysis/2026-09-24-continuous-score-gradient.md.

The arithmetic is pure and duck-typed, so these run without ADK objects.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from wrangler.optimize.continuous_score import continuous_case_scores


def _metric(name, score):
    return SimpleNamespace(metric_name=name, score=score)


def _case(eval_id, metrics, passed=True):
    """An EvalCaseResult-alike: metric results nested per invocation."""
    from google.adk.evaluation.eval_metrics import EvalStatus

    return SimpleNamespace(
        eval_id=eval_id,
        final_eval_status=EvalStatus.PASSED if passed else EvalStatus.FAILED,
        eval_metric_result_per_invocation=[
            SimpleNamespace(eval_metric_results=[_metric(n, s) for n, s in metrics.items()])
        ],
    )


class TestTheScoreIsContinuous:
    def test_it_averages_the_metric_scores(self):
        got = continuous_case_scores([_case("a", {"safety_v1": 1.0, "hallucinations_v1": 0.5})])
        assert got["a"] == pytest.approx(0.75)

    def test_a_near_miss_outranks_a_clear_miss(self):
        """The whole point. Under ADK's collapse both are 0.0, so a candidate that improved
        one from 0.50 to 0.84 scored identically to one that changed nothing."""
        got = continuous_case_scores(
            [
                _case("near", {"q": 0.84}, passed=False),
                _case("clear", {"q": 0.10}, passed=False),
            ]
        )
        assert got["near"] > got["clear"]

    def test_it_averages_across_invocations_too(self):
        case = SimpleNamespace(
            eval_id="multi",
            final_eval_status=None,
            eval_metric_result_per_invocation=[
                SimpleNamespace(eval_metric_results=[_metric("q", 1.0)]),
                SimpleNamespace(eval_metric_results=[_metric("q", 0.0)]),
            ],
        )
        assert continuous_case_scores([case])["multi"] == pytest.approx(0.5)


class TestItStaysInRange:
    def test_scores_are_bounded_to_the_unit_interval(self):
        """GEPA compares these against `perfect_score=1.0`; a value above it would make the
        run stop early on `skip_perfect_score`."""
        got = continuous_case_scores([_case("a", {"q": 5.0}), _case("b", {"q": -2.0})])
        assert 0.0 <= got["a"] <= 1.0
        assert 0.0 <= got["b"] <= 1.0


class TestMissingData:
    def test_a_none_metric_score_counts_as_zero_not_as_absent(self):
        """Patch 6's failure mode: an unserved metric returned None, and treating it as
        absent would silently average over the remaining metrics and hide the outage."""
        got = continuous_case_scores([_case("a", {"good": 1.0, "broken": None})])
        assert got["a"] == pytest.approx(0.5)

    def test_a_case_with_no_metric_results_falls_back_to_the_binary_verdict(self):
        """Better a coarse score than a dropped case: GEPA indexes scores by eval_id and a
        missing key is not a lower score, it is a KeyError mid-run."""
        from google.adk.evaluation.eval_metrics import EvalStatus

        empty_pass = SimpleNamespace(
            eval_id="p", final_eval_status=EvalStatus.PASSED, eval_metric_result_per_invocation=[]
        )
        empty_fail = SimpleNamespace(
            eval_id="f", final_eval_status=EvalStatus.FAILED, eval_metric_result_per_invocation=[]
        )
        got = continuous_case_scores([empty_pass, empty_fail])
        assert got["p"] == 1.0
        assert got["f"] == 0.0

    def test_every_case_gets_a_score(self):
        cases = [_case(str(i), {"q": 0.5}) for i in range(5)]
        assert set(continuous_case_scores(cases)) == {"0", "1", "2", "3", "4"}

    def test_an_empty_batch_is_an_empty_mapping(self):
        assert continuous_case_scores([]) == {}


class TestResolutionGain:
    def test_it_separates_candidates_the_binary_collapse_ties(self):
        """Reproduces the measured effect in miniature: two candidates that both fail every
        threshold are indistinguishable under ADK's scoring and ordered under this one."""
        from google.adk.evaluation.eval_metrics import EvalStatus

        def batch(scores):
            return [
                SimpleNamespace(
                    eval_id=f"case{i}",
                    final_eval_status=EvalStatus.FAILED,
                    eval_metric_result_per_invocation=[
                        SimpleNamespace(eval_metric_results=[_metric("q", s)])
                    ],
                )
                for i, s in enumerate(scores)
            ]

        weak = continuous_case_scores(batch([0.1, 0.2, 0.1]))
        strong = continuous_case_scores(batch([0.8, 0.84, 0.79]))
        binary = [0.0, 0.0, 0.0]
        assert sum(binary) == 0.0
        assert sum(strong.values()) > sum(weak.values())


class TestManifestPlumbing:
    """Off by default everywhere, and reaching the optimizer when asked."""

    def _manifest(self, tmp_path, pair_extra=None, top_extra=None):
        import yaml

        doc = {
            "name": "t",
            "agent_module": "agents.example_agent",
            "pairs": [
                {
                    "id": "arm",
                    "model": "gemini-3.5-flash",
                    "system_prompt": "hi",
                    **(pair_extra or {}),
                }
            ],
            **(top_extra or {}),
        }
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(doc))
        return path

    def test_it_is_off_by_default(self, tmp_path):
        from wrangler.core.factory import PairFactory

        m = PairFactory.load(self._manifest(tmp_path))
        assert m.pairs[0].continuous_val_score is False

    def test_a_pair_can_turn_it_on(self, tmp_path):
        from wrangler.core.factory import PairFactory

        m = PairFactory.load(self._manifest(tmp_path, {"continuous_val_score": True}))
        assert m.pairs[0].continuous_val_score is True

    @pytest.mark.parametrize("section", ["defaults", "pipeline"])
    def test_a_campaign_can_turn_it_on_once(self, tmp_path, section):
        """Both run paths read their own section; accepting either avoids the key depending
        on how the campaign is launched."""
        from wrangler.core.factory import PairFactory

        m = PairFactory.load(
            self._manifest(tmp_path, top_extra={section: {"continuous_val_score": True}})
        )
        assert m.pairs[0].continuous_val_score is True

    def test_a_pair_can_override_the_campaign_default_to_off(self, tmp_path):
        """So one arm can run the old scoring alongside the new in a single job -- which is
        how this gets validated without spending two campaigns."""
        from wrangler.core.factory import PairFactory

        m = PairFactory.load(
            self._manifest(
                tmp_path,
                pair_extra={"continuous_val_score": False},
                top_extra={"defaults": {"continuous_val_score": True}},
            )
        )
        assert m.pairs[0].continuous_val_score is False

    def test_it_reaches_the_pipeline_payload(self, tmp_path):
        from wrangler.core.factory import PairFactory
        from wrangler.pipeline.deploy_pipeline import _pairs_json

        m = PairFactory.load(self._manifest(tmp_path, {"continuous_val_score": True}))
        assert _pairs_json(m)[0]["continuous_val_score"] is True

    #: Manifests that turn continuous scoring on deliberately. Naming them here is the
    #: point: the flag re-baselines a campaign, so acquiring it must be an edit someone
    #: made on purpose and defended, not a default that drifted.
    DELIBERATELY_ON: ClassVar[set[str]] = {
        # Measures the plateau under the new signal so patience can be re-derived.
        # docs/analysis/2026-09-24-patience-under-continuous-scoring.md
        "patience-rederive_manifest.yaml",
    }

    def test_no_manifest_acquires_continuous_scoring_by_accident(self):
        """It re-baselines a campaign, so every manifest carrying it must be listed above."""
        from wrangler.core.factory import PairFactory

        on = {
            path.name
            for path in sorted(Path("manifests").glob("*.yaml"))
            if any(p.continuous_val_score for p in PairFactory.load(path).pairs)
        }
        assert on == self.DELIBERATELY_ON, (
            f"unexpected: {sorted(on - self.DELIBERATELY_ON)}; "
            f"no longer on: {sorted(self.DELIBERATELY_ON - on)}"
        )

    def test_the_measurement_manifest_sets_no_patience(self):
        """It has to run past its own plateau for the replay to see where a stopper would
        fire; a patience would truncate the trajectory that is the point of the run."""
        from wrangler.core.factory import PairFactory

        m = PairFactory.load("manifests/patience-rederive_manifest.yaml")
        assert all(p.patience is None for p in m.pairs)


class TestSubstitutionSemantics:
    """What the optimizer wrapper does with the recovered scores."""

    def test_a_case_the_recovery_missed_keeps_adks_verdict(self):
        """GEPA indexes scores by eval_id; a dropped key is a KeyError nine hours in, not a
        lower score."""
        from wrangler.optimize.continuous_score import apply_continuous_scores

        got = apply_continuous_scores({"a": 1.0, "b": 0.0}, [_case("a", {"q": 0.73})])
        assert got == {"a": pytest.approx(0.73), "b": 0.0}

    def test_an_unrecoverable_batch_degrades_to_adks_scoring(self):
        """Empty recovery must not blank the batch -- it must leave the run exactly as it
        would have been without this feature."""
        from wrangler.optimize.continuous_score import apply_continuous_scores

        adk = {"a": 1.0, "b": 0.0}
        assert apply_continuous_scores(adk, []) == adk

    def test_it_never_invents_a_case_adk_did_not_score(self):
        """The returned mapping must have exactly ADK's keys; GEPA iterates the batch it
        asked for."""
        from wrangler.optimize.continuous_score import apply_continuous_scores

        got = apply_continuous_scores(
            {"a": 1.0}, [_case("a", {"q": 0.5}), _case("stranger", {"q": 0.9})]
        )
        assert set(got) == {"a"}

    def test_recovery_over_a_real_shaped_batch_covers_every_case(self):
        cases = [_case(f"c{i}", {"safety_v1": 1.0, "q": 0.5 + i / 100}) for i in range(15)]
        got = continuous_case_scores(cases)
        assert len(got) == 15
        assert len(set(got.values())) == 15, "should separate cases the binary collapse ties"
