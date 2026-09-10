"""The per-generation MCP session refresh must actually re-establish the session.

Campaign 07's `c07-pro` arm logged `will run without the tools` 16 times across 111
generations (~14%), and every one of those candidates was scored into the objective GEPA
was optimizing against. Diagnosed 2026-09-09 from the server logs PR #51 captured, and the
cause is an interaction between two mitigations that are individually correct:

- `_refreshed_sample` closes each toolset between generations, to survive the idle drop
  documented in CLAUDE.md.
- `tool_list_cache_ttl_seconds=300` serves `tools/list` from a cache on the *toolset
  instance*, so a transient failure does not cost an invocation its whole toolset.

`McpToolset.close()` does not invalidate that cache. So the pre-warm immediately after the
close is answered from cache in ~0.1s and never reconnects, the session stays closed, and
the next real call blocks until ADK's `asyncio.wait_for(timeout=MCP_TIMEOUT_SECONDS)`
fires 120s later with a bare `TimeoutError()` — which is why the operator-facing message
ends in a colon with nothing after it.

The evidence that pins it: subtracting the 120s timeout from each of the 17 logged
failures puts **all 17** hanging calls 6-27s after a session re-warm.
"""

from __future__ import annotations

import collections

import pytest

from wrangler.optimize.optimizer import _invalidate_tool_list_cache


class _FakeToolset:
    """Stands in for `McpToolset`, with the one attribute that matters."""

    def __init__(self, entries: int = 3) -> None:
        self._tool_list_cache: collections.OrderedDict[str, object] = collections.OrderedDict(
            (f"key-{i}", object()) for i in range(entries)
        )
        self.closed = False

    async def close(self) -> None:
        # Mirrors ADK: closing the transport leaves the tool-list cache intact.
        self.closed = True


def test_closing_a_toolset_leaves_adks_cache_populated():
    """The upstream behaviour this guard exists because of.

    If ADK ever starts clearing the cache in `close()`, this fails and the workaround
    can go — that is the point of asserting it rather than assuming it.
    """
    ts = _FakeToolset()
    import asyncio

    asyncio.run(ts.close())
    assert ts.closed
    assert len(ts._tool_list_cache) == 3, (
        "ADK's close() now clears the tool-list cache; re-check whether "
        "_invalidate_tool_list_cache is still needed."
    )


def test_invalidate_empties_the_cache_so_the_next_get_tools_reconnects():
    ts = _FakeToolset()
    assert _invalidate_tool_list_cache(ts) is True
    assert len(ts._tool_list_cache) == 0


def test_invalidate_is_a_no_op_on_a_toolset_without_the_cache():
    """Caching is optional, and a non-MCP BaseToolset has no such attribute."""

    class _NoCache:
        pass

    assert _invalidate_tool_list_cache(_NoCache()) is False


def test_invalidate_survives_a_cache_that_refuses_to_clear():
    """Best-effort: the refresh must not die because invalidation failed.

    The surrounding code closes sessions in a `contextlib.suppress`; a raising cache
    must not be the one thing that escapes and kills the generation.
    """

    class _Hostile:
        @property
        def _tool_list_cache(self):
            raise RuntimeError("boom")

    assert _invalidate_tool_list_cache(_Hostile()) is False


@pytest.mark.parametrize("entries", [0, 1, 64])
def test_invalidate_reports_true_whenever_a_cache_exists(entries):
    """True means 'there was a cache and it is now empty', including when already empty.

    The caller uses the return value to warn when a refresh could not have reconnected,
    so 'no cache attribute' and 'cache present but empty' must not be conflated.
    """
    ts = _FakeToolset(entries)
    assert _invalidate_tool_list_cache(ts) is True
    assert len(ts._tool_list_cache) == 0
