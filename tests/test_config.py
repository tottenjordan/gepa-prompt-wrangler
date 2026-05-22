"""Tests for wrangler.config — model resolution, constants, and utilities."""

import pytest
from unittest.mock import patch, MagicMock

from wrangler.config import resolve_model, MODEL_COSTS


class TestResolveModel:
    def test_gemini_2x_returns_string(self):
        result = resolve_model("gemini-2.0-flash")
        assert result == "gemini-2.0-flash"
        assert isinstance(result, str)

    def test_models_prefix_returns_string(self):
        result = resolve_model("models/gemini-pro")
        assert result == "models/gemini-pro"
        assert isinstance(result, str)

    @patch("wrangler.config.LiteLlm")
    def test_gemini_3x_returns_litellm(self, mock_litellm):
        mock_instance = MagicMock()
        mock_litellm.return_value = mock_instance
        result = resolve_model("gemini-3.5-flash")
        mock_litellm.assert_called_once_with(
            model="vertex_ai/gemini-3.5-flash", vertex_location="global"
        )
        assert result == mock_instance

    @patch("wrangler.config.LiteLlm")
    def test_claude_returns_litellm(self, mock_litellm):
        mock_instance = MagicMock()
        mock_litellm.return_value = mock_instance
        result = resolve_model("claude-sonnet-4-6")
        mock_litellm.assert_called_once_with(
            model="vertex_ai/claude-sonnet-4-6", vertex_location="global"
        )
        assert result == mock_instance

    @patch("wrangler.config.LiteLlm")
    def test_already_prefixed_no_double_prefix(self, mock_litellm):
        mock_instance = MagicMock()
        mock_litellm.return_value = mock_instance
        resolve_model("vertex_ai/gemini-3.5-flash")
        args = mock_litellm.call_args
        model_arg = args[1].get("model") or args[0][0]
        assert not model_arg.startswith("vertex_ai/vertex_ai/")


class TestDisablePyopenssl:
    def test_no_openssl_is_noop(self):
        with patch.dict("sys.modules", {"OpenSSL": None, "OpenSSL.SSL": None}):
            from wrangler.config import disable_pyopenssl
            disable_pyopenssl()

    def test_function_exists_and_callable(self):
        from wrangler.config import disable_pyopenssl
        assert callable(disable_pyopenssl)


class TestConstants:
    def test_model_costs_has_all_models(self):
        expected_models = [
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
        ]
        for model in expected_models:
            assert model in MODEL_COSTS, f"{model} missing from MODEL_COSTS"
            assert "input" in MODEL_COSTS[model]
            assert "output" in MODEL_COSTS[model]
            assert isinstance(MODEL_COSTS[model]["input"], (int, float))
            assert isinstance(MODEL_COSTS[model]["output"], (int, float))
