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


class TestSpanExportDetection:
    """Dropped span batches are online eval's missing input, and nothing saw them.

    The export timeouts themselves are fixed (docs/notes/silent-failures.md #8),
    but the reason they survived so long is that no code path noticed. These
    cover the detector, not the exporter.
    """

    @staticmethod
    def _run(entries, **kwargs):
        from wrangler.eval import online_evaluators as oe

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"entries": entries}

        with (
            patch.object(oe, "_get_headers", return_value={}),
            patch.object(oe.http_requests, "post", return_value=_Resp()) as post,
        ):
            result = oe.count_span_export_errors("engine-123", **kwargs)
        return result, post.call_args.kwargs["json"]

    def test_counts_dropped_batches(self):
        result, _ = self._run([{"textPayload": "x"}] * 5)
        assert result["dropped_batches"] == 5
        assert result["engine_id"] == "engine-123"

    def test_clean_engine_reports_zero(self):
        result, _ = self._run([])
        assert result["dropped_batches"] == 0
        assert result["truncated"] is False

    def test_flags_truncation_rather_than_undercounting(self):
        """A full page means 'at least this many', and saying so matters.

        Silently reporting the page size as the count is the same shape as the
        --limit trap in repo-traps.md: a capped result read as a total.
        """
        result, _ = self._run([{"textPayload": "x"}] * 200)
        assert result["truncated"] is True

    def test_filter_scopes_to_the_engine_and_the_marker(self):
        _, body = self._run([])
        f = body["filter"]
        assert 'reasoning_engine_id="engine-123"' in f
        assert "Failed to export span batch" in f
        # Must NOT filter on severity: the container logs everything at DEFAULT,
        # so severity>=ERROR returns nothing and looks like a clean engine.
        assert "severity" not in f

    def test_window_is_honoured(self):
        _, body = self._run([], minutes=15)
        assert "timestamp>=" in body["filter"]
        result, _ = self._run([], minutes=15)
        assert result["window_minutes"] == 15
