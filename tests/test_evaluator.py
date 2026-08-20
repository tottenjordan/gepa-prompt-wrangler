"""Tests for wrangler.evaluator — pure helpers and data building."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from wrangler.core.config import get_batch_config
from wrangler.eval.evaluator import (
    EvalResult,
    _build_eval_dataset,
    _is_failed_response,
    _resolve_resource_name,
    _retry_failed_cases,
    run_batch_eval_averaged,
    save_eval_results,
)


class TestBuildEvalDataset:
    def test_basic_cases_to_dataframe(self):
        cases = [
            {"prompt": "q1"},
            {"prompt": "q2"},
            {"prompt": "q3"},
        ]
        df = _build_eval_dataset(cases)
        assert len(df) == 3
        assert "prompt" in df.columns

    def test_reference_from_expected_response(self):
        cases = [{"prompt": "q1", "expected_response": "answer1"}]
        df = _build_eval_dataset(cases)
        assert df.iloc[0]["reference"] == "answer1"

    def test_reference_from_reference_key(self):
        cases = [{"prompt": "q1", "reference": "ref1"}]
        df = _build_eval_dataset(cases)
        assert df.iloc[0]["reference"] == "ref1"

    def test_empty_cases_returns_empty_df(self):
        df = _build_eval_dataset([])
        assert len(df) == 0


class TestResolveResourceName:
    @patch("wrangler.eval.evaluator.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.eval.evaluator.GCP_REGION", "us-central1")
    def test_short_id_expanded(self):
        result = _resolve_resource_name("12345")
        assert result == "projects/test-project/locations/us-central1/reasoningEngines/12345"

    def test_full_resource_passthrough(self):
        full = "projects/my-proj/locations/us/reasoningEngines/123"
        assert _resolve_resource_name(full) == full


class TestEvalResult:
    def test_default_empty(self):
        result = EvalResult()
        assert result.scores == {}
        assert result.per_case == []
        assert result.scores_std == {}
        assert result.num_runs == 1

    def test_with_scores(self):
        scores = {"quality": 0.85, "safety": 1.0}
        per_case = [{"quality": 0.9}, {"quality": 0.8}]
        result = EvalResult(scores=scores, per_case=per_case)
        assert result.scores == scores
        assert len(result.per_case) == 2

    def test_scores_dict_access(self):
        result = EvalResult(scores={"a": 1.0, "b": 0.5})
        assert list(result.scores.items()) == [("a", 1.0), ("b", 0.5)]

    def test_with_std_dev(self):
        result = EvalResult(
            scores={"quality": 0.85},
            scores_std={"quality": 0.03},
            num_runs=3,
        )
        assert result.scores_std["quality"] == 0.03
        assert result.num_runs == 3


class TestRunBatchEvalAveraged:
    def test_single_run_delegates(self):
        mock_result = EvalResult(scores={"q": 0.9}, per_case=[{"q": 0.9}])
        with patch("wrangler.eval.evaluator.run_batch_eval", return_value=mock_result) as mock:
            result = run_batch_eval_averaged("engine", [{"prompt": "hi"}], num_runs=1)
            mock.assert_called_once()
            assert result.scores == {"q": 0.9}
            assert result.num_runs == 1

    def test_multi_run_averages(self):
        results = [
            EvalResult(scores={"q": 0.8, "s": 1.0}, per_case=[{"q": 0.8}]),
            EvalResult(scores={"q": 0.9, "s": 0.9}, per_case=[{"q": 0.9}]),
            EvalResult(scores={"q": 1.0, "s": 0.8}, per_case=[{"q": 1.0}]),
        ]
        with patch("wrangler.eval.evaluator.run_batch_eval", side_effect=results):
            result = run_batch_eval_averaged("engine", [{"prompt": "hi"}], num_runs=3)
            assert result.num_runs == 3
            assert abs(result.scores["q"] - 0.9) < 0.001
            assert abs(result.scores["s"] - 0.9) < 0.001
            assert result.scores_std["q"] > 0
            assert len(result.per_case) == 1
            assert abs(result.per_case[0]["q"] - 0.9) < 0.001


class TestSaveEvalResults:
    def test_saves_json_to_output_dir(self, tmp_path):
        scores = {"quality": 0.85, "safety": 1.0}
        path = save_eval_results("lite", scores, "baseline", str(tmp_path))
        assert Path(path).exists()
        with open(path) as f:
            data = json.load(f)
        assert data["agent"] == "lite"
        assert data["phase"] == "baseline"
        assert data["scores"] == scores
        assert "timestamp" in data


class TestGetBatchConfig:
    def test_gemini_3x_flash_lite(self):
        batch_size, delay, workers = get_batch_config("gemini-3.1-flash-lite")
        assert batch_size == 4
        assert delay == 15.0
        assert workers == 4

    def test_gemini_3x_flash(self):
        batch_size, delay, workers = get_batch_config("gemini-3.5-flash")
        assert batch_size == 4
        assert delay == 15.0
        assert workers == 4

    def test_gemini_3x_pro(self):
        batch_size, delay, workers = get_batch_config("gemini-3.1-pro-preview")
        assert batch_size == 4
        assert delay == 15.0
        assert workers == 4

    def test_gemini_2x_flash(self):
        batch_size, delay, workers = get_batch_config("gemini-2.5-flash")
        assert batch_size == 16
        assert delay == 5.0
        assert workers == 10

    def test_claude_sonnet(self):
        batch_size, delay, workers = get_batch_config("claude-sonnet-4-6")
        assert batch_size == 64
        assert delay == 0.0
        assert workers == 20

    def test_claude_opus(self):
        batch_size, delay, workers = get_batch_config("claude-opus-4-6")
        assert batch_size == 64
        assert delay == 0.0
        assert workers == 20

    def test_unknown_model_uses_default(self):
        batch_size, delay, workers = get_batch_config("some-unknown-model")
        assert batch_size == 16
        assert delay == 5.0
        assert workers == 10


class TestRetryFailedCases:
    def _make_inference_result(self, responses):
        df = pd.DataFrame(
            {"prompt": [f"q{i}" for i in range(len(responses))], "response": responses}
        )
        result = MagicMock()
        result.eval_dataset_df = df
        return result

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_detects_null_responses(self, mock_sleep, mock_batched):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1", "q2"]})
        inference_result = self._make_inference_result(["good", None, "good"])

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ["recovered"]})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        result = _retry_failed_cases(
            MagicMock(),
            "agent",
            eval_df,
            inference_result,
            "gemini-3.1-flash-lite",
        )
        mock_sleep.assert_called_once_with(30)
        mock_batched.assert_called_once()
        assert result.eval_dataset_df.iloc[1]["response"] == "recovered"

    def test_skips_when_all_succeed(self):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(["good", "also good"])

        result = _retry_failed_cases(
            MagicMock(),
            "agent",
            eval_df,
            inference_result,
            "gemini-3.5-flash",
        )
        assert result is inference_result

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_detects_error_dict_responses(self, mock_sleep, mock_batched):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(["good", {"error": "Resource exhausted"}])

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ["recovered"]})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        _result = _retry_failed_cases(
            MagicMock(),
            "agent",
            eval_df,
            inference_result,
            "gemini-3.1-pro",
        )
        assert mock_batched.called

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_detects_error_payload_stored_as_a_string(self, mock_sleep, mock_batched):
        """GEAP's empty event stream arrives as an error JSON *string*, not a dict."""
        empty_stream = (
            '{"error": "Failed to parse agent run response [] to agent data: '
            'list index out of range"}'
        )
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(["good", empty_stream])

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ["recovered"]})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        result = _retry_failed_cases(
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.5-flash"
        )
        assert mock_batched.called
        assert result.eval_dataset_df.iloc[1]["response"] == "recovered"

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_recovers_rows_carrying_non_broadcastable_objects(self, mock_sleep, mock_batched):
        """Splicing a recovered row must not go through pandas' row setitem.

        The real frames mix ADK objects with typed columns, and assigning a
        whole Series into `df.iloc[i]` makes pandas try to coerce each value to
        the destination dtype. Live that surfaced as
        `TypeError: object of type 'SessionInput' has no len()`; the `ts`
        column below reproduces the same class of TypeError, and both are
        avoided entirely by merging records.
        """

        class SessionInput:
            """Mimics the ADK type: no __len__, no __iter__."""

        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        df = pd.DataFrame(
            {
                "prompt": ["q0", "q1"],
                "response": ["good", '{"error": "empty"}'],
                "request": [SessionInput(), SessionInput()],
                "ts": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            }
        )
        inference_result = MagicMock(eval_dataset_df=df)

        retry_df = pd.DataFrame(
            {
                "prompt": ["q1"],
                "response": ["recovered"],
                "request": [SessionInput()],
                "ts": [SessionInput()],
            }
        )
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        # Guard the premise: the old whole-row assignment really does blow up.
        scratch = df.copy()
        with pytest.raises(TypeError):
            scratch.iloc[1] = retry_df.iloc[0]

        result = _retry_failed_cases(
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.5-flash"
        )
        out = result.eval_dataset_df
        assert out.iloc[1]["response"] == "recovered"
        assert out.iloc[0]["response"] == "good"
        assert list(out.columns) == ["prompt", "response", "request", "ts"]

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_columns_missing_from_the_retry_frame_keep_their_value(self, mock_sleep, mock_batched):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        df = pd.DataFrame(
            {
                "prompt": ["q0", "q1"],
                "response": ["good", '{"error": "empty"}'],
                "reference": ["ref0", "ref1"],
            }
        )
        inference_result = MagicMock(eval_dataset_df=df)

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ["recovered"]})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        result = _retry_failed_cases(
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.5-flash"
        )
        assert result.eval_dataset_df.iloc[1]["reference"] == "ref1"

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_a_retry_that_errors_again_is_not_counted_as_recovered(
        self, mock_sleep, mock_batched, capsys
    ):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(["good", '{"error": "empty"}'])

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ['{"error": "empty again"}']})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        _retry_failed_cases(MagicMock(), "agent", eval_df, inference_result, "gemini-3.5-flash")
        assert "Recovered 0/1" in capsys.readouterr().out


