"""The optimize stage must not refresh MCP sessions between generations.

Silent failure #12: a tool-using agent is evaluated with an empty toolset and scored into
the objective GEPA is searching against. Campaign 07 lost ~14% of its generations that way.

Two wrong answers were shipped before the right one, and both are pinned here so they
cannot come back.

**Wrong answer 1 — "the tool-list cache survives close()".** PR #59 added a helper to clear
it after `close()`. `McpToolset.close()` has always cleared it itself, as the first
statement in the method, so the helper was a no-op. It shipped because its test asserted
the claim against a *fake* toolset written to behave that way — it tested the model, not
ADK. `test_adk_close_clears_its_own_cache` now asserts it against the real class.

**Wrong answer 2 — "0.1s re-warms prove the sessions are stale".** Three *localhost* round
trips genuinely take about that; three *remote* ones measured 0.76s. The timing said
nothing either way. What actually disproved the cache theory was campaign 08 running with
the fix and the failure rate not moving: 3 in 20 generations against campaign 07's 16 in
111.

**What the evidence supports.** All 17 of campaign 07's hangs began 6-27s after a re-warm,
the servers logged 3,626 x 200 OK with zero errors, and the client-side failure is an
argument-less `TimeoutError` from `asyncio.wait_for` in ADK's `_execute_with_session`. The
refresh is the thing correlated with the failures, so the refresh is what goes.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

OPTIMIZER = Path(__file__).resolve().parents[1] / "wrangler" / "optimize" / "optimizer.py"


def _refreshed_sample_source() -> str:
    """The body of the per-generation sampler wrapper, located via the AST."""
    tree = ast.parse(OPTIMIZER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_refreshed_sample":
            return ast.get_source_segment(OPTIMIZER.read_text(), node) or ""
    raise AssertionError("_refreshed_sample not found in optimizer.py")


class TestTheRefreshIsGone:
    def test_no_toolset_is_closed_between_generations(self):
        """The one change that addresses #12.

        Closing every session each generation destroys pooled sessions ADK would have
        kept for 900s, and does it without ADK's in-flight guard.
        """
        src = _refreshed_sample_source()
        assert ".close()" not in src, (
            "the per-generation sampler wrapper closes toolsets again. All 17 of campaign "
            "07's toolset losses began 6-27s after a re-warm; see silent-failures #12."
        )

    def test_no_prewarm_between_generations(self):
        src = _refreshed_sample_source()
        assert "_prewarm_mcp_toolsets" not in src, (
            "re-warming per generation is the other half of the refresh that was removed"
        )

    def test_the_single_prewarm_before_the_run_survives(self):
        """Removing the refresh must not remove the one warm-up that is useful.

        The first generation otherwise pays connection setup inside its own timeout.
        """
        src = OPTIMIZER.read_text()
        assert "await _prewarm_mcp_toolsets(root_agent, tag)" in src, (
            "the single pre-warm before optimize() started has gone too; only the "
            "per-generation refresh should have been removed"
        )

    def test_the_dead_helper_is_gone(self):
        """PR #59's `_invalidate_tool_list_cache` duplicated ADK's own close()."""
        assert "_invalidate_tool_list_cache" not in OPTIMIZER.read_text(), (
            "the no-op cache helper is back; ADK's McpToolset.close() already clears it"
        )


class TestWhyThatHelperWasANoOp:
    """Asserted against the real ADK class, which is the check PR #59 skipped."""

    def test_adk_close_clears_its_own_cache(self):
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

        toolset = McpToolset(
            # Never connected to: close() clears the cache before touching the session.
            connection_params=StreamableHTTPConnectionParams(url="http://127.0.0.1:1/mcp"),
            tool_list_cache_ttl_seconds=300.0,
        )
        toolset._tool_list_cache["key"] = object()
        assert len(toolset._tool_list_cache) == 1

        asyncio.run(toolset.close())

        assert len(toolset._tool_list_cache) == 0, (
            "ADK no longer clears the tool-list cache in close(). If that has genuinely "
            "changed, the removed helper may be worth reinstating -- but check the "
            "failure rate, not the mechanism."
        )

    def test_it_is_the_first_thing_close_does(self):
        """So it happens even if the session teardown below it raises."""
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

        body = [
            line.strip()
            for line in inspect.getsource(McpToolset.close).split("\n")
            if line.strip() and not line.strip().startswith(("async def", "#", '"""', "'''"))
        ]
        # Skip the docstring body lines, which are not code.
        code = [line for line in body if "_tool_list_cache" in line or line.startswith("try")]
        assert code, "could not locate the cache clear in close()"
        assert "_tool_list_cache.clear()" in code[0], (
            f"cache clear is no longer the first statement in close(): {code[:2]}"
        )


class TestADKsPoolingIsWhatWeNowRelyOn:
    """Removing our refresh hands session lifetime to ADK. Pin what that means."""

    def test_the_idle_ttl_comfortably_exceeds_our_generation_gap(self):
        """~286s between generations against ADK's TTL: nothing idles out in the gap."""
        from google.adk.tools.mcp_tool import mcp_session_manager as mgr

        ttl = mgr._SESSION_IDLE_TTL_SECONDS
        assert ttl >= 600, (
            f"ADK's session idle TTL is now {ttl}s. Campaign generations are ~286s apart, "
            "so a TTL near or below that would let sessions expire between generations -- "
            "which is the condition the removed refresh was written for."
        )

    def test_the_idle_sweep_protects_calls_in_flight(self):
        """The guard our own close() did not have."""
        from google.adk.tools.mcp_tool import mcp_session_manager as mgr

        src = inspect.getsource(mgr.MCPSessionManager._evict_idle_sessions)
        assert "_session_use_counts" in src, (
            "ADK's idle sweep no longer checks for in-flight calls, so relying on its "
            "pooling instead of our own refresh is no longer clearly safer"
        )
