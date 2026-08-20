"""MCP tool discovery — direct Cloud Run URLs with optional Agent Registry fallback.

Uses direct Cloud Run URLs by default (faster, no registry overhead).
Falls back to Agent Registry lookup when direct URLs aren't configured.
"""

import logging

import httpx
from config import AGENT_REGISTRY_LOCATION, GCP_PROJECT_ID, MCP_SERVER_URLS
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

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
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=url,
                timeout=MCP_TIMEOUT_SECONDS,
                sse_read_timeout=MCP_READ_TIMEOUT_SECONDS,
                terminate_on_close=False,
                httpx_client_factory=_create_pooled_client,
            )
        )

    log.info("No direct URL for %s — trying Agent Registry", server_name)
    from google.adk.integrations.agent_registry import AgentRegistry

    registry = AgentRegistry(project_id=GCP_PROJECT_ID, location=AGENT_REGISTRY_LOCATION)
    return registry.get_mcp_toolset(_resolve_registry_name(registry, server_name))


def _resolve_registry_name(registry, server_name: str) -> str:
    """Map a `*_MCP_SERVER` value to a real Agent Registry resource name.

    The registry's own names look like
    ``projects/P/locations/L/mcpServers/agentregistry-<uuid>`` — an opaque id
    you cannot construct, only look up. The `*_MCP_SERVER` values in `.env` are
    Cloud Run service paths (``.../services/wrangler-search-mcp``), which the
    direct-URL path above uses purely as dict keys. Passing one straight to
    ``get_mcp_toolset`` 404s, so match on displayName instead.
    """
    if "/mcpServers/" in server_name:
        return server_name

    display_name = server_name.rstrip("/").rsplit("/", 1)[-1]
    for server in registry.list_mcp_servers().get("mcpServers", []):
        if server.get("displayName") == display_name:
            return server["name"]

    raise ValueError(
        f"No MCP server named {display_name!r} in the Agent Registry "
        f"({GCP_PROJECT_ID}/{AGENT_REGISTRY_LOCATION}), and no direct URL "
        f"configured for {server_name!r}. Set the matching *_MCP_URL."
    )
