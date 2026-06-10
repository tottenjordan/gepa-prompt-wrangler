"""Agent Registry integration — discovers MCP servers by registered name.

Falls back to direct Cloud Run URLs when the Agent Registry entry is not found.
"""

import logging

import httpx
from google.adk.integrations.agent_registry import AgentRegistry
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from config import GCP_PROJECT_ID, AGENT_REGISTRY_LOCATION, MCP_SERVER_URLS

log = logging.getLogger(__name__)

MCP_TIMEOUT_SECONDS = 120.0
MCP_READ_TIMEOUT_SECONDS = 180.0

_registry = None


def _create_pooled_client(**kwargs):
    """HTTPX client with connection pooling and keepalive.

    Accepts **kwargs because the MCP session manager passes headers
    and other parameters to the factory at session creation time.
    We pop conflicting keys to avoid duplicate keyword arguments.
    """
    kwargs.pop("timeout", None)
    kwargs.pop("limits", None)
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0),
        **kwargs,
    )


def get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry(
            project_id=GCP_PROJECT_ID, location=AGENT_REGISTRY_LOCATION
        )
    return _registry


def _harden_toolset(toolset):
    """Apply connection hardening to an McpToolset from Agent Registry."""
    if hasattr(toolset, '_connection_params'):
        cp = toolset._connection_params
        if hasattr(cp, 'timeout'):
            cp.timeout = MCP_TIMEOUT_SECONDS
        if hasattr(cp, 'sse_read_timeout'):
            cp.sse_read_timeout = MCP_READ_TIMEOUT_SECONDS
        if hasattr(cp, 'terminate_on_close'):
            cp.terminate_on_close = False
        if hasattr(cp, 'httpx_client_factory'):
            cp.httpx_client_factory = _create_pooled_client
    return toolset


def get_mcp_tools(server_name: str):
    try:
        toolset = get_registry().get_mcp_toolset(server_name)
        return _harden_toolset(toolset)
    except RuntimeError:
        url = MCP_SERVER_URLS.get(server_name)
        if not url:
            raise
        log.info("Agent Registry unavailable for %s — using direct URL %s", server_name, url)
        return McpToolset(connection_params=StreamableHTTPConnectionParams(
            url=url,
            timeout=MCP_TIMEOUT_SECONDS,
            sse_read_timeout=MCP_READ_TIMEOUT_SECONDS,
            terminate_on_close=False,
            httpx_client_factory=_create_pooled_client,
        ))
