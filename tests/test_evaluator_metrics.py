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


class _StubItem:
    """An EvaluationItem whose response came back empty because the SDK's
    loader could not parse the GCS file — the observable shape of defect #7."""

    def __init__(self, gcs_uri, response=None):
        self.gcs_uri = gcs_uri
        self.evaluation_response = response


class _StubEvals:
    def __init__(self, items):
        self._items = items
        self.evaluation_items = list(items)

    def get_evaluation_set(self, name):
        return self

    def get_evaluation_item(self, name):
        return self._items[name]


class _StubClient:
    def __init__(self, items):
        self.evals = _StubEvals(items)


class _StubRunWithSet:
    class evaluation_run_results:  # noqa: N801
        evaluation_set = "projects/p/locations/l/evaluationSets/s"


class TestPerCaseFallsBackToRawGcs:
    """When the SDK hands back an empty item, go to the file it came from.

    `EvaluationItem.gcs_uri` survives even when `evaluation_response` is the
    empty object the failed parse produced, so the data is still reachable.
    """

    def test_recovers_scores_from_gcs_when_response_is_empty(self, monkeypatch):
        raw = {
            "candidateResults": [
                {"metric": "safety_v1", "score": 1.0},
                {"metric": "instruction_following_v1", "score": 0.0},
                {"metric": "hallucination_v1", "error": {"code": 3, "message": "tool call"}},
            ]
        }
        items = {"item-1": _StubItem("gs://bucket/result_1.json", types.EvaluationItemResult())}
        monkeypatch.setattr(evaluator, "Client", lambda **kw: _StubClient(items))
        monkeypatch.setattr(evaluator, "_read_raw_result", lambda client, uri: raw)

        per_case = evaluator._extract_per_case_via_api(_StubRunWithSet())

        assert [evaluator.case_metrics(r) for r in per_case] == [
            {"safety_v1": 1.0, "instruction_following_v1": 0.0}
        ]
        assert per_case[0][evaluator.CASE_INDEX_KEY] == 0, "rows must identify their case"

    def test_does_not_touch_gcs_when_the_sdk_parsed_fine(self, monkeypatch):
        """No extra read on the happy path — it is a per-item network call."""

        class _Candidate:
            metric, score = "safety_v1", 1.0

        class _Response:
            def __init__(self):
                self.candidate_results = [_Candidate()]

        items = {"item-1": _StubItem("gs://bucket/result_1.json", _Response())}
        monkeypatch.setattr(evaluator, "Client", lambda **kw: _StubClient(items))

        def _boom(client, uri):
            raise AssertionError("read GCS despite a parsed response")

        monkeypatch.setattr(evaluator, "_read_raw_result", _boom)

        got = evaluator._extract_per_case_via_api(_StubRunWithSet())
        assert [evaluator.case_metrics(r) for r in got] == [{"safety_v1": 1.0}]

    def test_one_unreadable_file_does_not_sink_the_other_cases(self, monkeypatch):
        raw = {"candidateResults": [{"metric": "safety_v1", "score": 1.0}]}
        items = {
            "item-1": _StubItem("gs://bucket/a.json", types.EvaluationItemResult()),
            "item-2": _StubItem("gs://bucket/b.json", types.EvaluationItemResult()),
        }
        monkeypatch.setattr(evaluator, "Client", lambda **kw: _StubClient(items))

        def _read(client, uri):
            if uri.endswith("a.json"):
                raise OSError("403 on the bucket")
            return raw

        monkeypatch.setattr(evaluator, "_read_raw_result", _read)

        per_case = evaluator._extract_per_case_via_api(_StubRunWithSet())
        assert [evaluator.case_metrics(r) for r in per_case] == [{}, {"safety_v1": 1.0}]
        # Even the unreadable case keeps its identity, so it can be seen as
        # missing from a pairing rather than vanishing silently.
        assert [r[evaluator.CASE_INDEX_KEY] for r in per_case] == [0, 1]

    def test_item_with_no_gcs_uri_is_simply_empty(self, monkeypatch):
        items = {"item-1": _StubItem(None, types.EvaluationItemResult())}
        monkeypatch.setattr(evaluator, "Client", lambda **kw: _StubClient(items))
        got = evaluator._extract_per_case_via_api(_StubRunWithSet())
        assert [evaluator.case_metrics(r) for r in got] == [{}]


