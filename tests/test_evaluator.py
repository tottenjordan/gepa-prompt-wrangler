"""Tests for wrangler.evaluator — pure helpers and data building."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from wrangler.evaluator import _build_eval_dataset, _resolve_resource_name, save_eval_results, EvalResult


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

    def test_with_scores(self):
        scores = {"quality": 0.85, "safety": 1.0}
        per_case = [{"quality": 0.9}, {"quality": 0.8}]
        result = EvalResult(scores=scores, per_case=per_case)
        assert result.scores == scores
        assert len(result.per_case) == 2

    def test_scores_dict_access(self):
        result = EvalResult(scores={"a": 1.0, "b": 0.5})
        assert list(result.scores.items()) == [("a", 1.0), ("b", 0.5)]


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
