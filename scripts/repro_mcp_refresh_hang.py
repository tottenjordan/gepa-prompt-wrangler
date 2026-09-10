"""Reproduce the MCP "refresh hang" of silent failure #12, locally and in seconds.

Two conditions are both required, which is why it took three attempts to find:

  1. `get_tools()` must actually reach the session -- a tool-list cache hit never does.
  2. `close()` must run *concurrently* with it. Sequential close-then-get_tools is fine.

Measured here: 9 of 18 calls hang when both hold, 0 in every other combination.

Run it against a local MCP server:

    cd examples/multi_model_agents/mcp_servers/search && uv run python server.py &
    uv run python scripts/repro_mcp_refresh_hang.py

Why this matters in production: candidates share one `McpToolset` (agent.clone() is a
shallow copy), GEPA dispatches evaluations onto one event loop from a worker thread via
`asyncio.run_coroutine_threadsafe`, and the removed per-generation refresh called close()
on that shared object. The tool-list cache masked most of it -- its 300s TTL against a
~286s generation gap is why the leak rate was ~14% rather than constant.
"""

import asyncio
import time

from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

URL = "http://localhost:8001/mcp"


def make(cache_ttl):
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=URL, timeout=20.0, sse_read_timeout=30.0, terminate_on_close=False
        ),
        tool_list_cache_ttl_seconds=cache_ttl,
    )


async def one_trial(cache_ttl, concurrent_close):
    ts = make(cache_ttl)
    await ts.get_tools()

    async def call():
        return await ts.get_tools()

    tasks = [asyncio.create_task(call()) for _ in range(6)]
    if concurrent_close:
        tasks.append(asyncio.create_task(ts.close()))
    t0 = time.time()
    _done, pending = await asyncio.wait(tasks, timeout=10.0)
    hung = len(pending)
    for p in pending:
        p.cancel()
    return hung, time.time() - t0


async def main():
    for ttl, close_it, label in [
        (300.0, False, "cache ON,  no close      "),
        (300.0, True, "cache ON,  close racing  "),
        (None, False, "cache OFF, no close      "),
        (None, True, "cache OFF, close racing  "),
    ]:
        hangs = 0
        for _ in range(3):
            h, _dt = await one_trial(ttl, close_it)
            hangs += h
        print(f"  {label}  hung calls: {hangs}/18")


asyncio.run(main())
