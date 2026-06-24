"""Tests for wrangler.evaluator — pure helpers and data building."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

from wrangler.eval.evaluator import (
    _build_eval_dataset, _resolve_resource_name, save_eval_results,
    EvalResult, run_batch_eval_averaged, _retry_failed_cases,
)
from wrangler.core.config import get_batch_config


class TestBuildEvalDataset:
    def test_basic_cases_to_dataframe(self):
        cases = [
            {"prompt": "q1"}, {"prompt": "q2"}, {"prompt": "q3"},
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
        df = pd.DataFrame({"prompt": [f"q{i}" for i in range(len(responses))],
                           "response": responses})
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
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.1-flash-lite",
        )
        mock_sleep.assert_called_once_with(30)
        mock_batched.assert_called_once()
        assert result.eval_dataset_df.iloc[1]["response"] == "recovered"

    def test_skips_when_all_succeed(self):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(["good", "also good"])

        result = _retry_failed_cases(
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.5-flash",
        )
        assert result is inference_result

    @patch("wrangler.eval.evaluator._run_batched_inference")
    @patch("wrangler.eval.evaluator.time.sleep")
    def test_detects_error_dict_responses(self, mock_sleep, mock_batched):
        eval_df = pd.DataFrame({"prompt": ["q0", "q1"]})
        inference_result = self._make_inference_result(
            ["good", {"error": "Resource exhausted"}]
        )

        retry_df = pd.DataFrame({"prompt": ["q1"], "response": ["recovered"]})
        mock_batched.return_value = MagicMock(eval_dataset_df=retry_df)

        result = _retry_failed_cases(
            MagicMock(), "agent", eval_df, inference_result, "gemini-3.1-pro",
        )
        assert mock_batched.called
