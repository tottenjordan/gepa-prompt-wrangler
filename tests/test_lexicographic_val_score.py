"""Tests for the lexicographic validation score: pass/fail first, continuous mean as tie-break.

The plain continuous mean (option A) was validated on 2026-09-26 and not adopted: it selected
*different* prompts rather than better ones, and it does not preserve the pass/fail ordering.
This variant keeps ADK's ordering exactly and uses the continuous mean only among candidates
passing the same number of cases. See docs/analysis/2026-09-26-onoff-validation-result.md.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from typing import ClassVar

import pytest
import yaml

from wrangler.core.factory import PairFactory
from wrangler.optimize.continuous_score import (
    LEXICOGRAPHIC_EPSILON,
    apply_lexicographic_scores,
)


def _case(eval_id, score, passed):
    """An EvalCaseResult-alike with one metric, so its continuous mean is `score`."""
    from google.adk.evaluation.eval_metrics import EvalStatus

    return SimpleNamespace(
        eval_id=eval_id,
        final_eval_status=EvalStatus.PASSED if passed else EvalStatus.FAILED,
        eval_metric_result_per_invocation=[
            SimpleNamespace(eval_metric_results=[SimpleNamespace(metric_name="q", score=score)])
        ],
    )


def _aggregate(cases):
    """Score a candidate the way GEPA does: the mean of its per-case scores."""
    adk = {c.eval_id: 1.0 if c.final_eval_status.name == "PASSED" else 0.0 for c in cases}
    return mean(apply_lexicographic_scores(adk, cases).values())


class TestItPreservesThePassFailOrdering:
    """The property the plain mean lacked, and the reason this variant exists."""

    @pytest.mark.parametrize("n", [3, 30, 98])
    def test_one_more_pass_beats_any_continuous_advantage(self, n):
        """Worst case for the ordering: the candidate with one more pass has every other
        case at 0.0, the other has every failing case at a near-miss of 0.99 and every
        passing case perfect. The plain mean ranks the second higher; this must not."""
        more = [_case(f"c{i}", 1.0 if i == 0 else 0.0, passed=i == 0) for i in range(n)]
        fewer = [_case(f"c{i}", 0.99, passed=False) for i in range(n)]
        assert _aggregate(more) > _aggregate(fewer)

    def test_the_bound_is_real_and_not_accidental(self):
        """Past n = (1 - e) / e the tie-break can outweigh a pass, so the documented limit
        is the actual one. If this stops failing, the epsilon changed and the docstring's
        bound needs re-deriving."""
        n = round((1 - LEXICOGRAPHIC_EPSILON) / LEXICOGRAPHIC_EPSILON) + 2
        more = [_case(f"c{i}", 1.0 if i == 0 else 0.0, passed=i == 0) for i in range(n)]
        fewer = [_case(f"c{i}", 1.0, passed=False) for i in range(n)]
        assert _aggregate(more) < _aggregate(fewer)


class TestItBreaksTies:
    def test_equal_pass_counts_are_separated_by_the_continuous_mean(self):
        """Under binary scoring both are 1/3, and `best_idx` would take whichever GEPA
        discovered first."""
        near = [_case("a", 1.0, True), _case("b", 0.84, False), _case("c", 0.80, False)]
        far = [_case("a", 1.0, True), _case("b", 0.10, False), _case("c", 0.05, False)]
        assert _aggregate(near) > _aggregate(far)

    def test_only_a_perfect_case_scores_one(self):
        """So GEPA's all-perfect-minibatch skip fires on the same cases as under the plain
        mean: a pass with headroom left is not perfect."""
        got = apply_lexicographic_scores(
            {"perfect": 1.0, "headroom": 1.0},
            [_case("perfect", 1.0, True), _case("headroom", 0.9, True)],
        )
        assert got["perfect"] == 1.0
        assert got["headroom"] < 1.0


class TestSubstitutionSemantics:
    def test_a_case_the_recovery_missed_keeps_adks_verdict(self):
        got = apply_lexicographic_scores({"a": 1.0, "b": 0.0}, [_case("a", 0.5, True)])
        assert got["b"] == 0.0
        assert set(got) == {"a", "b"}

    def test_an_unrecoverable_batch_is_exactly_binary(self):
        adk = {"a": 1.0, "b": 0.0}
        assert apply_lexicographic_scores(adk, []) == adk

    def test_it_never_invents_a_case_adk_did_not_score(self):
        got = apply_lexicographic_scores(
            {"a": 1.0}, [_case("a", 1.0, True), _case("stranger", 0.9, True)]
        )
        assert set(got) == {"a"}


class TestManifestPlumbing:
    def _load(self, tmp_path, pair_extra=None, top_extra=None):
        doc = {
            "name": "t",
            "agent_module": "agents.example_agent",
            "pairs": [
                {"id": "arm", "model": "gemini-3.5-flash", "system_prompt": "hi"}
                | (pair_extra or {})
            ],
            **(top_extra or {}),
        }
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(doc))
        return PairFactory.load(path)

    def test_it_is_off_by_default(self, tmp_path):
        assert self._load(tmp_path).pairs[0].lexicographic_val_score is False

    @pytest.mark.parametrize("section", ["defaults", "pipeline"])
    def test_a_campaign_can_turn_it_on_once(self, tmp_path, section):
        m = self._load(tmp_path, top_extra={section: {"lexicographic_val_score": True}})
        assert m.pairs[0].lexicographic_val_score is True

    def test_it_cannot_be_combined_with_the_plain_mean(self, tmp_path):
        """One case cannot carry two scores; silently preferring one would run a different
        experiment from the one the manifest describes."""
        with pytest.raises(ValueError, match="arm"):
            self._load(tmp_path, {"continuous_val_score": True, "lexicographic_val_score": True})

    def test_a_pair_can_opt_out_of_a_campaign_default_to_use_the_other(self, tmp_path):
        m = self._load(
            tmp_path,
            {"continuous_val_score": True, "lexicographic_val_score": False},
            {"defaults": {"lexicographic_val_score": True}},
        )
        assert (m.pairs[0].continuous_val_score, m.pairs[0].lexicographic_val_score) == (
            True,
            False,
        )

    def test_it_reaches_the_pipeline_payload(self, tmp_path):
        from wrangler.pipeline.deploy_pipeline import _pairs_json

        m = self._load(tmp_path, {"lexicographic_val_score": True})
        assert _pairs_json(m)[0]["lexicographic_val_score"] is True

    def test_an_arm_without_it_keeps_its_pipeline_payload_unchanged(self, tmp_path):
        """`pair_json` is a KFP input. A new key on every pair would change every existing
        manifest's inputs and cost a full-price rerun of every cached stage."""
        from wrangler.pipeline.deploy_pipeline import _pairs_json

        assert "lexicographic_val_score" not in _pairs_json(self._load(tmp_path))[0]

    #: Manifests that turn it on deliberately; see the matching guard for the plain mean.
    DELIBERATELY_ON: ClassVar[set[str]] = {
        # Its first real run, as one arm beside three binary replicates and a control.
        # docs/analysis/2026-09-29-campaign-10-design.md
        "campaign-10_manifest.yaml",
    }

    def test_no_manifest_acquires_lexicographic_scoring_by_accident(self):
        on = {
            path.name
            for path in sorted(Path("manifests").glob("*.yaml"))
            if any(p.lexicographic_val_score for p in PairFactory.load(path).pairs)
        }
        assert on == self.DELIBERATELY_ON


class TestTheOptimizerRejectsBoth:
    def test_before_touching_adk(self, monkeypatch):
        """Checked first, so a bad call fails in milliseconds rather than after patching
        ADK and starting MCP sessions."""
        from wrangler.optimize import optimizer

        def _must_not_run(**_):
            raise AssertionError("patched ADK before validating its arguments")

        monkeypatch.setattr(optimizer, "_patch_adk", _must_not_run)
        with pytest.raises(ValueError, match="mutually exclusive"):
            optimizer.optimize(
                agent_module_path="unused",
                continuous_val_score=True,
                lexicographic_val_score=True,
            )
