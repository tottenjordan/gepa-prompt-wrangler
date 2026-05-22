"""Flash Agent — handles simple tasks with light reasoning using a fast model."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from config import FLASH_MODEL, SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER, resolve_model
from registry import get_mcp_tools
from prompts.flash_prompts import ACTIVE as INSTRUCTION

AGENT_DESCRIPTION = "Corporate travel and expense assistant with access to flight, hotel, and expense management tools."

flash_agent = LlmAgent(
    model=resolve_model(FLASH_MODEL),
    name="flash_agent",
    description=AGENT_DESCRIPTION,
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

root_agent = flash_agent

import types as _t
agent = _t.SimpleNamespace(root_agent=flash_agent)
