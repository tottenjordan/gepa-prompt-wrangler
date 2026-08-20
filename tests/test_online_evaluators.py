"""Tests for wrangler.online_evaluators — constants and pure helpers."""

from unittest.mock import patch

from wrangler.eval.online_evaluators import (
    CUSTOM_METRICS,
    PREDEFINED_METRICS,
    _agent_resource,
    _build_evaluator_config,
)


class TestConstants:
    def test_predefined_metrics_non_empty(self):
        assert len(PREDEFINED_METRICS) >= 4
        for metric in PREDEFINED_METRICS:
            assert isinstance(metric, str)

    def test_custom_metrics_structure(self):
        assert isinstance(CUSTOM_METRICS, list)
        for metric in CUSTOM_METRICS:
            assert "displayName" in metric
            assert "metric" in metric


class TestAgentResource:
    @patch("wrangler.eval.online_evaluators.PROJECT_NUMBER", "123456")
    @patch("wrangler.eval.online_evaluators.GCP_REGION", "us-central1")
    def test_format(self):
        result = _agent_resource("12345")
        assert result == "projects/123456/locations/us-central1/reasoningEngines/12345"


class TestBuildEvaluatorConfig:
    @patch("wrangler.eval.online_evaluators.PROJECT_NUMBER", "123456")
    @patch("wrangler.eval.online_evaluators.GCP_REGION", "us-central1")
    def test_config_structure(self):
        config = _build_evaluator_config("flash", "engine123", ["custom_metric_1"])
        assert "displayName" in config
        assert "Flash" in config["displayName"]
        assert "agentResource" in config
        assert "metricSources" in config

    @patch("wrangler.eval.online_evaluators.PROJECT_NUMBER", "123456")
    @patch("wrangler.eval.online_evaluators.GCP_REGION", "us-central1")
    def test_metric_sources_count(self):
        config = _build_evaluator_config("test", "engine123", ["m1", "m2"])
        sources = config["metricSources"]
        expected = len(PREDEFINED_METRICS) + 2
        assert len(sources) == expected
