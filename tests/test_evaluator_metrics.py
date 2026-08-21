"""Tests for the batch-eval tool_use metric.

The predefined ``tool_use_quality_v1`` metric auto-generates rubrics server-side
while blind to the agent's tools, producing inverted rubrics that penalize
correct tool use. These tests assert that batch eval instead uses an explicit
LLM-judge metric whose criteria reward correct tool selection + parameters, and
that its score key is aliased back to ``tool_use_quality_v1`` for reporting.

No network access — all object-level inspection.
"""

import pytest
from pydantic import ValidationError
from vertexai import types

from wrangler.eval import evaluator
from wrangler.eval.evaluator import (
    _TOOL_USE_METRIC_NAME,
    _TOOL_USE_REPORT_KEY,
    DEFAULT_METRICS,
    _tool_use_metric,
)


class TestToolUseMetric:
    def test_is_llm_metric_not_predefined(self):
        m = _tool_use_metric()
        # Must be an LLMMetric carrying an explicit prompt_template, NOT the
        # bare predefined RubricMetric.TOOL_USE_QUALITY.
        assert isinstance(m, types.LLMMetric)
        assert m.prompt_template, "tool_use metric must carry an explicit prompt_template"

    def test_routes_to_llm_handler_not_predefined(self):
        """A metric named tool_use_quality_v1 would be hijacked by the predefined
        handler (ignoring the custom prompt). The metric name must avoid that."""
        from vertexai._genai import _evals_constant

        m = _tool_use_metric()
        assert m.name not in _evals_constant.SUPPORTED_PREDEFINED_METRICS, (
            f"metric name {m.name!r} collides with a predefined metric and would "
            "be routed to the predefined (auto-rubric) handler"
        )
        assert m.name == _TOOL_USE_METRIC_NAME == "tool_use_quality"

    def test_payload_uses_llm_based_spec(self):
        """The SDK transformer must emit an llm_based_metric_spec (explicit
        criteria), not a predefined_metric_spec (auto-generated rubrics)."""
        from vertexai._genai import _transformers as tr

        payload = tr.t_metrics([_tool_use_metric()])[0]
        assert "llm_based_metric_spec" in payload
        assert "predefined_metric_spec" not in payload

    def test_prompt_rewards_tool_use(self):
        """Prompt must instruct the judge to score correct tool selection +
        parameters and to NOT penalize calling tools (mirrors the GEPA sampler's
        rubric_based_tool_use_quality_v1 rubrics)."""
        text = _tool_use_metric().prompt_template.lower()
        assert "tool" in text
        assert "select" in text  # correct tool selection
        assert "parameter" in text  # correct parameters
        # Must explicitly avoid the inverted "refuse / no tool call" framing.
        assert "do not penalize" in text

    def test_prompt_requests_parseable_json(self):
        """The evaluation_run API rejects free-form judge output — the prompt
        must request a JSON object with a score field."""
        text = _tool_use_metric().prompt_template
        assert "json" in text.lower()
        assert '"score"' in text

    def test_report_key_is_v1(self):
        assert _TOOL_USE_REPORT_KEY == "tool_use_quality_v1"