class TestMetricCoverage:
    """A mean over four cases must not be presentable as a mean over five.

    The aggregate scores in EvalResult.scores come from the *server-side*
    summary metrics, which quietly exclude cases whose metric errored. The
    number looks identical either way, so uneven coverage has to be stated
    explicitly or a before/after comparison silently measures the dropout
    rather than the prompt. See docs/notes/silent-failures.md #7.
    """

    def test_counts_cases_per_metric(self):
        per_case = [
            {"safety_v1": 1.0, "hallucination_v1": 0.9},
            {"safety_v1": 1.0},
            {"safety_v1": 0.5, "hallucination_v1": 0.8},
        ]
        assert evaluator._metric_coverage(per_case) == {
            "safety_v1": 3,
            "hallucination_v1": 2,
        }

    def test_empty_per_case_is_empty_coverage(self):
        assert evaluator._metric_coverage([]) == {}

    def test_warns_and_names_the_short_metrics(self):
        lines = evaluator._coverage_warning({"safety_v1": 5, "hallucination_v1": 2}, 5)
        text = "\n".join(lines)
        assert "UNEVEN METRIC COVERAGE" in text
        assert "hallucination_v1" in text
        assert "2/5" in text
        # The healthy metric must not be listed as a problem.
        assert "safety_v1" not in text.split("hallucination_v1")[0].split("\n")[-1]

    def test_silent_when_every_metric_covers_every_case(self):
        assert evaluator._coverage_warning({"safety_v1": 5, "hallucination_v1": 5}, 5) == []

    def test_silent_when_there_are_no_cases(self):
        assert evaluator._coverage_warning({}, 0) == []

    def test_a_metric_absent_everywhere_still_counts_as_uneven(self):
        """Zero coverage is the worst case, not an exempt one."""
        lines = evaluator._coverage_warning({"safety_v1": 5, "hallucination_v1": 0}, 5)
        assert any("hallucination_v1" in x and "0/5" in x for x in lines)

    def test_eval_result_carries_coverage(self):
        r = evaluator.EvalResult(coverage={"safety_v1": 4})
        assert r.coverage == {"safety_v1": 4}

    def test_eval_result_coverage_defaults_empty(self):
        """Existing constructions must keep working untouched."""
        assert evaluator.EvalResult().coverage == {}


class TestInferenceRetryBudget:
    """Inference must spend attempts, because one is nowhere near enough.

    GEAP drops ~75% of requests on booting workers (silent-failures.md #5,
    measured unfixable from the client). A single retry recovers ~25% of
    failures in expectation, which is why the PR #11 verification run logged
    `Recovered 0/4` then `Recovered 0/5` and then died on a 500. Same lesson
    PR #10 applied to the traffic generator: you cannot dodge this, so spend
    attempts on it.
    """

    @staticmethod
    def _frame(responses):
        import pandas as pd

        return pd.DataFrame(
            {"prompt": [f"q{i}" for i in range(len(responses))], "response": responses}
        )

    @staticmethod
    def _dataset(responses):
        from vertexai import types

        return types.EvaluationDataset(eval_dataset_df=TestInferenceRetryBudget._frame(responses))

    def _run(self, monkeypatch, initial, script):
        """`script` yields the response list each successive retry pass returns."""
        from vertexai import types

        from wrangler.eval import evaluator as ev

        monkeypatch.setattr(ev.time, "sleep", lambda _s: None)
        passes = []

        def _fake_inference(client, agent_resource, df, **kwargs):
            passes.append(len(df))
            responses = script.pop(0) if script else ["" for _ in range(len(df))]
            return types.EvaluationDataset(eval_dataset_df=self._frame(responses))

        monkeypatch.setattr(ev, "_run_batched_inference", _fake_inference)
        result = ev._retry_failed_cases(
            client=None,
            agent_resource="engines/1",
            eval_df=self._frame(initial),
            inference_result=self._dataset(initial),
            model="m",
        )
        return result.eval_dataset_df, passes

    def test_a_case_that_fails_twice_then_succeeds_is_recovered(self, monkeypatch):
        """Today this is lost: the single retry pass fails and it gives up."""
        df, passes = self._run(
            monkeypatch,
            initial=["", "ok-0"],
            script=[[""], [""], ["recovered"]],
        )
        assert df["response"].tolist() == ["recovered", "ok-0"]
        assert len(passes) == 3, f"expected 3 retry passes, made {len(passes)}"

    def test_stops_at_the_budget(self, monkeypatch):
        from wrangler.eval.evaluator import _INFERENCE_MAX_ATTEMPTS

        _df, passes = self._run(monkeypatch, initial=[""], script=[])
        # The initial inference is attempt 1; the retries make up the rest.
        assert len(passes) == _INFERENCE_MAX_ATTEMPTS - 1

    def test_a_case_that_never_succeeds_is_left_failed(self, monkeypatch):
        df, _ = self._run(monkeypatch, initial=["", "ok-0"], script=[])
        assert df["response"].tolist()[1] == "ok-0"
        from wrangler.eval.evaluator import _is_failed_response

        assert _is_failed_response(df["response"].tolist()[0])

    def test_only_still_failed_rows_are_retried(self, monkeypatch):
        """Each pass must shrink, or recovered cases get re-run and re-lost."""
        _df, passes = self._run(
            monkeypatch,
            initial=["", "", ""],
            script=[["fixed", "", ""], ["fixed2", ""], ["fixed3"]],
        )
        assert passes[:3] == [3, 2, 1], f"pass sizes {passes} should shrink as cases recover"

    def test_no_failures_short_circuits(self, monkeypatch):
        df, passes = self._run(monkeypatch, initial=["ok-0", "ok-1"], script=[])
        assert passes == [], "should not call inference when nothing failed"
        assert df["response"].tolist() == ["ok-0", "ok-1"]

    def test_reports_attempts_and_recovery(self, monkeypatch, capsys):
        self._run(monkeypatch, initial=["", "ok"], script=[[""], ["recovered"]])
        out = capsys.readouterr().out
        assert "Recovered 1/1" in out
        assert "attempt" in out.lower()


