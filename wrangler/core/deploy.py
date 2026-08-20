"""Deploy/update agents on the Gemini Enterprise Agent Platform (GEAP).

Deployment uploads a self-contained build package via ``source_packages`` and
lets GEAP build the container from source. A cloudpickle-based path used to
live here too; it was deleted because cloudpickle captures module references
(registry.py, config.py, prompts/) that do not exist on the GEAP server.
"""

import os
import re
import shutil
from pathlib import Path

import vertexai

from .config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

# --- Source-based deployment constants ---

_SOURCE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]>=1.163.0",
    "google-genai>=2",
    "google-auth>=2.52.0",
    # Pinned exactly, and must match the floor in pyproject.toml -- the
    # container and the local env otherwise run different ADK versions and
    # a bug reproduces in only one of them.
    "google-adk[a2a,agent-identity,eval,mcp]==2.7.1",
    "anthropic[vertex]>=0.49.0",
    "litellm>=1.83.14",
    "python-dotenv>=1.0.0",
    "pydantic>=2.12.5",
    # httpx is deliberately not pinned here. google-adk and anthropic both
    # depend on it, and pinning a third bound on top of theirs is how the GEAP
    # image build ends up unresolvable. Let their constraints decide.
    "opentelemetry-instrumentation-google-genai",
    "opentelemetry-instrumentation-grpc",
    "opentelemetry-instrumentation-httpx",
]

