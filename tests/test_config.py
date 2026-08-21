"""Tests for wrangler.config — model resolution, constants, and utilities."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wrangler.core.config import MODEL_COSTS, resolve_model
from wrangler.core.models import MODELS, model_location


@pytest.fixture
def vertex_env(monkeypatch):
    """A fully-configured Vertex environment, independent of the local .env."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")


class TestResolveModel:
    def test_gemini_2x_returns_string(self):
        result = resolve_model("gemini-2.0-flash")
        assert result == "gemini-2.0-flash"
        assert isinstance(result, str)

    def test_models_prefix_returns_string(self):
        result = resolve_model("models/gemini-pro")
        assert result == "models/gemini-pro"
        assert isinstance(result, str)

    @patch("google.adk.models.google_llm.Gemini")
    def test_gemini_3x_returns_gemini(self, mock_gemini, vertex_env):
        mock_instance = MagicMock()
        mock_gemini.return_value = mock_instance
        result = resolve_model("gemini-3.5-flash")
        mock_gemini.assert_called_once_with(
            model="gemini-3.5-flash",
            client_kwargs={"vertexai": True, "project": "test-proj", "location": "global"},
        )
        assert result == mock_instance

    @patch("google.adk.models.anthropic_llm.Claude")
    def test_claude_returns_claude(self, mock_claude, vertex_env):
        mock_instance = MagicMock()
        mock_claude.return_value = mock_instance
        result = resolve_model("claude-sonnet-4-6")
        mock_claude.assert_called_once_with(
            model="projects/test-proj/locations/global"
            "/publishers/anthropic/models/claude-sonnet-4-6"
        )
        assert result == mock_instance

    @patch("google.adk.models.google_llm.Gemini")
    def test_gemini_3x_pro_returns_gemini(self, mock_gemini, vertex_env):
        mock_instance = MagicMock()
        mock_gemini.return_value = mock_instance
        result = resolve_model("gemini-3.1-pro-preview")
        mock_gemini.assert_called_once_with(
            model="gemini-3.1-pro-preview",
            client_kwargs={"vertexai": True, "project": "test-proj", "location": "global"},
        )
        assert result == mock_instance

    @patch("google.adk.models.anthropic_llm.Claude")
    def test_claude_ignores_a_regional_google_cloud_location(
        self, mock_claude, vertex_env, monkeypatch
    ):
        """A stale GOOGLE_CLOUD_LOCATION must not reach the model.

        This is the exact failure that motivated pinning: with the env var set
        to us-central1, a bare Claude id produced "Publisher Model
        .../locations/us-central1/publishers/anthropic/models/claude-sonnet-4-6
        is not servable in region us-central1". ADK reads project and location
        out of the resource path when one is given, so the env var loses.
        """
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        resolve_model("claude-sonnet-4-6")
        assert "locations/global" in mock_claude.call_args.kwargs["model"]

    @patch("google.adk.models.google_llm.Gemini")
    def test_gemini_3x_ignores_a_regional_google_cloud_location(
        self, mock_gemini, vertex_env, monkeypatch
    ):
        """Same rule for Gemini 3.x — GEAP can force the env var regionally."""
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        resolve_model("gemini-3.5-flash")
        assert mock_gemini.call_args.kwargs["client_kwargs"]["location"] == "global"

    @patch("google.adk.models.google_llm.Gemini")
    def test_no_project_leaves_client_construction_to_adk(self, mock_gemini, monkeypatch):
        """Without a project there is nothing to pin, and API-key mode must still work."""
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        resolve_model("gemini-3.5-flash")
        mock_gemini.assert_called_once_with(model="gemini-3.5-flash")


class TestModelLocation:
    """The location rule: Gemini 2.x regional, Gemini 3.x and Claude global."""

    @pytest.mark.parametrize(
        "model",
        ["gemini-2.0-flash", "gemini-2.5-flash", "models/gemini-pro"],
    )
    def test_gemini_2x_is_regional(self, model, vertex_env):
        assert model_location(model) == "us-central1"

    @pytest.mark.parametrize(
        "model",
        [
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
        ],
    )
    def test_gemini_3x_and_claude_are_global(self, model, vertex_env):
        assert model_location(model) == "global"

    def test_regional_models_follow_gcp_region(self, monkeypatch):
        """The regional branch tracks GCP_REGION rather than hardcoding us-central1."""
        monkeypatch.setenv("GCP_REGION", "europe-west4")
        assert model_location("gemini-2.5-flash") == "europe-west4"
        assert model_location("gemini-3.5-flash") == "global"

    def test_every_registered_model_has_a_location(self):
        """No model in the registry may fall outside the rule."""
        for name in MODELS:
            assert model_location(name) in {"global", "us-central1"}


class TestDisablePyopenssl:
    def test_no_openssl_is_noop(self):
        with patch.dict("sys.modules", {"OpenSSL": None, "OpenSSL.SSL": None}):
            from wrangler.core.config import disable_pyopenssl

            disable_pyopenssl()

    def test_function_exists_and_callable(self):
        from wrangler.core.config import disable_pyopenssl

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


def test_examples_config_imports_without_mcp_env(monkeypatch):
    """The examples config must import cleanly with no MCP env set.

    build_source_package() rewrites these subscripts to .get() for
    deployment; the source itself should not need that rewrite.
    """
    import importlib
    import sys

    # config.py calls load_dotenv() at import time, which would repopulate the
    # very variables this test deletes. Without this stub the test passes on any
    # machine that has a populated .env and fails only in CI -- exactly backwards.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_args, **_kwargs: False)

    for var in ("SEARCH_MCP_SERVER", "BOOKING_MCP_SERVER", "EXPENSE_MCP_SERVER"):
        monkeypatch.delenv(var, raising=False)

    sys.path.insert(0, "examples/multi_model_agents")
    sys.modules.pop("config", None)
    try:
        importlib.import_module("config")  # must not raise
    finally:
        sys.path.pop(0)
        sys.modules.pop("config", None)


def test_no_hardcoded_project_identifiers():
    """Committed code must not contain a real GCP project id or number.

    This repo is meant to be reusable; a hardcoded project silently points
    a new user's runs at someone else's infrastructure. Prose docs are
    exempt -- markdown may name a project when describing a real run.
    """
    import subprocess

    banned = ("hybrid-vertex", "934903580331")
    tracked = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh", "*.yaml", "*.yml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    # This file has to name the banned strings in order to search for them.
    self_path = Path(__file__).resolve()

    offenders = [
        f
        for f in tracked
        if Path(f).is_file()
        and Path(f).resolve() != self_path
        and any(b in Path(f).read_text(errors="ignore") for b in banned)
    ]
    assert not offenders, f"hardcoded project identifiers in: {offenders}"


def test_config_reexports_registry_not_its_own_tables():
    """config.py must delegate to the registry, not keep a parallel table."""
    import wrangler.core.config as cfg
    from wrangler.core.models import MODELS

    assert set(cfg.MODEL_COSTS) == set(MODELS), "MODEL_COSTS has drifted from the registry"
    assert set(cfg.RATE_LIMITS) == set(MODELS), "RATE_LIMITS has drifted from the registry"
