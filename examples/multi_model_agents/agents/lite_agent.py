"""Lite Agent — handles trivial, single-intent lookups using the fastest model."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from config import LITE_MODEL, SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER, resolve_model
from registry import get_mcp_tools
from prompts.lite_prompts import ACTIVE as INSTRUCTION

AGENT_DESCRIPTION = "Corporate travel and expense assistant with access to flight, hotel, and expense management tools."

lite_agent = LlmAgent(
    model=resolve_model(LITE_MODEL),
    name="lite_agent",
    description=AGENT_DESCRIPTION,
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

root_agent = lite_agent

import types as _t
agent = _t.SimpleNamespace(root_agent=lite_agent)
