"""Tests for wrangler.online_monitors — constants and pure helpers."""

from unittest.mock import patch

from wrangler.eval.online_monitors import QUICK_EVAL_CASES, _resolve_agent_resource


class TestResolveAgentResource:
    @patch("wrangler.eval.online_monitors.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.eval.online_monitors.GCP_REGION", "us-central1")
    def test_short_id_expanded(self):
        result = _resolve_agent_resource("12345")
        assert result == "projects/test-project/locations/us-central1/reasoningEngines/12345"

    def test_full_resource_passthrough(self):
        full = "projects/my-proj/locations/us/reasoningEngines/123"
        assert _resolve_agent_resource(full) == full


class TestConstants:
    def test_quick_eval_cases_non_empty(self):
        assert len(QUICK_EVAL_CASES) >= 5
        for case in QUICK_EVAL_CASES:
            assert isinstance(case, str)
            assert len(case) > 0