class TestIsFailedResponse:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            float("nan"),
            {"error": "boom"},
            '{"error": "Failed to parse agent run response [] to agent data"}',
        ],
    )
    def test_failures(self, value):
        assert _is_failed_response(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "a normal answer",
            "I found flight FL001 for you.",
            {"text": "fine"},
            # Prose that merely mentions an error is a real answer.
            "The API returned an error, so I could not book that flight.",
            # A JSON object response with no error key is fine.
            '{"flights": ["FL001"]}',
            # Malformed JSON is not an error payload — leave it to the SDK.
            '{"error": ',
        ],
    )
    def test_successes(self, value):
        assert _is_failed_response(value) is False


class TestDefaultMetricVersions:
    """Guard the metric versions batch eval submits to the service.

    An unversioned RubricMetric resolves through the SDK's client-side
    METRIC_LATEST_SPEC_NAME table, which drifts ahead of what the eval service
    serves. When it does, the service errors per-metric and the SDK's
    extra='forbid' result model then fails to parse the WHOLE result file --
    silently zeroing every per-case score. These tests fail loudly instead.
    """

    def test_all_default_metrics_are_v1(self):
        """v1 is what the us-central1 service serves (probed 2026-08-20).

        It is also what the report layer keys on: thresholds_from_sampler_config
        emits final_response_quality_v1 / hallucination_v1 / safety_v1 /
        tool_use_quality_v1, so a v2 metric name silently misses every lookup.
        """
        from wrangler.eval.evaluator import _TOOL_USE_METRIC_NAME, DEFAULT_METRICS

        for metric in DEFAULT_METRICS:
            name = metric.name.lower()
            if name == _TOOL_USE_METRIC_NAME:
                continue  # custom LLMMetric, not a predefined versioned one
            assert metric.version == "v1", (
                f"{name} must pin version='v1'; an unpinned metric follows the "
                "SDK's latest table and gets rejected by the service"
            )

    def test_no_unversioned_predefined_metrics(self):
        from wrangler.eval.evaluator import _TOOL_USE_METRIC_NAME, DEFAULT_METRICS

        unpinned = [
            m.name
            for m in DEFAULT_METRICS
            if m.name.lower() != _TOOL_USE_METRIC_NAME and getattr(m, "version", None) is None
        ]
        assert not unpinned, f"unpinned predefined metrics: {unpinned}"


