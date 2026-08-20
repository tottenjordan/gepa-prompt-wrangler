"""Sonnet Agent — handles complex, multi-intent requests using Claude Sonnet."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from config import (
    SONNET_MODEL,
    SEARCH_MCP_SERVER,
    BOOKING_MCP_SERVER,
    EXPENSE_MCP_SERVER,
    resolve_model,
)
from registry import get_mcp_tools
from prompts.sonnet_prompts import OPTIMIZED

INSTRUCTION = OPTIMIZED["wrangler_v4"]["prompt"]

AGENT_DESCRIPTION = "Corporate travel and expense assistant with access to flight, hotel, and expense management tools."

sonnet_agent = LlmAgent(
    model=resolve_model(SONNET_MODEL),
    name="sonnet_agent",
    description=AGENT_DESCRIPTION,
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

root_agent = sonnet_agent

import types as _t

agent = _t.SimpleNamespace(root_agent=sonnet_agent)
