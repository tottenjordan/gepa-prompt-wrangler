"""Reproduce silent failure #12 against the REAL topology, and show patch 8 fixing it.

**This script previously reported the wrong answer, and that is why it is worth reading.**
Its first version raced ONE `close()` against ONE `get_tools()` on a toolset it owned, and
measured 0/18 hangs at the production cache setting (300s) -- which reads as "close-racing
is not the problem at the config we run". Campaign 08 then lost 12% and 24% of its
generations to exactly that. The negative result was about the reproduction, not about
production, and it cost two shipped fixes (PR #59, PR #75) that changed nothing.

What it under-modelled, per docs/analysis/2026-09-12-mcp-toolset-loss-localised.md:

  | | old repro | production |
  | --- | --- | --- |
  | closers | 1 bare `ts.close()` | ~604 `Runner.close()`, in bursts of ~32 |
  | toolset ownership | one local toolset | ONE toolset shared by every candidate |
  | closer identity | the test itself | a *different* candidate's runner |

So this version builds the real thing: one shared `McpToolset`, N short-lived `Runner`s
over `agent.clone()`, each closing it, with `get_tools()` in flight across the burst.

**It runs both with and without patch 8**, because a reproduction that only shows the fixed
state cannot tell you the harness still reproduces the bug. Expect hangs WITHOUT and zero
WITH; if the "without" column is also zero, this script has stopped reproducing and its
green result means nothing -- exactly the failure it is written to avoid.

Still not the acceptance test. That is the `will run without the tools` rate on a real
optimize stage, counted with analyze_toolset_loss.py, because a local reproduction cannot
model 9 hours of concurrent judge traffic.

Usage:
    cd examples/multi_model_agents/mcp_servers/search && uv run python server.py &
    uv run python scripts/repro_mcp_refresh_hang.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time

DEFAULT_URL = "http://localhost:8001/mcp"

# Production settings, not convenient ones. The 300s TTL is the value that made the old
# reproduction look clean, so running at anything else would dodge the original mistake.
PRODUCTION_CACHE_TTL = 300.0
CLOSE_BURST = 32  # measured burst size in the 90-150s band before every loss
READERS = 6
HANG_TIMEOUT = 10.0  # production waits 120s; 10s is unambiguously a hang and keeps this fast


def _build_shared_agent(url: str, cache_ttl: float | None):
    """One toolset, one agent -- the object every candidate ends up sharing."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    from wrangler.core.models import DEFAULT_JUDGE_MODEL

    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url, timeout=20.0, sse_read_timeout=30.0, terminate_on_close=False
        ),
        tool_list_cache_ttl_seconds=cache_ttl,
    )
    agent = LlmAgent(name="shared_agent", model=DEFAULT_JUDGE_MODEL, tools=[toolset])
    return agent, toolset


async def _trial(url: str, cache_ttl: float | None) -> tuple[int, int]:
    """One burst. Returns (hung readers, closes issued).

    The closers are real `Runner.close()` calls over `agent.clone()`, which is the whole
    point: a shallow clone shares the toolset, so the close a runner issues lands on the
    session another reader is using.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    agent, toolset = _build_shared_agent(url, cache_ttl)
    await toolset.get_tools()  # establish the session, as the pre-warm does

    if agent.clone().tools[0] is not toolset:
        raise SystemExit(
            "agent.clone() no longer shares the toolset -- silent-failures #12's mechanism "
            "does not apply as written. Re-derive it before trusting this script."
        )

    # Model the TTL lapse, do NOT lower the TTL. Both conditions must hold to reproduce:
    # get_tools() has to reach the session, and a cache hit never does. The TTL stays at
    # its production 300s; what varies in production is *when* the burst lands relative to
    # it. Generations averaged ~286s apart against a 300s TTL, so the cache covers most
    # refresh windows and lapses at a few -- which is why the leak was ~14% rather than
    # constant. Clearing here puts the burst in one of those lapsed windows deliberately,
    # instead of running ~20 minutes hoping to catch one.
    #
    # The previous version of this script instead set the TTL to None, which is not a
    # setting we run, and then reported the production TTL as clean. Modelling the lapse
    # is the honest form of the same experiment.
    toolset._tool_list_cache.clear()

    async def reader() -> None:
        for _ in range(4):
            await toolset.get_tools()
            await asyncio.sleep(0)

    async def closer() -> None:
        # A fresh short-lived runner per close, exactly as GEPA does per candidate.
        runner = Runner(
            app_name="repro", agent=agent.clone(), session_service=InMemorySessionService()
        )
        await runner.close()

    tasks = [asyncio.create_task(reader()) for _ in range(READERS)]
    tasks += [asyncio.create_task(closer()) for _ in range(CLOSE_BURST)]
    _done, pending = await asyncio.wait(tasks, timeout=HANG_TIMEOUT)
    for p in pending:
        p.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    # Teardown of a session this trial deliberately abused; a failure here says nothing.
    with contextlib.suppress(Exception):
        await toolset.close()
    return len(pending), CLOSE_BURST


async def _run_condition(url: str, trials: int, patched: bool) -> int:
    """Run `trials` bursts with patch 8 on or off. Returns total hung tasks."""
    from wrangler.optimize import optimizer

    hung = 0
    for _ in range(trials):
        if patched:
            async with optimizer._deferred_toolset_closes(tag="   "):
                h, _ = await _trial(url, PRODUCTION_CACHE_TTL)
        else:
            h, _ = await _trial(url, PRODUCTION_CACHE_TTL)
        hung += h
    return hung


async def main_async(args) -> int:
    total = args.trials * READERS
    print(
        f"\nReal topology: 1 shared McpToolset, {CLOSE_BURST} Runner.close() per burst, "
        f"{READERS} readers in flight, cache TTL {PRODUCTION_CACHE_TTL}s (production)\n"
    )

    t0 = time.time()
    unpatched = await _run_condition(args.url, args.trials, patched=False)
    patched = await _run_condition(args.url, args.trials, patched=True)

    print(f"  patch 8 OFF   hung readers: {unpatched}/{total}")
    print(f"  patch 8 ON    hung readers: {patched}/{total}")
    print(f"\n  ({time.time() - t0:.1f}s)\n")

    if unpatched == 0:
        print(
            "INCONCLUSIVE, and do not read the ON row as a pass. With the patch OFF this\n"
            "harness did not reproduce the bug, so it cannot demonstrate a fix -- the same\n"
            "way the previous version of this script 'exonerated' close-racing. Raise\n"
            "--trials or CLOSE_BURST, or check the server is actually up.",
            file=sys.stderr,
        )
        return 2
    if patched:
        print(f"FAIL: patch 8 left {patched} hung reader(s).", file=sys.stderr)
        return 1
    print(
        "Reproduced with the patch off, gone with it on.\n"
        "NOT the acceptance test: that is the `will run without the tools` rate on a real\n"
        "optimize stage via analyze_toolset_loss.py. A local burst cannot model 9 hours."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL, help="local MCP server URL")
    ap.add_argument("--trials", type=int, default=3, help="bursts per condition")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
