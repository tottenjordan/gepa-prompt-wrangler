"""Tests for wrangler.tools.boot_probe — the GEAP request-routing probe.

The probe exists because every number we have about the empty-stream defect
(docs/notes/silent-failures.md #5) came from a tool built to *land* traces.
Retries turn a 17% server-side success rate into a respectable-looking trace
count, and the interesting unit — the individual attempt — is not recorded at
all. This records every attempt and never retries.
"""

import asyncio
import itertools
import json
from pathlib import Path

import pytest

from wrangler.tools import boot_probe


class _StubAgent:
    """An engine that returns a scripted number of events per attempt.

    ``script`` is consumed one entry per attempt; an entry may be an int (that
    many events) or an Exception to raise. Records the wall-clock interval of
    every stream so overlap can be asserted.
    """

    def __init__(self, script):
        self.script = list(script)
        self.intervals = []
        self.sessions = []

    def create_session(self, user_id):
        self.sessions.append(user_id)
        return {"id": f"session-for-{user_id}"}

    def async_stream_query(self, user_id, session_id, message):
        outcome = self.script.pop(0) if self.script else 0

        async def _gen():
            start = asyncio.get_running_loop().time()
            await asyncio.sleep(0.01)
            if isinstance(outcome, Exception):
                self.intervals.append((start, asyncio.get_running_loop().time()))
                raise outcome
            for i in range(outcome):
                yield {"content": {"parts": [{"text": f"event-{i}"}]}}
            self.intervals.append((start, asyncio.get_running_loop().time()))

        return _gen()


class TestWilsonInterval:
    """A reach rate without an interval is how n=12 got mistaken for a 5x win.

    The pacing arm read 5/12 vs 1/12 and collapsed to nothing at n=30
    (silent-failures.md #5). The interval is what would have said so at the
    time.
    """

    def test_half_of_ten(self):
        lo, hi = boot_probe.wilson_interval(5, 10)
        assert lo == pytest.approx(0.2366, abs=0.001)
        assert hi == pytest.approx(0.7634, abs=0.001)

    def test_zero_successes_has_a_nonzero_upper_bound(self):
        lo, hi = boot_probe.wilson_interval(0, 10)
        assert lo == 0.0
        assert hi == pytest.approx(0.2775, abs=0.001)

    def test_no_observations_is_maximally_uncertain(self):
        assert boot_probe.wilson_interval(0, 0) == (0.0, 1.0)

    def test_narrows_with_n(self):
        _, hi_small = boot_probe.wilson_interval(40, 100)
        _, hi_large = boot_probe.wilson_interval(160, 400)
        assert hi_large < hi_small


class TestAttemptRows:
    def test_events_mean_reached(self):
        agent = _StubAgent([3])
        rows = asyncio.run(boot_probe.run_arm(agent, "bare-claude", "eng1", n=1, spacing=0.0))
        assert len(rows) == 1
        assert rows[0]["reached"] is True
        assert rows[0]["event_count"] == 3
        assert rows[0]["error"] == ""

    def test_zero_events_is_the_defect_and_is_not_an_error(self):
        """The empty stream is a 200 with no events. It must not look like a crash."""
        agent = _StubAgent([0])
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=1, spacing=0.0))
        assert rows[0]["reached"] is False
        assert rows[0]["event_count"] == 0
        assert rows[0]["error"] == ""

    def test_an_exception_is_recorded_separately_from_an_empty_stream(self):
        agent = _StubAgent([RuntimeError("connection reset")])
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=1, spacing=0.0))
        assert rows[0]["reached"] is False
        assert "connection reset" in rows[0]["error"]

    def test_rows_carry_the_join_fields(self):
        agent = _StubAgent([1])
        rows = asyncio.run(boot_probe.run_arm(agent, "mcp-gemini", "eng42", n=1, spacing=0.0))
        row = rows[0]
        for key in (
            "arm",
            "engine_id",
            "attempt_index",
            "nonce",
            "sent_at",
            "finished_at",
            "latency_s",
            "event_count",
            "reached",
            "error",
        ):
            assert key in row, key
        assert row["arm"] == "mcp-gemini"
        assert row["engine_id"] == "eng42"
        assert row["latency_s"] > 0

    def test_the_nonce_reaches_the_prompt(self):
        """A join key costs nothing here and cannot be added after the fact."""
        agent = _StubAgent([1])
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=1, spacing=0.0))
        assert rows[0]["nonce"]
        assert rows[0]["nonce"] in rows[0]["prompt"]

    def test_nonces_are_unique_per_attempt(self):
        agent = _StubAgent([1, 1, 1])
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=3, spacing=0.0))
        assert len({r["nonce"] for r in rows}) == 3


