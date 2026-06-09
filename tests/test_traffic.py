"""Tests for wrangler.traffic — pure helpers and constants."""

import pytest
from unittest.mock import patch

from wrangler.tools.traffic import DEFAULT_QUERIES, _resolve_resource


class TestResolveResource:
    @patch("wrangler.tools.traffic.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.tools.traffic.GCP_REGION", "us-central1")
    def test_short_id_expanded(self):
        result = _resolve_resource("12345")
        assert result == "projects/test-project/locations/us-central1/reasoningEngines/12345"

    def test_full_resource_passthrough(self):
        full = "projects/my-proj/locations/us/reasoningEngines/123"
        assert _resolve_resource(full) == full

    @patch("wrangler.tools.traffic.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.tools.traffic.GCP_REGION", "us-central1")
    def test_empty_id(self):
        result = _resolve_resource("")
        assert "reasoningEngines/" in result


class TestDefaultQueries:
    def test_non_empty(self):
        assert len(DEFAULT_QUERIES) >= 10

    def test_all_have_complexity(self):
        for query, complexity in DEFAULT_QUERIES:
            assert isinstance(query, str)
            assert isinstance(complexity, str)
            assert len(query) > 0