# Standard ADK class_methods for Agent Engine — copied from
# google.adk.cli.cli_deploy._AGENT_ENGINE_CLASS_METHODS (ADK 2.2.0).
_ADK_CLASS_METHODS = [
    {
        "name": "get_session",
        "description": "Deprecated. Use async_get_session instead.\n\n        Get a session for the given user.\n        ",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user_id", "session_id"],
            "type": "object",
        },
        "api_mode": "",
    },
    {
        "name": "list_sessions",
        "description": "Deprecated. Use async_list_sessions instead.\n\n        List sessions for the given user.\n        ",
        "parameters": {
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "type": "object",
        },
        "api_mode": "",
    },
    {
        "name": "create_session",
        "description": "Deprecated. Use async_create_session instead.\n\n        Creates a new session.\n        ",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string", "nullable": True},
                "state": {"type": "object", "nullable": True},
            },
            "required": ["user_id"],
            "type": "object",
        },
        "api_mode": "",
    },
    {
        "name": "delete_session",
        "description": "Deprecated. Use async_delete_session instead.\n\n        Deletes a session for the given user.\n        ",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user_id", "session_id"],
            "type": "object",
        },
        "api_mode": "",
    },
    {
        "name": "async_get_session",
        "description": "Get a session for the given user.",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user_id", "session_id"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "async_list_sessions",
        "description": "List sessions for the given user.",
        "parameters": {
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "async_create_session",
        "description": "Creates a new session.",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string", "nullable": True},
                "state": {"type": "object", "nullable": True},
            },
            "required": ["user_id"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "async_delete_session",
        "description": "Deletes a session for the given user.",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user_id", "session_id"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "async_add_session_to_memory",
        "description": "Generates memories.",
        "parameters": {
            "properties": {"session": {"additionalProperties": True, "type": "object"}},
            "required": ["session"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "async_search_memory",
        "description": "Searches memories for the given user.",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["user_id", "query"],
            "type": "object",
        },
        "api_mode": "async",
    },
    {
        "name": "stream_query",
        "description": "Deprecated. Use async_stream_query instead.\n\n        Streams responses from the ADK application in response to a message.",
        "parameters": {
            "properties": {
                "message": {
                    "anyOf": [
                        {"type": "string"},
                        {"additionalProperties": True, "type": "object"},
                    ]
                },
                "user_id": {"type": "string"},
                "session_id": {"type": "string", "nullable": True},
                "run_config": {"type": "object", "nullable": True},
            },
            "required": ["message", "user_id"],
            "type": "object",
        },
        "api_mode": "stream",
    },
    {
        "name": "async_stream_query",
        "description": "Streams responses asynchronously from the ADK application.",
        "parameters": {
            "properties": {
                "message": {
                    "anyOf": [
                        {"type": "string"},
                        {"additionalProperties": True, "type": "object"},
                    ]
                },
                "user_id": {"type": "string"},
                "session_id": {"type": "string", "nullable": True},
                "run_config": {"type": "object", "nullable": True},
            },
            "required": ["message", "user_id"],
            "type": "object",
        },
        "api_mode": "async_stream",
    },
    {
        "name": "streaming_agent_run_with_events",
        "description": "Streams responses asynchronously from the ADK application (AgentSpace).",
        "parameters": {
            "properties": {"request_json": {"type": "string"}},
            "required": ["request_json"],
            "type": "object",
        },
        "api_mode": "async_stream",
    },
]

_APP_PY_TEMPLATE = '''\
"""Auto-generated GEAP entrypoint."""
import os
import asyncio
import logging as _log
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from vertexai.agent_engines import AdkApp

from .config import resolve_model, SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER
from .registry import get_mcp_tools

_log.basicConfig(level=_log.INFO)
_here = Path(__file__).parent
INSTRUCTION = (_here / "instruction.txt").read_text().strip()
MODEL = os.environ.get("AGENT_MODEL", "{model}")

_log.info("[GEAP startup] model=%s, location=%s, project=%s",
          MODEL, os.environ.get("GOOGLE_CLOUD_LOCATION", "unset"),
          os.environ.get("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", "unset")))
_log.info("[GEAP startup] instruction=%d chars, MCP servers: search=%s, booking=%s, expense=%s",
          len(INSTRUCTION), bool(SEARCH_MCP_SERVER), bool(BOOKING_MCP_SERVER), bool(EXPENSE_MCP_SERVER))

# -- Validate model connectivity before serving --
try:
    resolved = resolve_model(MODEL)
    _log.info("[GEAP startup] model resolved: %s (type=%s)", resolved, type(resolved).__name__)
except Exception as exc:
    _log.error("[GEAP startup] FATAL: resolve_model(%s) failed: %s", MODEL, exc)
    raise

root_agent = LlmAgent(
    model=resolved,
    name="{agent_name}",
    description="Corporate travel and expense assistant with access to flight, hotel, and expense management tools.",
    instruction=INSTRUCTION,
    tools=[
        get_mcp_tools(SEARCH_MCP_SERVER),
        get_mcp_tools(BOOKING_MCP_SERVER),
        get_mcp_tools(EXPENSE_MCP_SERVER),
        PreloadMemoryTool(),
    ],
)

app = AdkApp(agent=root_agent, enable_tracing=True)

# -- Startup checks: MCP tools + model ping --
async def _startup_checks():
    # 1. MCP reachability.
    #
    # Deliberately builds THROWAWAY toolsets rather than probing
    # root_agent.tools. McpToolset caches its session on the event loop that
    # first opened it, and these checks run on their own short-lived loop. A
    # probe against the serving agent's toolsets therefore opens sessions,
    # then closes the loop underneath them, leaving the *serving* path holding
    # sessions bound to a dead loop -- visible in the logs as
    #
    #   Cleaning up session (disconnected or different loop): session_no_headers
    #   Error cleaning up session ...: original event loop is closed
    #
    # The agent then answers ordinary prompts perfectly and dies the moment the
    # model emits a function_call: the tool await never reaches the network, no
    # HTTP request is logged, no exception surfaces, and the caller gets 200
    # with an empty body and zero events.
    #
    # A health check must not share mutable connection state with the thing it
    # is checking.
    mcp_ok, mcp_fail = 0, 0
    for server in (SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER):
        if not server:
            continue
        probe = None
        try:
            probe = get_mcp_tools(server)
            tools = await asyncio.wait_for(probe.get_tools(), timeout=30.0)
            tool_names = [t.name for t in tools] if tools else []
            _log.info("[GEAP startup] MCP OK: %s -> %d tools %s",
                      server, len(tool_names), tool_names[:3])
            mcp_ok += 1
        except Exception as exc:
            _log.error("[GEAP startup] MCP FAILED: %s -> %s", server, exc)
            mcp_fail += 1
        finally:
            # Close on the same loop that opened it, so nothing outlives this
            # coroutine. Failure to close is not itself a health signal.
            if probe is not None and hasattr(probe, "close"):
                try:
                    await probe.close()
                except Exception as exc:
                    _log.debug("[GEAP startup] probe close failed for %s: %s", server, exc)
    _log.info("[GEAP startup] MCP summary: %d OK, %d failed", mcp_ok, mcp_fail)
    if mcp_ok == 0 and mcp_fail > 0:
        _log.error("[GEAP startup] FATAL: no MCP tools connected — agent cannot use tools")

    # 2. Model ping — send a trivial request to verify the model endpoint works.
    #
    # Gemini only. The genai client resolves a bare model id under
    # publishers/google/, so pinging a Claude id here asks for
    # publishers/google/models/claude-sonnet-4-6 and always 404s -- a false
    # alarm that says nothing about the agent, which reaches Claude through
    # resolve_model()'s publishers/anthropic/ path. Skip rather than lie.
    if not MODEL.startswith("gemini"):
        _log.info("[GEAP startup] model ping skipped for non-Gemini model %s "
                  "(resolved via %s)", MODEL, type(resolved).__name__)
        return
    try:
        from google import genai
        client = genai.Client(vertexai=True,
                              project=os.environ.get("GCP_PROJECT_ID", ""),
                              location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
        response = await client.aio.models.generate_content(
            model=MODEL, contents="Say OK")
        _log.info("[GEAP startup] model ping OK: %s",
                  response.text[:50] if response.text else "(empty)")
    except Exception as exc:
        _log.error("[GEAP startup] FATAL: model ping failed for %s: %s", MODEL, exc)

# Run the checks on a dedicated thread with its own event loop.
#
# The previous form was a bare `asyncio.run(_startup_checks())` guarded by an
# except that swallowed "cannot be called from a running event loop". Under
# GEAP that guard always fired: the module is imported from inside a running
# loop, asyncio.run() raised immediately, and the coroutine was never awaited
# (visible only as a RuntimeWarning). So the checks NEVER ran, and an agent
# that could not reach its MCP servers started up looking perfectly healthy.
#
# A daemon thread runs regardless of whether a loop is already active. Failures
# are logged, never raised — a broken MCP server should be loud in the logs, not
# a crash-loop on startup.
def _run_startup_checks_in_background():
    import threading

    def _target():
        try:
            asyncio.run(_startup_checks())
        except Exception as exc:
            _log.error("[GEAP startup] startup checks failed: %s", exc, exc_info=True)

    threading.Thread(target=_target, name="geap-startup-checks", daemon=True).start()


_run_startup_checks_in_background()
'''

_REGISTRY_PY_TEMPLATE = '''\
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
'''


def _get_client():
    return vertexai.Client(project=GCP_PROJECT_ID, location=GCP_REGION)


def build_source_package(
    agent_module: str,
    instruction: str,
    model: str,
    build_dir: str | None = None,
) -> str:
    """Assemble a self-contained source package for GEAP deployment.

    Creates a directory containing app.py (AdkApp entrypoint), config.py,
    registry.py, prompts/, instruction.txt, and requirements.txt — everything
    GEAP needs to build and run the agent from source.

    The build_dir defaults to a ``_geap_build_pkg`` subdirectory next to the
    agent module.  The Vertex AI SDK requires source_packages to be under the
    project directory — ``/tmp`` is rejected.

    Args:
        agent_module: Path to the agent module (e.g. "examples/multi_model_agents/agents/sonnet_agent").
        instruction: The system prompt text.
        model: Model string (e.g. "claude-sonnet-4-6").
        build_dir: Where to assemble the package.  Defaults to a sibling of agent_module.

    Returns:
        The build_dir path.
    """
    agent_path = Path(agent_module)

    # Accept a dotted module path ("examples.multi_model_agents.agents.x") as
    # well as the manifest's slash form.  Path() happily swallows the dotted
    # form -- its .parent is "." -- so the walk below then fails to find
    # config.py and used to warn and carry on, producing a package with no
    # config.py that GEAP accepts and then fails to start twenty minutes later.
    if not agent_path.exists() and not agent_path.with_suffix(".py").exists():
        dotted = Path(agent_module.replace(".", "/"))
        if dotted.exists() or dotted.with_suffix(".py").exists():
            agent_path = dotted

    agent_parent = agent_path.parent

    # config.py may live alongside the agents/ directory, not inside it.
    # Walk up until we find it.
    if not (agent_parent / "config.py").exists():
        found = False
        for _ in range(3):
            candidate = agent_parent.parent
            if (candidate / "config.py").exists():
                agent_parent = candidate
                found = True
                break
            agent_parent = candidate
        if not found:
            raise FileNotFoundError(
                f"config.py not found within 3 levels of {agent_module!r}. "
                f"agent_module must be a path relative to the project root "
                f"(e.g. 'examples/multi_model_agents/agents/sonnet_agent'). "
                f"Deploying without it produces an agent that cannot start."
            )

    if build_dir is None:
        # The SDK's tar.add() uses relative paths from CWD.  The GEAP server
        # extracts the tarball into /code/ and adds /code/ to sys.path.  So
        # the build package must be a relative path from CWD (project root)
        # to appear at /code/_geap_build_pkg/ and be importable.
        build_dir = str(Path.cwd() / "_geap_build_pkg")

    build_path = Path(build_dir)
    if build_path.exists():
        shutil.rmtree(build_path)
    build_path.mkdir(parents=True)

    # Copy agent dependency files into the build package
    config_src = agent_parent / "config.py"
    if config_src.exists():
        shutil.copy2(config_src, build_path / "config.py")

    prompts_src = agent_parent / "prompts"
    if prompts_src.is_dir():
        shutil.copytree(
            prompts_src,
            build_path / "prompts",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    # Generate a GEAP-specific registry.py that uses Agent Registry with
    # ADC auth instead of direct Cloud Run URLs.  The GEAP container's
    # service account provides ADC automatically.
    (build_path / "registry.py").write_text(_REGISTRY_PY_TEMPLATE)

    # Patch config.py for GEAP compatibility: rewrite hard os.environ["KEY"]
    # to os.environ.get() for the MCP vars. A third-party agent config that
    # subscripts an env var the GEAP server does not set crashes the container
    # on import. This repo's config already uses .get(), so it is a no-op here.
    #
    # There used to be a second rewrite that re-pointed Claude at a global
    # resource path by string-replacing `return Claude(model=model_str)`. It is
    # gone, and should not come back. config.py now pins the location per model
    # itself (model_location()), and the replacement was indentation-blind: once
    # that return moved under an `if not project:` guard, the rewrite hoisted
    # the guard's body and left the substituted `return` outside it, so every
    # deploy with a project set died at import with
    #
    #   UnboundLocalError: cannot access local variable '_proj'
    #
    # A textual .replace() on Python source is a rewrite that cannot see scope.
    # Fix the source, do not patch it on the way past.
    config_copy = build_path / "config.py"
    if config_copy.exists():
        text = config_copy.read_text()
        text = re.sub(
            r'= os\.environ\["(SEARCH_MCP_SERVER|BOOKING_MCP_SERVER|EXPENSE_MCP_SERVER)"\]',
            r'= os.environ.get("\1", "")',
            text,
        )
        config_copy.write_text(text)

    # Deliberately NOT copying `agent_parent / ".env"`. It used to be shipped as
    # a "fallback" for the MCP env vars, but it cannot work as one: the copy
    # lands at /code/_geap_build_pkg/.env while config.py's load_dotenv()
    # searches from the process CWD (/code) and never finds it. See
    # `mcp_env_from_environ` below, which is how those vars actually travel.
    # So the copy bought nothing and uploaded a secrets file to a server that
    # never reads it. tests/test_deploy.py::test_no_env_file_shipped pins this.

    # Write instruction file (replaced during redeploy)
    (build_path / "instruction.txt").write_text(instruction)

    # Write requirements
    (build_path / "requirements.txt").write_text("\n".join(_SOURCE_REQUIREMENTS) + "\n")

    # Write __init__.py
    (build_path / "__init__.py").write_text("")

    # Write app.py from template
    agent_name = agent_path.stem.replace("_agent", "") + "_agent"
    app_content = _APP_PY_TEMPLATE.format(
        model=model,
        agent_name=agent_name,
    )
    (build_path / "app.py").write_text(app_content)

    return str(build_path)


_MCP_ENV_PREFIXES = ("SEARCH_MCP", "BOOKING_MCP", "EXPENSE_MCP")


def mcp_env_from_environ() -> dict[str, str]:
    """Collect the MCP server/URL env vars the deployed agent needs.

    The generated ``registry.py`` in the build package reads ``*_MCP_SERVER``
    and ``*_MCP_URL`` from the *GEAP server's* environment, so they must travel
    in the deployment config's ``env_vars``. They cannot arrive any other way:
    the ``.env`` copied into the build package is at
    ``/code/_geap_build_pkg/.env``, while ``config.py``'s ``load_dotenv()``
    searches from the process CWD (``/code``) and never finds it.

    An agent deployed without these does not fail loudly — it comes up with an
    empty toolset and *role-plays* tool use, emitting literal ``<tool_call>``
    text in its response. See docs/notes/repo-traps.md.
    """
    return {k: v for k, v in os.environ.items() if k.startswith(_MCP_ENV_PREFIXES) and v}


def _scaling_from_environ() -> dict:
    """Read GEAP instance-scaling settings from the environment.

    `GEAP_MIN_INSTANCES` is the one that matters. GEAP will route a request to
    a worker that has not finished booting, and that request comes back HTTP
    200 with zero events instead of an error -- startup here is ~8s of MCP
    handshakes. Raising the floor keeps warm workers around so fewer requests
    land on a cold one.

    It is a mitigation, not a fix: GEAP also spawns cold workers when scaling
    *up*, which was observed mid-run under steady load, so callers still have
    to treat an empty stream as retryable. See `wrangler/tools/traffic.py`.

    Unset means "leave it to GEAP", so this stays a no-op until asked for.
    """
    scaling = {}
    for key, field in (
        ("GEAP_MIN_INSTANCES", "min_instances"),
        ("GEAP_MAX_INSTANCES", "max_instances"),
        ("GEAP_CONTAINER_CONCURRENCY", "container_concurrency"),
    ):
        raw = os.environ.get(key, "").strip()
        if raw:
            scaling[field] = int(raw)
    return scaling


def _build_source_config(
    build_dir: str,
    display_name: str,
    env_vars: dict | None = None,
    min_instances: int | None = None,
    max_instances: int | None = None,
) -> dict:
    """Build the config dict for source-based deployment."""
    pkg_name = Path(build_dir).name
    # source_packages must use a path relative to CWD — the SDK's
    # tar.add() preserves the path structure in the archive.  An absolute
    # path would create a broken archive on the GEAP server.
    try:
        rel_path = str(Path(build_dir).relative_to(Path.cwd()))
    except ValueError:
        rel_path = build_dir

    # Explicit arguments win over the environment; both may be absent.
    scaling = _scaling_from_environ()
    if min_instances is not None:
        scaling["min_instances"] = min_instances
    if max_instances is not None:
        scaling["max_instances"] = max_instances

    return {
        **scaling,
        "staging_bucket": f"gs://{GCP_STAGING_BUCKET}",
        "source_packages": [rel_path],
        "requirements_file": f"{rel_path}/requirements.txt",
        "entrypoint_module": f"{pkg_name}.app",
        "entrypoint_object": "app",
        "class_methods": _ADK_CLASS_METHODS,
        "agent_framework": "google-adk",
        "display_name": display_name,
        "labels": {"solution": "promp-wrangler"},
        "env_vars": {
            "GCP_PROJECT_ID": GCP_PROJECT_ID,
            "GCP_REGION": GCP_REGION,
            "GOOGLE_CLOUD_LOCATION": "global",
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
            # MCP vars are a *default* layer: an explicit env_vars from the
            # caller (the pipeline passes its localhost overrides here) wins.
            **mcp_env_from_environ(),
            **(env_vars or {}),
        },
    }


def deploy_agent_from_source(
    agent_module: str,
    model: str,
    instruction: str,
    display_name: str | None = None,
    env_vars: dict | None = None,
    min_instances: int | None = None,
    max_instances: int | None = None,
) -> str:
    """Deploy a new agent to GEAP using source-based deployment. Returns engine_id.

    Assembles a build package from the agent's source files and deploys via
    source_packages — no cloudpickle serialization.
    """
    import time as _time

    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    print(f"  Deploying from source ({Path(agent_module).stem})...")
    last_err = None
    build_dir = None
    for attempt in range(3):
        try:
            build_dir = build_source_package(agent_module, instruction, model)
            config = _build_source_config(
                build_dir,
                display_name=display_name or "gepa-agent",
                env_vars=env_vars,
                min_instances=min_instances,
                max_instances=max_instances,
            )
            remote = _get_client().agent_engines.create(config=config)
            break
        except Exception as e:
            last_err = e
            if build_dir:
                shutil.rmtree(build_dir, ignore_errors=True)
            if attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"  Deploy attempt {attempt + 1} failed, retrying in {wait}s: {e}")
                _time.sleep(wait)
            else:
                import logging

                logging.getLogger(__name__).exception(
                    f"Deploy failed after 3 attempts for {display_name or 'gepa-agent'}"
                )
                raise
    else:
        raise last_err  # unreachable, but satisfies type checker

    if build_dir:
        shutil.rmtree(build_dir, ignore_errors=True)
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    engine_id = resource_name.split("/")[-1]
    print(f"  Deployed: {engine_id}")
    return engine_id


def update_agent_from_source(
    engine_id: str,
    agent_module: str,
    model: str,
    instruction: str,
    display_name: str | None = None,
    env_vars: dict | None = None,
    min_instances: int | None = None,
    max_instances: int | None = None,
) -> str:
    """Update an existing agent on GEAP using source-based deployment. Returns engine_id."""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    import time as _time

    if not engine_id.startswith("projects/"):
        engine_id = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"

    print(f"  Updating from source ({engine_id.split('/')[-1]})...")
    last_err = None
    build_dir = None
    for attempt in range(3):
        try:
            build_dir = build_source_package(agent_module, instruction, model)
            config = _build_source_config(
                build_dir,
                display_name=display_name or "gepa-agent",
                env_vars=env_vars,
                min_instances=min_instances,
                max_instances=max_instances,
            )
            remote = _get_client().agent_engines.update(name=engine_id, config=config)
            break
        except Exception as e:
            last_err = e
            if build_dir:
                shutil.rmtree(build_dir, ignore_errors=True)
            if attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"  Update attempt {attempt + 1} failed, retrying in {wait}s: {e}")
                _time.sleep(wait)
            else:
                import logging

                logging.getLogger(__name__).exception(
                    f"Update failed after 3 attempts for {engine_id.split('/')[-1]}"
                )
                raise
    else:
        raise last_err

    if build_dir:
        shutil.rmtree(build_dir, ignore_errors=True)
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    print(f"  Updated: {resource_name.split('/')[-1]}")
    return resource_name.split("/")[-1]
