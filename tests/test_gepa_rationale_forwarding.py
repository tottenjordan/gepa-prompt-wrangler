"""GEPA's reflector must see WHY a rubric failed, not just the number.

ADK's `LocalEvalSampler._extract_eval_data` builds the reflective dataset and emits, per
metric, `{metric_name, score, eval_status}` -- while the object it reads from carries
`EvalMetricResult.details.rubric_scores[].rationale`, a populated per-rubric explanation. It
drops it. So the model writing every candidate prompt is told
"instruction_following_v1: 0.71" and never "asked for one line, returned six".

That is the input GEPA's whole method rests on: the metric's textual feedback is read
straight into the reflection prompt, and a metric returning only pass/fail starves the
reflection step. It is also a mechanism for two things measured here and never explained --
criterion-up/holdout-down across five arms, and prompts growing 78 -> 3,873 characters, which
is what search looks like when it cannot see what is wrong.

These tests pin the enrichment. **They do not show it helps** -- only a real optimize stage's
holdout delta can, and the confound with `use_merge` is recorded in the analysis doc.
"""

from __future__ import annotations

import types as pytypes

import pytest


class _Rubric:
    def __init__(self, rid, score, rationale):
        self.rubric_id, self.score, self.rationale = rid, score, rationale


def _metric_result(name, score, rubrics=None):
    details = pytypes.SimpleNamespace(rubric_scores=rubrics) if rubrics is not None else None
    return pytypes.SimpleNamespace(
        metric_name=name, score=score, details=details, rubric_scores=rubrics
    )


@pytest.fixture
def enrich():
    from wrangler.optimize.optimizer import _enrich_with_rationales

    return _enrich_with_rationales


class TestRationaleReachesTheReflector:
    def test_a_rationale_is_attached_to_its_metric(self, enrich):
        extracted = {
            "c1": {"invocations": [{"eval_metric_results": [{"metric_name": "m", "score": 0.5}]}]}
        }
        results = [
            pytypes.SimpleNamespace(
                eval_id="c1",
                eval_metric_result_per_invocation=[
                    pytypes.SimpleNamespace(
                        eval_metric_results=[
                            _metric_result(
                                "m", 0.5, [_Rubric("ADHERENCE", 0.0, "returned six lines")]
                            )
                        ]
                    )
                ],
            )
        ]
        out = enrich(extracted, results)
        got = out["c1"]["invocations"][0]["eval_metric_results"][0]
        assert got["rubric_scores"] == [
            {"rubric_id": "ADHERENCE", "score": 0.0, "rationale": "returned six lines"}
        ]

    def test_metrics_without_rubrics_are_left_alone(self, enrich):
        extracted = {
            "c1": {"invocations": [{"eval_metric_results": [{"metric_name": "m", "score": 1.0}]}]}
        }
        results = [
            pytypes.SimpleNamespace(
                eval_id="c1",
                eval_metric_result_per_invocation=[
                    pytypes.SimpleNamespace(eval_metric_results=[_metric_result("m", 1.0, None)])
                ],
            )
        ]
        out = enrich(extracted, results)
        assert "rubric_scores" not in out["c1"]["invocations"][0]["eval_metric_results"][0]

    def test_a_rubric_with_no_rationale_is_skipped_not_emitted_empty(self, enrich):
        """An entry saying `rationale: None` is noise in a reflection prompt."""
        extracted = {
            "c1": {"invocations": [{"eval_metric_results": [{"metric_name": "m", "score": 0.5}]}]}
        }
        results = [
            pytypes.SimpleNamespace(
                eval_id="c1",
                eval_metric_result_per_invocation=[
                    pytypes.SimpleNamespace(
                        eval_metric_results=[_metric_result("m", 0.5, [_Rubric("R", 1.0, None)])]
                    )
                ],
            )
        ]
        out = enrich(extracted, results)
        assert "rubric_scores" not in out["c1"]["invocations"][0]["eval_metric_results"][0]


class TestItCannotBreakAnOptimizeStage:
    """A nine-hour stage may not die because an enrichment hit an unexpected shape."""

    def test_a_missing_case_is_ignored(self, enrich):
        results = [pytypes.SimpleNamespace(eval_id="absent", eval_metric_result_per_invocation=[])]
        assert enrich({"c1": {"invocations": []}}, results) == {"c1": {"invocations": []}}

    def test_a_mismatched_shape_returns_the_original(self, enrich):
        weird = {"c1": "not-a-dict"}
        assert enrich(weird, [pytypes.SimpleNamespace(eval_id="c1")]) == weird

    def test_none_extracted_is_returned_unchanged(self, enrich):
        assert enrich(None, []) is None
