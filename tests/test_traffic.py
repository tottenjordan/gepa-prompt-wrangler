"""Tests for wrangler.traffic — pure helpers and constants."""

import asyncio
from unittest.mock import patch

from wrangler.tools.traffic import (
    DEFAULT_QUERIES,
    _event_text,
    _resolve_resource,
    _stream,
    generate_traffic,
    summarize_run,
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


class ExplodingOnceAgent(FakeAgent):
    """Raises on the first stream, then behaves. Models a transient blip."""

    def __init__(self, answer):
        super().__init__([answer])
        self._exploded = False

    async def async_stream_query(self, user_id, session_id, message):
        if not self._exploded:
            self._exploded = True
            self.calls += 1
            raise ConnectionError("connection reset by peer")
        async for event in super().async_stream_query(user_id, session_id, message):
            yield event


class ConcurrencyTrackingAgent(FakeAgent):
    """Records the high-water mark of simultaneous in-flight streams."""

    def __init__(self, answer):
        super().__init__([answer])
        self.in_flight = 0
        self.max_in_flight = 0

    async def async_stream_query(self, user_id, session_id, message):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Yield control so other tasks can pile up — without an await here
            # the coroutine runs to completion before any sibling starts and
            # the high-water mark would read 1 no matter what.
            await asyncio.sleep(0.01)
            async for event in super().async_stream_query(user_id, session_id, message):
                yield event
        finally:
            self.in_flight -= 1


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


class TestSummarizeRun:
    """The attempt rate is the number that must survive into the report.

    Retries can carry the trace count to 100% while the engine is still eating
    three of every four requests. A summary that showed only traces-per-query
    would hide the very defect this tool exists to observe.
    """

    def test_reports_attempt_rate_not_just_traces(self):
        lines = "\n".join(summarize_run(traces=10, queries=10, attempts=40))
        assert "Traces emitted: 10" in lines
        assert "Attempts spent: 40" in lines
        assert "25%" in lines

    def test_warns_when_attempt_rate_is_on_the_floor(self):
        """Every query landed a trace, but only after four tries each."""
        lines = "\n".join(summarize_run(traces=10, queries=10, attempts=40))
        assert "Attempt rate below" in lines
        assert "booting workers" in lines

    def test_quiet_when_the_engine_is_healthy(self):
        lines = "\n".join(summarize_run(traces=10, queries=10, attempts=11))
        assert "Attempt rate below" not in lines
        assert "NO trace" not in lines

    def test_counts_queries_that_never_landed(self):
        lines = "\n".join(summarize_run(traces=7, queries=10, attempts=50))
        assert "3 queries produced NO trace" in lines

    def test_no_division_by_zero_on_an_empty_run(self):
        lines = "\n".join(summarize_run(traces=0, queries=0, attempts=0))
        assert "0%" in lines


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
        assert "Traces emitted: 2" in out

    def test_empty_stream_is_retried(self, capsys):
        """A cold GEAP worker answers 200 with no events; the next one is warm."""
        agent = FakeAgent([EMPTY, ANSWER])
        out = self._run(agent, capsys, count=1)
        assert "an answer" in out
        assert "Traces emitted: 1" in out
        assert "2 attempt(s)" in out
        assert agent.calls == 2

    def test_spends_the_full_attempt_budget_before_giving_up(self, capsys):
        """Six attempts, not three: at ~1-in-4 per attempt, three lands ~58%."""
        agent = FakeAgent([EMPTY])
        out = self._run(agent, capsys, count=1)
        assert "no trace after 6 attempts" in out
        assert "1 queries produced NO trace" in out
        assert agent.calls == 6

    def test_attempt_budget_is_configurable(self, capsys):
        agent = FakeAgent([EMPTY])
        self._run(agent, capsys, count=1, max_attempts=2)
        assert agent.calls == 2

    def test_a_transient_exception_does_not_abandon_the_query(self, capsys):
        """The old code returned on the first exception, spending no more attempts.

        The failure this tool exists around is itself transient, so one blip
        should cost an attempt, not the trace.
        """
        agent = ExplodingOnceAgent(ANSWER)
        out = self._run(agent, capsys, count=1)
        assert "an answer" in out
        assert "Traces emitted: 1" in out
        assert agent.calls == 2

    def test_concurrency_is_bounded(self, capsys):
        agent = ConcurrencyTrackingAgent(ANSWER)
        self._run(agent, capsys, count=12, concurrency=3)
        assert agent.max_in_flight <= 3, f"ran {agent.max_in_flight} at once"
        assert agent.max_in_flight > 1, "did not actually run concurrently"

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
        assert "error:" not in out


class TestDefaultQueries:
    def test_non_empty(self):
        assert len(DEFAULT_QUERIES) >= 10

    def test_all_have_complexity(self):
        for query, complexity in DEFAULT_QUERIES:
            assert isinstance(query, str)
            assert isinstance(complexity, str)
            assert len(query) > 0
