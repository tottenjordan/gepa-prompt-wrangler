"""Tests for wrangler.traffic — pure helpers and constants."""

import asyncio
from unittest.mock import patch

from wrangler.tools.traffic import (
    DEFAULT_QUERIES,
    _event_text,
    _resolve_resource,
    _stream,
    generate_traffic,
)


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


class FakeAgent:
    """Stands in for the AgentEngine proxy, which builds its methods at runtime.

    `responses` is a list of event-lists, one per call — so a test can make the
    first call come back empty (GEAP's cold-worker behaviour) and the next one
    succeed. The last entry repeats once exhausted.
    """

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0
        self.session_ids = []

    def create_session(self, user_id):
        return {"id": f"session-{user_id}-{len(self.session_ids)}"}

    def stream_query(self, **kwargs):
        raise AssertionError(
            "sync stream_query is deprecated in ADK's class-methods list "
            "— traffic generation must use async_stream_query"
        )

    async def async_stream_query(self, user_id, session_id, message):
        self.session_ids.append(session_id)
        events = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        for event in events:
            yield event


class TestEventText:
    def test_extracts_text_from_parts(self):
        event = {"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}
        assert _event_text(event) == "hello world"

    def test_skips_function_call_and_response_parts(self):
        event = {
            "content": {
                "parts": [
                    {"text": "searching"},
                    {"function_call": {"name": "search_flights", "args": {}}},
                    {"function_response": {"name": "search_flights", "response": {}}},
                ]
            }
        }
        assert _event_text(event) == "searching"

    def test_event_with_no_content(self):
        assert _event_text({}) == ""

    def test_object_with_text_attribute(self):
        class Chunk:
            text = "from an object"

        assert _event_text(Chunk()) == "from an object"


ANSWER = [{"content": {"parts": [{"text": "an answer"}]}}]
EMPTY: list = []


class TestStream:
    def test_returns_text_and_event_count(self):
        agent = FakeAgent(
            [
                [
                    {"content": {"parts": [{"text": "I'll search. "}]}},
                    {"content": {"parts": [{"function_call": {"name": "search_flights"}}]}},
                    {"content": {"parts": [{"text": "Found FL001."}]}},
                ]
            ]
        )
        text, count = asyncio.run(_stream(agent, "u1", "s1", "find flights"))
        assert text == "I'll search. Found FL001."
        assert count == 3

    def test_empty_stream_reports_zero(self):
        text, count = asyncio.run(_stream(FakeAgent([EMPTY]), "u1", "s1", "q"))
        assert text == ""
        assert count == 0


class TestGenerateTraffic:
    """The whole point of this tool is emitting traces, so silence is a failure."""

    @staticmethod
    def _run(agent, capsys, **kwargs):
        with (
            patch("wrangler.tools.traffic.vertexai.init"),
            patch("wrangler.tools.traffic.disable_pyopenssl"),
            patch("wrangler.tools.traffic.agent_engines.get", return_value=agent),
        ):
            generate_traffic(agent_ids=["123"], interval=0, **kwargs)
        return capsys.readouterr().out

    def test_uses_async_stream_query(self, capsys):
        out = self._run(FakeAgent([ANSWER]), capsys, count=2)
        assert "an answer" in out
        assert "Errors:        0" in out

    def test_empty_stream_is_retried(self, capsys):
        """A cold GEAP worker answers 200 with no events; the next one is warm."""
        agent = FakeAgent([EMPTY, ANSWER])
        out = self._run(agent, capsys, count=1)
        assert "Empty stream" in out
        assert "an answer" in out
        assert "Errors:        0" in out
        assert agent.calls == 2

    def test_persistently_empty_counts_as_an_error(self, capsys):
        agent = FakeAgent([EMPTY])
        out = self._run(agent, capsys, count=1)
        assert "No events after 3 attempts" in out
        assert "Errors:        1" in out
        assert agent.calls == 3

    def test_each_attempt_gets_a_fresh_session(self, capsys):
        agent = FakeAgent([EMPTY, ANSWER])
        self._run(agent, capsys, count=1)
        assert len(agent.session_ids) == 2
        assert len(set(agent.session_ids)) == 2

    def test_each_query_gets_its_own_session(self, capsys):
        agent = FakeAgent([ANSWER])
        self._run(agent, capsys, count=3)
        assert len(agent.session_ids) == 3
        assert len(set(agent.session_ids)) == 3

    def test_sync_stream_query_is_never_called(self, capsys):
        """FakeAgent.stream_query raises; a regression here surfaces as an error line."""
        out = self._run(FakeAgent([ANSWER]), capsys, count=1)
        assert "x Error" not in out


class TestDefaultQueries:
    def test_non_empty(self):
        assert len(DEFAULT_QUERIES) >= 10

    def test_all_have_complexity(self):
        for query, complexity in DEFAULT_QUERIES:
            assert isinstance(query, str)
            assert isinstance(complexity, str)
            assert len(query) > 0