class TestMcpEnvPropagation:
    """The local deploy path used to ship agents with no MCP env vars at all.

    A toolless agent does not fail loudly -- it comes up with an empty toolset
    and role-plays tool use, emitting literal <tool_call> text. Only the
    pipeline path passed env_vars, so `wrangler run` deployed broken agents.
    """

    def test_mcp_vars_collected_from_environ(self, monkeypatch):
        from wrangler.core.deploy import mcp_env_from_environ

        monkeypatch.setenv("SEARCH_MCP_SERVER", "wrangler-search-mcp")
        monkeypatch.setenv("SEARCH_MCP_URL", "https://search.example/mcp")
        monkeypatch.setenv("UNRELATED_VAR", "nope")

        env = mcp_env_from_environ()
        assert env["SEARCH_MCP_SERVER"] == "wrangler-search-mcp"
        assert env["SEARCH_MCP_URL"] == "https://search.example/mcp"
        assert "UNRELATED_VAR" not in env

    def test_empty_values_are_dropped(self, monkeypatch):
        """An empty string is worse than absent: it looks configured."""
        from wrangler.core.deploy import mcp_env_from_environ

        monkeypatch.setenv("BOOKING_MCP_URL", "")
        assert "BOOKING_MCP_URL" not in mcp_env_from_environ()

    def test_source_config_includes_mcp_env(self, monkeypatch, tmp_path):
        from wrangler.core.deploy import _build_source_config

        monkeypatch.setenv("EXPENSE_MCP_SERVER", "wrangler-expense-mcp")
        monkeypatch.setenv("EXPENSE_MCP_URL", "https://expense.example/mcp")
        monkeypatch.chdir(tmp_path)
        build_dir = tmp_path / "_geap_build_pkg"
        build_dir.mkdir()

        config = _build_source_config(str(build_dir), "test-agent")
        assert config["env_vars"]["EXPENSE_MCP_SERVER"] == "wrangler-expense-mcp"
        assert config["env_vars"]["EXPENSE_MCP_URL"] == "https://expense.example/mcp"

    def test_explicit_env_vars_win_over_environ(self, monkeypatch, tmp_path):
        """The pipeline overrides MCP URLs to localhost -- that must stick."""
        from wrangler.core.deploy import _build_source_config

        monkeypatch.setenv("SEARCH_MCP_URL", "https://cloudrun.example/mcp")
        monkeypatch.chdir(tmp_path)
        build_dir = tmp_path / "_geap_build_pkg"
        build_dir.mkdir()

        config = _build_source_config(
            str(build_dir), "test-agent", env_vars={"SEARCH_MCP_URL": "http://localhost:8001/mcp"}
        )
        assert config["env_vars"]["SEARCH_MCP_URL"] == "http://localhost:8001/mcp"
