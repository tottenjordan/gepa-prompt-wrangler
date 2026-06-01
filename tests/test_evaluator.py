"""Tests for wrangler.evaluator — pure helpers and data building."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from wrangler.evaluator import (
    _build_eval_dataset, _resolve_resource_name, save_eval_results,
    EvalResult, run_batch_eval_averaged,
)


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
    @patch("wrangler.evaluator.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.evaluator.GCP_REGION", "us-central1")
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
        with patch("wrangler.evaluator.run_batch_eval", return_value=mock_result) as mock:
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
        with patch("wrangler.evaluator.run_batch_eval", side_effect=results):
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