class TestEmptyEvalIsRefused:
    """Submitting an all-failed inference set returns a 500 that explains nothing.

    On 2026-08-21 a verification run lost every case to the cold-worker defect,
    submitted the empty set anyway, and `create_evaluation_run` answered
    `500 INTERNAL`. That error names no cause and cost the run. Say it here
    instead, where the cause is known.
    """

    @staticmethod
    def _df(responses):
        import pandas as pd

        return pd.DataFrame(
            {
                "prompt": [f"q{i}" for i in range(len(responses))],
                "response": responses,
                "agent_data": ["{}" for _ in responses],
            }
        )

    def test_all_rows_failed_raises_before_submitting(self):
        from wrangler.eval.evaluator import _assert_scorable

        with pytest.raises(RuntimeError, match="0 of 3"):
            _assert_scorable(self._df(["", "", ""]), tag="[sonnet] ")

    def test_the_message_names_the_cause(self):
        from wrangler.eval.evaluator import _assert_scorable

        with pytest.raises(RuntimeError) as exc:
            _assert_scorable(self._df([""]), tag="")
        text = str(exc.value)
        assert "silent-failures" in text, "must point at the known cause"
        assert "500" in text, "must explain what it is preventing"

    def test_a_partially_failed_set_still_submits(self):
        from wrangler.eval.evaluator import _assert_scorable

        _assert_scorable(self._df(["", "a real answer", ""]), tag="")

    def test_a_fully_healthy_set_submits(self):
        from wrangler.eval.evaluator import _assert_scorable

        _assert_scorable(self._df(["one", "two"]), tag="")


class TestCaseIdentity:
    """per_case rows must say *which* case they are, or before/after cannot pair.

    The 2026-08-22 sweep scored 30 cases before and 57 after on one arm, 60/36
    on another. Every delta therefore compared two different subsets of the 64,
    and there was no way to line them up — the rows were bare {metric: score}
    dicts with nothing identifying the case. That unpairing is the single
    largest source of the measured +0.039 noise floor.
    """

    def test_reserved_key_is_not_treated_as_a_metric(self):
        from wrangler.eval.evaluator import CASE_INDEX_KEY, case_metrics

        row = {CASE_INDEX_KEY: 7, "safety_v1": 1.0, "hallucination_v1": 0.5}
        assert case_metrics(row) == {"safety_v1": 1.0, "hallucination_v1": 0.5}

    def test_coverage_ignores_the_case_index(self):
        """Otherwise every row reports an extra 'metric' with perfect coverage."""
        from wrangler.eval.evaluator import CASE_INDEX_KEY, _metric_coverage

        pc = [{CASE_INDEX_KEY: 0, "safety_v1": 1.0}, {CASE_INDEX_KEY: 3, "safety_v1": 0.5}]
        assert _metric_coverage(pc) == {"safety_v1": 2}

    def test_pairing_matches_on_case_index(self):
        from wrangler.eval.evaluator import CASE_INDEX_KEY, pair_per_case

        before = [{CASE_INDEX_KEY: 0, "m": 0.4}, {CASE_INDEX_KEY: 2, "m": 0.6}]
        after = [{CASE_INDEX_KEY: 2, "m": 0.9}, {CASE_INDEX_KEY: 5, "m": 0.1}]
        paired = pair_per_case(before, after)
        assert list(paired) == [2], "only case 2 appears on both sides"
        assert paired[2] == ({"m": 0.6}, {"m": 0.9})

    def test_pairing_is_empty_without_indices(self):
        """Rows from before this change carry no index; pairing must not guess.

        Falling back to positional matching would silently pair case 0 of one
        subset with case 0 of a different subset — the exact error this exists
        to prevent.
        """
        from wrangler.eval.evaluator import pair_per_case

        assert pair_per_case([{"m": 0.4}], [{"m": 0.9}]) == {}

    def test_paired_delta_uses_only_common_cases(self):
        from wrangler.eval.evaluator import CASE_INDEX_KEY, paired_deltas

        before = [{CASE_INDEX_KEY: 0, "m": 0.4}, {CASE_INDEX_KEY: 1, "m": 0.2}]
        after = [{CASE_INDEX_KEY: 1, "m": 0.6}, {CASE_INDEX_KEY: 9, "m": 1.0}]
        d = paired_deltas(before, after)
        assert d["n_paired"] == 1
        assert d["deltas"]["m"] == pytest.approx(0.4)
        assert d["dropped_before"] == 1
        assert d["dropped_after"] == 1


