"""Template agent with MCP tools.

Replace the MCP server URL with your deployed MCP server.
The create_agent() factory is used by wrangler to inject different models and prompts.

## Tool Name Convention

When using MCP tools via Agent Registry, tool names get prefixed:

    {server_name}_{tool_function_name}

For example, if your MCP server is registered as "my-api-server" and has a
tool called "search_items", the eval case should use:

    my_api_server_search_items

Use `wrangler inspect .` to discover the exact tool names.
"""

import os

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseServerParams


# ---------------------------------------------------------------------------
# MCP Server Connection
# ---------------------------------------------------------------------------

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8080/sse")

mcp_tools = McpToolset(
    connection_params=SseServerParams(url=MCP_SERVER_URL),
)


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_INSTRUCTION = "You are a helpful assistant. Use the available tools to answer user questions."


def create_agent(model: str = DEFAULT_MODEL, instruction: str = DEFAULT_INSTRUCTION) -> Agent:
    """Factory function for wrangler integration."""
    from wrangler.core.config import resolve_model
    return Agent(
        model=resolve_model(model),
        name="my_mcp_agent",
        description="Template agent with MCP tools.",
        instruction=instruction,
        tools=[mcp_tools],
    )


root_agent = create_agent()
