"""MCP tool discovery — direct Cloud Run URLs with optional Agent Registry fallback.

Uses direct Cloud Run URLs by default (faster, no registry overhead).
Falls back to Agent Registry lookup when direct URLs aren't configured.
"""

import logging
import os

import httpx
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from config import GCP_PROJECT_ID, AGENT_REGISTRY_LOCATION, MCP_SERVER_URLS

log = logging.getLogger(__name__)

MCP_TIMEOUT_SECONDS = 120.0
MCP_READ_TIMEOUT_SECONDS = 180.0


def _create_pooled_client(**kwargs):
    """HTTPX client with connection pooling and keepalive."""
    kwargs.pop("timeout", None)
    kwargs.pop("limits", None)
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0),
        **kwargs,
    )


def get_mcp_tools(server_name: str):
    url = MCP_SERVER_URLS.get(server_name)
    if url:
        log.info("Using direct URL for %s: %s", server_name, url)
        return McpToolset(connection_params=StreamableHTTPConnectionParams(
            url=url,
            timeout=MCP_TIMEOUT_SECONDS,
            sse_read_timeout=MCP_READ_TIMEOUT_SECONDS,
            terminate_on_close=False,
            httpx_client_factory=_create_pooled_client,
        ))

    log.info("No direct URL for %s — trying Agent Registry", server_name)
    from google.adk.integrations.agent_registry import AgentRegistry
    registry = AgentRegistry(
        project_id=GCP_PROJECT_ID, location=AGENT_REGISTRY_LOCATION
    )
    return registry.get_mcp_toolset(server_name)