class TestStandaloneResultsArePairable:
    """A control arm runs through standalone eval twice; it must keep per-case rows.

    Standalone mode used to persist only aggregate means. Two such runs can then
    be compared only by subtracting numbers computed over different case
    subsets — which on 2026-08-22 produced an apparent +0.180 spread from an
    unchanged prompt, with no way to separate sampling from signal.
    """

    def test_per_case_is_written(self, tmp_path):
        import json
        import pathlib

        from wrangler.eval.evaluator import CASE_INDEX_KEY, save_eval_results

        rows = [{CASE_INDEX_KEY: 0, "safety_v1": 1.0}, {CASE_INDEX_KEY: 4, "safety_v1": 0.5}]
        path = save_eval_results(
            agent_name="control",
            scores={"safety_v1": 0.75},
            output_dir=str(tmp_path),
            per_case=rows,
        )
        saved = json.loads(pathlib.Path(path).read_text())
        assert saved["per_case"] == rows

    def test_absent_per_case_is_an_empty_list_not_missing(self, tmp_path):
        """Consumers should not have to distinguish 'no rows' from 'old file'."""
        import json
        import pathlib

        from wrangler.eval.evaluator import save_eval_results

        path = save_eval_results(
            agent_name="a", scores={"safety_v1": 1.0}, output_dir=str(tmp_path)
        )
        assert json.loads(pathlib.Path(path).read_text())["per_case"] == []

    def test_two_saved_runs_can_be_paired(self, tmp_path):
        """The whole point: a measured noise floor over the same cases."""
        import json
        import pathlib

        from wrangler.eval.evaluator import CASE_INDEX_KEY, paired_deltas, save_eval_results

        p1 = save_eval_results(
            agent_name="c",
            scores={},
            phase="run1",
            output_dir=str(tmp_path),
            per_case=[{CASE_INDEX_KEY: 0, "m": 0.5}, {CASE_INDEX_KEY: 1, "m": 0.9}],
        )
        p2 = save_eval_results(
            agent_name="c",
            scores={},
            phase="run2",
            output_dir=str(tmp_path),
            per_case=[{CASE_INDEX_KEY: 1, "m": 1.0}, {CASE_INDEX_KEY: 7, "m": 0.2}],
        )
        a = json.loads(pathlib.Path(p1).read_text())["per_case"]
        b = json.loads(pathlib.Path(p2).read_text())["per_case"]
        d = paired_deltas(a, b)
        assert d["n_paired"] == 1
        assert d["deltas"]["m"] == pytest.approx(0.1)