class TestNoRetries:
    """A retry destroys the unit of observation.

    traffic.py spends six attempts to land one trace, which is correct for its
    job and fatal for this one: the recorded outcome becomes "did any of six
    succeed" rather than "did this request succeed".
    """

    def test_every_attempt_is_recorded_even_when_all_fail(self):
        agent = _StubAgent([0] * 5)
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=5, spacing=0.0))
        assert len(rows) == 5
        assert all(r["reached"] is False for r in rows)

    def test_attempt_indices_are_dense(self):
        agent = _StubAgent([0, 1, 0, 1])
        rows = asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=4, spacing=0.0))
        assert [r["attempt_index"] for r in rows] == [0, 1, 2, 3]


class TestSerialization:
    """One request in flight per engine is what makes the log join unambiguous.

    With two requests overlapping, two `POST /api/stream_reasoning_engine` log
    lines fall inside both client windows and neither can be attributed.
    """

    def test_attempts_do_not_overlap(self):
        agent = _StubAgent([1] * 6)
        asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=6, spacing=0.0))
        intervals = sorted(agent.intervals)
        for (_, prev_end), (next_start, _) in itertools.pairwise(intervals):
            assert next_start >= prev_end

    def test_each_attempt_gets_a_fresh_session(self):
        agent = _StubAgent([1] * 3)
        asyncio.run(boot_probe.run_arm(agent, "arm", "eng1", n=3, spacing=0.0))
        assert len(set(agent.sessions)) == 3


class TestBlocks:
    def test_blocks_are_labelled(self):
        agent = _StubAgent([1] * 12)
        rows = asyncio.run(
            boot_probe.run_arm(agent, "arm", "eng1", n=12, spacing=0.0, block_size=3)
        )
        assert [r["block"] for r in rows] == [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]

    def test_a_block_size_that_does_not_divide_n_still_covers_every_attempt(self):
        agent = _StubAgent([1] * 10)
        rows = asyncio.run(
            boot_probe.run_arm(agent, "arm", "eng1", n=10, spacing=0.0, block_size=4)
        )
        assert len(rows) == 10
        assert [r["block"] for r in rows] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]


class TestJsonl:
    def test_one_line_per_row_and_appends(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        boot_probe.append_rows(path, [{"a": 1}, {"a": 2}])
        boot_probe.append_rows(path, [{"a": 3}])
        lines = path.read_text().strip().split("\n")
        assert [json.loads(x)["a"] for x in lines] == [1, 2, 3]

    def test_creates_parent_directories(self, tmp_path: Path):
        path = tmp_path / "nested" / "deeper" / "run.jsonl"
        boot_probe.append_rows(path, [{"a": 1}])
        assert path.exists()


class TestSummarize:
    def test_reports_per_arm_reach_with_an_interval(self):
        rows = [{"arm": "a", "reached": True}] * 3 + [{"arm": "a", "reached": False}] * 7
        rows += [{"arm": "b", "reached": True}] * 8 + [{"arm": "b", "reached": False}] * 2
        summary = boot_probe.summarize(rows)
        assert summary["a"]["n"] == 10
        assert summary["a"]["reached"] == 3
        assert summary["a"]["rate"] == pytest.approx(0.3)
        assert summary["a"]["ci_low"] < 0.3 < summary["a"]["ci_high"]
        assert summary["b"]["rate"] == pytest.approx(0.8)

    def test_an_arm_with_no_rows_is_absent_rather_than_zero(self):
        """Zero reach and no data are different claims."""
        assert boot_probe.summarize([]) == {}