class TestDefaultMetrics:
    def test_contains_other_four_predefined_metrics(self):
        """The four non-tool-use metrics must remain the bare prebuilt ones.

        Prebuilt metrics are LazyLoadedPrebuiltMetric whose .name is the
        uppercase attribute name (resolved server-side to e.g.
        final_response_quality_v1).
        """
        names = {getattr(m, "name", None) for m in DEFAULT_METRICS}
        for expected in (
            "FINAL_RESPONSE_QUALITY",
            "HALLUCINATION",
            "SAFETY",
            "INSTRUCTION_FOLLOWING",
        ):
            assert expected in names, f"{expected} missing from DEFAULT_METRICS"

    def test_other_four_are_unchanged_prebuilt_metrics(self):
        """The four non-tool-use metrics must be the bare prebuilt RubricMetric
        objects, not custom/LLM metrics."""
        for m in DEFAULT_METRICS:
            if getattr(m, "name", None) == _TOOL_USE_METRIC_NAME:
                continue
            assert not isinstance(m, types.LLMMetric)
            assert getattr(m, "prompt_template", None) in (None, "")

    def test_tool_use_is_explicit_llm_metric(self):
        tool_use = [m for m in DEFAULT_METRICS if getattr(m, "name", None) == _TOOL_USE_METRIC_NAME]
        assert len(tool_use) == 1
        assert isinstance(tool_use[0], types.LLMMetric)

    def test_no_bare_predefined_tool_use(self):
        """The inverted-rubric predefined tool_use_quality_v1 must NOT be present.

        The prebuilt TOOL_USE_QUALITY object would carry the name
        'TOOL_USE_QUALITY'; the resolved predefined name is 'tool_use_quality_v1'.
        Neither should appear in DEFAULT_METRICS."""
        names = {getattr(m, "name", None) for m in DEFAULT_METRICS}
        assert "tool_use_quality_v1" not in names
        assert "TOOL_USE_QUALITY" not in names

    def test_metric_count_unchanged(self):
        assert len(DEFAULT_METRICS) == 5


class TestScoreKeyAlias:
    def test_alias_constants_distinct(self):
        # The metric scores under "tool_use_quality"; reports read
        # "tool_use_quality_v1". The alias bridges them in run_batch_eval.
        assert _TOOL_USE_METRIC_NAME != _TOOL_USE_REPORT_KEY

    def test_alias_helper_maps_custom_name_to_report_key(self):
        """Exercise the real alias helper used by run_batch_eval and
        online_monitors (not a re-implementation of the loop)."""
        assert evaluator._alias_tool_use_key("tool_use_quality") == "tool_use_quality_v1"
        assert evaluator._alias_tool_use_key(evaluator._TOOL_USE_METRIC_NAME) == (
            evaluator._TOOL_USE_REPORT_KEY
        )
        # Other metric names pass through unchanged.
        assert evaluator._alias_tool_use_key("safety") == "safety"
        assert evaluator._alias_tool_use_key("final_response_quality_v1") == (
            "final_response_quality_v1"
        )


class _StubSummaryMetrics:
    def __init__(self, metrics):
        self.metrics = metrics


class _StubRunResults:
    def __init__(self, metrics):
        self.summary_metrics = _StubSummaryMetrics(metrics)


class _StubEvaluationRun:
    """Mirrors the attribute path run_batch_eval reads:
    evaluation_run.evaluation_run_results.summary_metrics.metrics
    """

    def __init__(self, metrics):
        self.evaluation_run_results = _StubRunResults(metrics)


class TestAggregateExtractionAliasPath:
    """Integration test of the REAL score-extraction + alias code path.

    run_batch_eval's aggregate-score block was factored into the pure helper
    ``_extract_aggregate_scores`` (same logic, no behavior change). These tests
    drive that helper with a stub eval-run object shaped exactly like the SDK's
    result so the actual extraction+alias logic runs — not a re-implementation.
    """

    def test_tool_use_average_is_aliased_to_v1(self):
        # The custom metric reports under "tool_use_quality/AVERAGE"; the
        # extraction must surface it as "tool_use_quality_v1" in the scores dict.
        run = _StubEvaluationRun({"tool_use_quality/AVERAGE": 0.87})
        scores = evaluator._extract_aggregate_scores(run)
        assert "tool_use_quality_v1" in scores
        assert "tool_use_quality" not in scores
        assert scores["tool_use_quality_v1"] == 0.87

    def test_realistic_summary_metrics_full_alias_and_passthrough(self):
        """Mirror a realistic summary_metrics payload: each metric carries an
        /AVERAGE entry (and often /STANDARD_DEVIATION, which must be ignored).
        Only the tool-use key is aliased; the rest pass through unchanged."""
        run = _StubEvaluationRun(
            {
                "final_response_quality_v1/AVERAGE": 0.72,
                "final_response_quality_v1/STANDARD_DEVIATION": 0.10,
                "hallucination_v1/AVERAGE": 0.95,
                "safety_v1/AVERAGE": 1.0,
                "instruction_following_v1/AVERAGE": 0.68,
                "tool_use_quality/AVERAGE": 0.81,
                "tool_use_quality/STANDARD_DEVIATION": 0.05,
            }
        )
        scores = evaluator._extract_aggregate_scores(run)

        assert scores == {
            "final_response_quality_v1": 0.72,
            "hallucination_v1": 0.95,
            "safety_v1": 1.0,
            "instruction_following_v1": 0.68,
            "tool_use_quality_v1": 0.81,
        }
        # Non-average entries (std dev) must not leak into scores.
        assert all("STANDARD_DEVIATION" not in k for k in scores)

    def test_handles_namespaced_metric_keys(self):
        """Some metric keys are namespaced (e.g. 'foo/tool_use_quality/AVERAGE').
        The short name is the segment before /AVERAGE, which then gets aliased."""
        run = _StubEvaluationRun({"ns/tool_use_quality/AVERAGE": 0.5})
        scores = evaluator._extract_aggregate_scores(run)
        assert scores == {"tool_use_quality_v1": 0.5}

    def test_missing_run_results_returns_empty(self):
        class _Empty:
            evaluation_run_results = None

        assert evaluator._extract_aggregate_scores(_Empty()) == {}


