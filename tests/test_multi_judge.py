"""Tests for wrangler.multi_judge — multi-judge ensemble scoring."""

from unittest.mock import patch, MagicMock

from wrangler.optimize.multi_judge import evaluate, _get_judge_models, DEFAULT_JUDGE_MODELS


class TestGetJudgeModels:
    def test_default_models(self):
        with patch.dict("os.environ", {}, clear=True):
            models = _get_judge_models()
        assert models == DEFAULT_JUDGE_MODELS

    def test_custom_models_from_env(self):
        with patch.dict("os.environ", {"WRANGLER_JUDGE_MODELS": "model-a,model-b,model-c"}):
            models = _get_judge_models()
        assert models == ["model-a", "model-b", "model-c"]

    def test_empty_env_returns_default(self):
        with patch.dict("os.environ", {"WRANGLER_JUDGE_MODELS": ""}):
            models = _get_judge_models()
        assert models == DEFAULT_JUDGE_MODELS


class TestEvaluate:
    @patch("wrangler.optimize.multi_judge._call_judge")
    @patch("wrangler.optimize.multi_judge._get_judge_models")
    def test_returns_mean_of_scores(self, mock_models, mock_call):
        mock_models.return_value = ["judge-a", "judge-b"]
        mock_call.side_effect = [0.8, 0.6]

        score = evaluate(query="test", response="answer", reference="ref")
        assert score == 0.7

    @patch("wrangler.optimize.multi_judge._call_judge")
    @patch("wrangler.optimize.multi_judge._get_judge_models")
    def test_handles_single_judge_failure(self, mock_models, mock_call):
        mock_models.return_value = ["judge-a", "judge-b"]
        mock_call.side_effect = [0.8, Exception("timeout")]

        score = evaluate(query="test", response="answer", reference="ref")
        assert score == 0.8

    @patch("wrangler.optimize.multi_judge._call_judge")
    @patch("wrangler.optimize.multi_judge._get_judge_models")
    def test_all_judges_fail_returns_default(self, mock_models, mock_call):
        mock_models.return_value = ["judge-a"]
        mock_call.side_effect = Exception("fail")

        score = evaluate(query="test", response="answer", reference="ref")
        assert score == 0.5
