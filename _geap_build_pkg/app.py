"""Auto-generated GEAP entrypoint (no toolsets)."""
import os
import logging as _log
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from vertexai.agent_engines import AdkApp

from .config import resolve_model

_log.basicConfig(level=_log.INFO)
_here = Path(__file__).parent
INSTRUCTION = (_here / "instruction.txt").read_text().strip()
MODEL = os.environ.get("AGENT_MODEL", "gemini-3.5-flash")

_log.info("[GEAP startup] model=%s, location=%s, project=%s",
          MODEL, os.environ.get("GOOGLE_CLOUD_LOCATION", "unset"),
          os.environ.get("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", "unset")))
_log.info("[GEAP startup] instruction=%d chars, toolsets: none (bare probe build)",
          len(INSTRUCTION))

try:
    resolved = resolve_model(MODEL)
    _log.info("[GEAP startup] model resolved: %s (type=%s)", resolved, type(resolved).__name__)
except Exception as exc:
    _log.error("[GEAP startup] FATAL: resolve_model(%s) failed: %s", MODEL, exc)
    raise

root_agent = LlmAgent(
    model=resolved,
    name="sonnet_agent",
    description="Bare probe agent with no MCP tools, for measuring GEAP request routing.",
    instruction=INSTRUCTION,
    # PreloadMemoryTool is kept deliberately. The factor under test is the three
    # MCP handshakes at import, not "has any tool at all" -- so the two arms
    # differ in exactly one thing.
    tools=[PreloadMemoryTool()],
)

app = AdkApp(agent=root_agent, enable_tracing=True)

_log.info("[GEAP startup] bare probe ready")