class TestLenientResultParsing:
    """One rater error must not cost the other four metrics.

    ``types.CandidateResult`` has no ``error`` field and inherits
    ``extra='forbid'``, so the SDK's own loader
    (``_evals_common._convert_gcs_to_evaluation_item_result``) throws away the
    whole result file when any single metric errors — and returns an empty
    object rather than raising, so the caller cannot tell. Payload shapes below
    are taken verbatim from the archived GCS results. See
    docs/notes/silent-failures.md #7.
    """

    def test_keeps_scores_alongside_an_errored_metric(self):
        payload = {
            "candidateResults": [
                {"metric": "safety_v1", "score": 1.0},
                {"metric": "tool_use_quality", "score": 0.5},
                {
                    "metric": "hallucination_v1",
                    "error": {
                        "code": 3,
                        "message": "The model response did not complete successfully.\n"
                        "Finish reason: UNEXPECTED_TOOL_CALL.",
                    },
                },
            ]
        }
        scores, errored = evaluator._scores_from_raw_result(payload)
        assert scores == {"safety_v1": 1.0, "tool_use_quality_v1": 0.5}
        assert errored == ["hallucination_v1"]

    def test_the_whole_payload_is_unparseable_to_the_sdk(self):
        """Pins the premise: without this the test above proves nothing.

        If a future SDK relaxes `extra` or adds an `error` field to
        CandidateResult, this fails and the workaround can be reconsidered.
        """
        payload = {"candidateResults": [{"metric": "m", "error": {"code": 3}}]}
        with pytest.raises(ValidationError, match="candidateResults"):
            types.EvaluationItemResult(**payload)

    def test_aliases_the_custom_tool_use_key(self):
        scores, _ = evaluator._scores_from_raw_result(
            {"candidateResults": [{"metric": "tool_use_quality", "score": 0.9}]}
        )
        assert scores == {"tool_use_quality_v1": 0.9}

    def test_strips_the_resource_path_from_metric_names(self):
        scores, _ = evaluator._scores_from_raw_result(
            {
                "candidateResults": [
                    {"metric": "projects/p/locations/l/metrics/safety_v1", "score": 1.0}
                ]
            }
        )
        assert scores == {"safety_v1": 1.0}

    def test_null_score_is_not_coerced_to_zero(self):
        """A missing score is absent, not 0.0 — ADK patch 4 taught us that one.

        Coercing None to 0.0 is how GEPA spent a whole run optimizing against a
        safety criterion pinned at zero (silent-failures #6).
        """
        scores, errored = evaluator._scores_from_raw_result(
            {"candidateResults": [{"metric": "safety_v1", "score": None}]}
        )
        assert scores == {}
        assert errored == []

    def test_tolerates_junk(self):
        assert evaluator._scores_from_raw_result({}) == ({}, [])
        assert evaluator._scores_from_raw_result({"candidateResults": None}) == ({}, [])
        assert evaluator._scores_from_raw_result({"candidateResults": []}) == ({}, [])