class TestScoringStageLoss:
    """Cases that inferred fine can still vanish during scoring, silently.

    Measured 2026-08-23 on two control runs: 50 rows submitted -> 41 scored,
    and 44 -> 39. The primary extraction path reads whatever
    `evaluation_item_results.eval_case_results` contains and never compares it
    against the number submitted, so a case whose result file carried any
    per-metric error — the extra='forbid' cascade — is simply absent.

    PR #11 added GCS recovery, but only on the API-fallback path, which runs
    solely when `evaluation_item_results is None`. When it is present but
    short, nothing notices.
    """

    class _Case:
        def __init__(self, idx, score):
            self.eval_case_index = idx
            self.response_candidate_results = [
                type("C", (), {"metric_results": {"safety_v1": score}})()
            ]

    class _Items:
        def __init__(self, cases):
            self.eval_case_results = cases

    class _Run:
        def __init__(self, cases):
            self.evaluation_item_results = TestScoringStageLoss._Items(cases)
            self.evaluation_run_results = None

    def test_shortfall_against_expected_is_reported(self, capsys):
        """Silence is what let 9 cases disappear unnoticed."""
        run = self._Run([self._Case(0, 1.0), self._Case(1, 0.5)])
        evaluator._extract_per_case_scores(run, expected=5)
        out = capsys.readouterr().out
        assert "2" in out, out
        assert "5" in out, out
        assert "scor" in out.lower()

    def test_no_shortfall_stays_quiet(self, capsys):
        run = self._Run([self._Case(0, 1.0), self._Case(1, 0.5)])
        evaluator._extract_per_case_scores(run, expected=2)
        assert "fewer" not in capsys.readouterr().out.lower()

    def test_expected_is_optional_so_existing_callers_are_unaffected(self):
        run = self._Run([self._Case(0, 1.0)])
        assert len(evaluator._extract_per_case_scores(run)[0]) == 1


class TestScoringProvenance:
    """Which extraction path produced the rows has to survive into the artifact.

    The 2026-08-23 control run scored 53/53 with ragged per-metric coverage —
    the recovery path's exact signature, since it enumerates the eval set by
    position and keeps whatever metrics parsed. But the only record that it
    fired was a stdout line, and the log was lost before it could be read. The
    artifact alone could not distinguish "recovery worked" from "the loss did
    not reproduce". Verification that depends on a log file is not
    verification.
    """

    def test_sdk_path_is_labelled(self):
        run = TestScoringStageLoss._Run(
            [TestScoringStageLoss._Case(0, 1.0), TestScoringStageLoss._Case(1, 0.5)]
        )
        rows, source = evaluator._extract_per_case_scores(run, expected=2)
        assert len(rows) == 2
        assert source == "sdk"

    def test_recovery_path_is_labelled(self, monkeypatch):
        from wrangler.eval.evaluator import CASE_INDEX_KEY

        run = TestScoringStageLoss._Run([TestScoringStageLoss._Case(0, 1.0)])
        monkeypatch.setattr(
            evaluator,
            "_extract_per_case_via_api",
            lambda _run: [
                {CASE_INDEX_KEY: 0, "safety_v1": 1.0},
                {CASE_INDEX_KEY: 1},
                {CASE_INDEX_KEY: 2, "safety_v1": 0.5},
            ],
        )
        rows, source = evaluator._extract_per_case_scores(run, expected=3)
        assert len(rows) == 3
        assert source == "gcs_recovery"

    def test_failed_recovery_keeps_the_sdk_label(self, monkeypatch):
        """Recovery that did not help must not be recorded as if it had."""
        run = TestScoringStageLoss._Run([TestScoringStageLoss._Case(0, 1.0)])
        monkeypatch.setattr(evaluator, "_extract_per_case_via_api", lambda _run: [])
        rows, source = evaluator._extract_per_case_scores(run, expected=3)
        assert len(rows) == 1
        assert source == "sdk"

    def test_saved_artifact_records_coverage_and_scoring(self, tmp_path):
        import json
        import pathlib

        from wrangler.eval.evaluator import CASE_INDEX_KEY, save_eval_results

        path = save_eval_results(
            agent_name="ctrl",
            scores={"safety_v1": 1.0},
            phase="standalone",
            output_dir=str(tmp_path),
            per_case=[{CASE_INDEX_KEY: 0, "safety_v1": 1.0}, {CASE_INDEX_KEY: 1}],
            coverage={"safety_v1": 1},
            scoring={"submitted": 2, "scored": 2, "source": "gcs_recovery"},
        )
        data = json.loads(pathlib.Path(path).read_text())
        assert data["coverage"] == {"safety_v1": 1}
        assert data["scoring"]["source"] == "gcs_recovery"
        assert data["scoring"]["submitted"] == 2

    def test_saved_artifact_omits_nothing_when_unknown(self, tmp_path):
        """Older callers pass neither; the keys stay present and empty."""
        import json
        import pathlib

        from wrangler.eval.evaluator import save_eval_results

        path = save_eval_results(
            agent_name="ctrl", scores={}, phase="standalone", output_dir=str(tmp_path)
        )
        data = json.loads(pathlib.Path(path).read_text())
        assert data["coverage"] == {}
        assert data["scoring"] == {}
