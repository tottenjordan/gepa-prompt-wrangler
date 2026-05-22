"""Pro Agent — handles moderate tasks requiring reasoning using Gemini Pro."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from config import PRO_MODEL, SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER, resolve_model
from registry import get_mcp_tools
from prompts.pro_prompts import ACTIVE as INSTRUCTION

AGENT_DESCRIPTION = "Corporate travel and expense assistant with access to flight, hotel, and expense management tools."

pro_agent = LlmAgent(
    model=resolve_model(PRO_MODEL),
    name="pro_agent",
    description=AGENT_DESCRIPTION,
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

root_agent = pro_agent

import types as _t
agent = _t.SimpleNamespace(root_agent=pro_agent)
