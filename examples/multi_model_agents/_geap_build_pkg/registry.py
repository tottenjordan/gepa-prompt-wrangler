"""MCP tool discovery for GEAP deployment.

Uses direct Cloud Run URLs with GoogleAuth — the GEAP container's service
account provides ADC credentials for Cloud Run invoker auth automatically.
"""
import os

import httpx
import google.auth
from google.auth.transport.requests import Request
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams


class _GoogleAuth(httpx.Auth):
    """Attaches Google ADC bearer token to every request."""

    def __init__(self):
        self.creds, _ = google.auth.default()

    def auth_flow(self, request):
        if not self.creds.valid:
            self.creds.refresh(Request())
        request.headers["Authorization"] = f"Bearer {self.creds.token}"
        yield request


def _create_authed_client(**kwargs):
    kwargs.pop("timeout", None)
    kwargs.pop("limits", None)
    kwargs.pop("auth", None)
    return httpx.AsyncClient(
        auth=_GoogleAuth(),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        timeout=httpx.Timeout(connect=60.0, read=180.0, write=30.0, pool=30.0),
        **kwargs,
    )


_MCP_URLS = {k: v for k, v in {
    os.environ.get("SEARCH_MCP_SERVER", ""): os.environ.get("SEARCH_MCP_URL", ""),
    os.environ.get("BOOKING_MCP_SERVER", ""): os.environ.get("BOOKING_MCP_URL", ""),
    os.environ.get("EXPENSE_MCP_SERVER", ""): os.environ.get("EXPENSE_MCP_URL", ""),
}.items() if k and v}

if not _MCP_URLS:
    import logging as _log
    _log.getLogger(__name__).error(
        "No MCP server URLs configured. Set SEARCH_MCP_SERVER/SEARCH_MCP_URL, "
        "BOOKING_MCP_SERVER/BOOKING_MCP_URL, EXPENSE_MCP_SERVER/EXPENSE_MCP_URL "
        "via env_vars or .env file."
    )
    raise RuntimeError("No MCP server URLs configured — agent cannot use tools.")


def get_mcp_tools(server_name):
    url = _MCP_URLS.get(server_name, "")
    if not url:
        raise ValueError(f"No MCP URL configured for {server_name}")
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            timeout=60.0,
            sse_read_timeout=180.0,
            terminate_on_close=False,
            httpx_client_factory=_create_authed_client,
        ),
        # Serve tools/list from cache rather than re-listing every invocation.
        # A transient session failure otherwise costs that invocation its whole
        # toolset: ADK retries once, then hands the agent zero tools without
        # raising, and the agent answers as if it had none. Staleness is the
        # trade — ADK ignores notifications/tools/list_changed — but these
        # servers have a fixed tool set. Keep in sync with
        # examples/multi_model_agents/registry.py.
        tool_list_cache_ttl_seconds=300.0,
    )
