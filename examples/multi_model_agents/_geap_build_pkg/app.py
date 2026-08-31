"""Auto-generated GEAP entrypoint."""
import os
import asyncio
import logging as _log
import time as _time
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from vertexai.agent_engines import AdkApp

from .config import resolve_model, SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER
from .registry import get_mcp_tools

_log.basicConfig(level=_log.INFO)
_here = Path(__file__).parent
INSTRUCTION = (_here / "instruction.txt").read_text().strip()
MODEL = os.environ.get("AGENT_MODEL", "gemini-3.1-pro-preview")

# Budget for one MCP handshake in the startup CHECK. Not 30s: see the comment
# in _startup_checks. Matches examples/multi_model_agents/registry.py's
# MCP_TIMEOUT_SECONDS -- keep the two in step.
_MCP_PROBE_TIMEOUT = float(os.environ.get("MCP_PROBE_TIMEOUT_SECONDS", "120"))

_log.info("[GEAP startup] model=%s, location=%s, project=%s",
          MODEL, os.environ.get("GOOGLE_CLOUD_LOCATION", "unset"),
          os.environ.get("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", "unset")))
_log.info("[GEAP startup] instruction=%d chars, MCP servers: search=%s, booking=%s, expense=%s",
          len(INSTRUCTION), bool(SEARCH_MCP_SERVER), bool(BOOKING_MCP_SERVER), bool(EXPENSE_MCP_SERVER))

# -- Validate model connectivity before serving --
try:
    resolved = resolve_model(MODEL)
    _log.info("[GEAP startup] model resolved: %s (type=%s)", resolved, type(resolved).__name__)
except Exception as exc:
    _log.error("[GEAP startup] FATAL: resolve_model(%s) failed: %s", MODEL, exc)
    raise

root_agent = LlmAgent(
    model=resolved,
    name="pro_agent",
    description="Corporate travel and expense assistant with access to flight, hotel, and expense management tools.",
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

app = AdkApp(agent=root_agent, enable_tracing=True)

# -- Startup checks: MCP tools + model ping --
async def _startup_checks():
    # 1. MCP reachability.
    #
    # Deliberately builds THROWAWAY toolsets rather than probing
    # root_agent.tools. McpToolset caches its session on the event loop that
    # first opened it, and these checks run on their own short-lived loop. A
    # probe against the serving agent's toolsets therefore opens sessions,
    # then closes the loop underneath them, leaving the *serving* path holding
    # sessions bound to a dead loop -- visible in the logs as
    #
    #   Cleaning up session (disconnected or different loop): session_no_headers
    #   Error cleaning up session ...: original event loop is closed
    #
    # The agent then answers ordinary prompts perfectly and dies the moment the
    # model emits a function_call: the tool await never reaches the network, no
    # HTTP request is logged, no exception surfaces, and the caller gets 200
    # with an empty body and zero events.
    #
    # A health check must not share mutable connection state with the thing it
    # is checking.
    # 30s was too tight and produced a fake defect. Measured 2026-08-31 on the
    # v4 engines: workers that passed reached their MCP summary a median 6.3s
    # after import, while workers that "failed" took a median 109s and up to
    # 834s -- the containers were starved, not the MCP servers, which returned
    # 200 throughout. On one worker the handshake completed five seconds AFTER
    # the probe had already logged a failure. Matches the 120s the local
    # registry.py uses; keep the two in step.
    _t0 = _time.monotonic()
    mcp_ok, mcp_fail = 0, 0
    for server in (SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER):
        if not server:
            continue
        probe = None
        try:
            probe = get_mcp_tools(server)
            tools = await asyncio.wait_for(probe.get_tools(), timeout=_MCP_PROBE_TIMEOUT)
            tool_names = [t.name for t in tools] if tools else []
            _log.info("[GEAP startup] MCP OK: %s -> %d tools %s",
                      server, len(tool_names), tool_names[:3])
            mcp_ok += 1
        except Exception as exc:
            # Log the exception *type*, not just str(exc). The most common
            # failure here is the wait_for above, and TimeoutError's str() is
            # the empty string -- which rendered every one of these as
            # "MCP FAILED: <server> -> " and said nothing about what happened.
            _log.error("[GEAP startup] MCP FAILED: %s -> %s: %s",
                       server, type(exc).__name__, exc or "(no detail)")
            mcp_fail += 1
        finally:
            # Close on the same loop that opened it, so nothing outlives this
            # coroutine. Failure to close is not itself a health signal.
            if probe is not None and hasattr(probe, "close"):
                try:
                    await probe.close()
                except Exception as exc:
                    _log.debug("[GEAP startup] probe close failed for %s: %s", server, exc)
    _log.info("[GEAP startup] MCP summary: %d OK, %d failed (%.1fs)",
              mcp_ok, mcp_fail, _time.monotonic() - _t0)
    if mcp_ok == 0 and mcp_fail > 0:
        # Deliberately NOT "the agent cannot use tools". These are throwaway
        # probe toolsets; the serving agent builds its own and connects lazily
        # on first use. The old wording said the agent was broken, and it was
        # not -- verified 2026-08-31 by sending a tool-requiring query to an
        # engine that had logged exactly this line: it called search_flights
        # correctly. Claiming a fatal error that is not one is how a real one
        # stops being believed.
        _log.error("[GEAP startup] MCP probe failed for all %d server(s) in %.1fs. "
                   "This is the startup CHECK, not the serving toolsets -- the agent "
                   "may still work. A slow container is the usual cause; compare this "
                   "duration against the ~6s a healthy worker takes.",
                   mcp_fail, _time.monotonic() - _t0)

    # 2. Model ping — send a trivial request to verify the model endpoint works.
    #
    # Gemini only. The genai client resolves a bare model id under
    # publishers/google/, so pinging a Claude id here asks for
    # publishers/google/models/claude-sonnet-4-6 and always 404s -- a false
    # alarm that says nothing about the agent, which reaches Claude through
    # resolve_model()'s publishers/anthropic/ path. Skip rather than lie.
    if not MODEL.startswith("gemini"):
        _log.info("[GEAP startup] model ping skipped for non-Gemini model %s "
                  "(resolved via %s)", MODEL, type(resolved).__name__)
        return
    try:
        from google import genai
        client = genai.Client(vertexai=True,
                              project=os.environ.get("GCP_PROJECT_ID", ""),
                              location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
        response = await client.aio.models.generate_content(
            model=MODEL, contents="Say OK")
        _log.info("[GEAP startup] model ping OK: %s",
                  response.text[:50] if response.text else "(empty)")
    except Exception as exc:
        _log.error("[GEAP startup] FATAL: model ping failed for %s: %s", MODEL, exc)

# Run the checks on a dedicated thread with its own event loop.
#
# The previous form was a bare `asyncio.run(_startup_checks())` guarded by an
# except that swallowed "cannot be called from a running event loop". Under
# GEAP that guard always fired: the module is imported from inside a running
# loop, asyncio.run() raised immediately, and the coroutine was never awaited
# (visible only as a RuntimeWarning). So the checks NEVER ran, and an agent
# that could not reach its MCP servers started up looking perfectly healthy.
#
# A daemon thread runs regardless of whether a loop is already active. Failures
# are logged, never raised — a broken MCP server should be loud in the logs, not
# a crash-loop on startup.
def _run_startup_checks_in_background():
    import threading

    def _target():
        try:
            asyncio.run(_startup_checks())
        except Exception as exc:
            _log.error("[GEAP startup] startup checks failed: %s", exc, exc_info=True)

    threading.Thread(target=_target, name="geap-startup-checks", daemon=True).start()


_run_startup_checks_in_background()
