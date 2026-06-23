"""Deploy/update agents on the Gemini Enterprise Agent Platform (GEAP).

Two deployment paths:
  - Legacy (cloudpickle): deploy_agent() / update_agent()
  - Source-based: deploy_agent_from_source() / update_agent_from_source()

Source-based deployment uploads a self-contained build package and lets GEAP
build the container from source — no cloudpickle serialization, no module
path issues.
"""

import os
import re
import shutil
from pathlib import Path

import vertexai

from .config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

# --- Legacy pickle-based requirements ---

REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines,evaluation]>=1.154.0",
    "google-genai>=1.66.0",
    "google-auth>=2.52.0",
    "google-adk[a2a,agent-identity,eval,mcp]>=2.2.0",
    "anthropic[vertex]>=0.49.0",
    "litellm>=1.83.14",
    "python-dotenv>=1.0.0",
    "pydantic>=2.12.5",
]

# --- Source-based deployment constants ---

_SOURCE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines]>=1.154.0",
    "google-genai>=1.66.0",
    "google-auth>=2.52.0",
    "google-adk[a2a,agent-identity,eval,mcp]>=2.2.0",
    "anthropic[vertex]>=0.49.0",
    "litellm>=1.83.14",
    "python-dotenv>=1.0.0",
    "pydantic>=2.12.5",
    "httpx>=0.28.0",
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
            "properties": {
                "session": {"additionalProperties": True, "type": "object"}
            },
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
    # 1. MCP tool warm-up
    mcp_ok, mcp_fail = 0, 0
    for tool in root_agent.tools:
        if hasattr(tool, "get_tools"):
            try:
                tools = await asyncio.wait_for(tool.get_tools(), timeout=30.0)
                tool_names = [t.name for t in tools] if tools else []
                _log.info("[GEAP startup] MCP OK: %s -> %d tools %s",
                          type(tool).__name__, len(tool_names), tool_names[:3])
                mcp_ok += 1
            except Exception as exc:
                _log.error("[GEAP startup] MCP FAILED: %s -> %s", type(tool).__name__, exc)
                mcp_fail += 1
    _log.info("[GEAP startup] MCP summary: %d OK, %d failed", mcp_ok, mcp_fail)
    if mcp_ok == 0 and mcp_fail > 0:
        _log.error("[GEAP startup] FATAL: no MCP tools connected — agent cannot use tools")

    # 2. Model ping — send a trivial request to verify the model endpoint works
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
        raise RuntimeError("Model %s is not reachable: %s" % (MODEL, exc)) from exc

try:
    asyncio.run(_startup_checks())
except RuntimeError as exc:
    if "cannot be called from a running event loop" not in str(exc):
        raise
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
    return McpToolset(connection_params=StreamableHTTPConnectionParams(
        url=url,
        timeout=60.0,
        sse_read_timeout=180.0,
        terminate_on_close=False,
        httpx_client_factory=_create_authed_client,
    ))
'''


def _get_client():
    return vertexai.Client(project=GCP_PROJECT_ID, location=GCP_REGION)


def deploy_agent(
    agent,
    display_name: str | None = None,
    env_vars: dict | None = None,
    extra_packages: list[str] | None = None,
) -> str:
    """Deploy a new agent to GEAP. Returns the resource name.

    Args:
        agent: The ADK agent object to deploy.
        display_name: Display name for the agent in GEAP.
        env_vars: Additional environment variables.
        extra_packages: Local directories to upload alongside the agent.
            Required when the agent's tool functions are defined in local
            modules that aren't pip-installable (e.g., ``["agents/example_agent"]``).
    """
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    config = {
        "staging_bucket": f"gs://{GCP_STAGING_BUCKET}",
        "requirements": REQUIREMENTS,
        "display_name": display_name or agent.name,
        "labels": {"solution": "promp-wrangler"},
        "env_vars": {
            "GCP_PROJECT_ID": GCP_PROJECT_ID,
            "GCP_REGION": GCP_REGION,
            "GOOGLE_CLOUD_LOCATION": "global",
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
            **(env_vars or {}),
        },
    }
    if extra_packages:
        config["extra_packages"] = extra_packages

    print(f"  Deploying {agent.name}...")
    remote = _get_client().agent_engines.create(agent=agent, config=config)
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    engine_id = resource_name.split("/")[-1]
    print(f"  Deployed: {engine_id}")
    return engine_id


def update_agent(
    agent,
    engine_id: str,
    display_name: str | None = None,
    env_vars: dict | None = None,
    extra_packages: list[str] | None = None,
) -> str:
    """Update an existing agent on GEAP. Returns the resource name."""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    if not engine_id.startswith("projects/"):
        engine_id = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"

    config = {
        "staging_bucket": f"gs://{GCP_STAGING_BUCKET}",
        "requirements": REQUIREMENTS,
        "display_name": display_name or agent.name,
        "labels": {"solution": "promp-wrangler"},
        "env_vars": {
            "GCP_PROJECT_ID": GCP_PROJECT_ID,
            "GCP_REGION": GCP_REGION,
            "GOOGLE_CLOUD_LOCATION": "global",
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
            **(env_vars or {}),
        },
    }
    if extra_packages:
        config["extra_packages"] = extra_packages

    print(f"  Updating {agent.name} ({engine_id.split('/')[-1]})...")
    remote = _get_client().agent_engines.update(
        name=engine_id, agent=agent, config=config,
    )
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    print(f"  Updated: {resource_name.split('/')[-1]}")
    return resource_name.split("/")[-1]


# --- Source-based deployment (no cloudpickle) ---


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
            import logging
            logging.getLogger(__name__).warning(
                f"config.py not found within 3 levels of {agent_module} — "
                f"build package may be incomplete"
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
            prompts_src, build_path / "prompts",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    # Generate a GEAP-specific registry.py that uses Agent Registry with
    # ADC auth instead of direct Cloud Run URLs.  The GEAP container's
    # service account provides ADC automatically.
    (build_path / "registry.py").write_text(_REGISTRY_PY_TEMPLATE)

    # Patch config.py for GEAP compatibility:
    # 1. Rewrite hard os.environ["KEY"] to os.environ.get() for MCP vars
    # 2. Patch resolve_model() so Claude models use full resource name with
    #    locations/global — GEAP sets GOOGLE_CLOUD_LOCATION=us-central1
    #    (restricted env var) but Claude requires global.
    config_copy = build_path / "config.py"
    if config_copy.exists():
        text = config_copy.read_text()
        text = re.sub(
            r'= os\.environ\["(SEARCH_MCP_SERVER|BOOKING_MCP_SERVER|EXPENSE_MCP_SERVER)"\]',
            r'= os.environ.get("\1", "")',
            text,
        )
        # Patch resolve_model: Claude models need full resource name with
        # locations/global. Gemini 3.x models use the GOOGLE_CLOUD_LOCATION
        # env var set in app.py (no resource name rewrite needed).
        text = text.replace(
            'return Claude(model=model_str)',
            '_proj = os.environ.get("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))\n'
            '        return Claude(model=f"projects/{_proj}/locations/global/publishers/anthropic/models/{model_str}")',
        )
        config_copy.write_text(text)

    # Copy .env as fallback for MCP server env vars — config.py's
    # load_dotenv() will read it if env_vars from the deployment config
    # are missing or incomplete.
    env_file = agent_parent / ".env"
    if env_file.exists():
        shutil.copy2(env_file, build_path / ".env")

    # Write instruction file (replaced during redeploy)
    (build_path / "instruction.txt").write_text(instruction)

    # Write requirements
    (build_path / "requirements.txt").write_text(
        "\n".join(_SOURCE_REQUIREMENTS) + "\n"
    )

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


def _build_source_config(
    build_dir: str,
    display_name: str,
    env_vars: dict | None = None,
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
    return {
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
            **(env_vars or {}),
        },
    }


def deploy_agent_from_source(
    agent_module: str,
    model: str,
    instruction: str,
    display_name: str | None = None,
    env_vars: dict | None = None,
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
    for attempt in range(3):
        try:
            build_dir = build_source_package(agent_module, instruction, model)
            config = _build_source_config(
                build_dir,
                display_name=display_name or "gepa-agent",
                env_vars=env_vars,
            )
            remote = _get_client().agent_engines.create(config=config)
            break
        except Exception as e:
            last_err = e
            shutil.rmtree(build_dir, ignore_errors=True)
            if attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"  Deploy attempt {attempt + 1} failed (GEAP transient error), retrying in {wait}s...")
                _time.sleep(wait)
            else:
                import logging
                logging.getLogger(__name__).error(
                    f"Deploy failed after 3 attempts for {display_name or 'gepa-agent'}: {e}"
                )
                raise
    else:
        raise last_err  # unreachable, but satisfies type checker

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
    for attempt in range(3):
        try:
            build_dir = build_source_package(agent_module, instruction, model)
            config = _build_source_config(
                build_dir,
                display_name=display_name or "gepa-agent",
                env_vars=env_vars,
            )
            remote = _get_client().agent_engines.update(name=engine_id, config=config)
            break
        except Exception as e:
            last_err = e
            shutil.rmtree(build_dir, ignore_errors=True)
            if attempt < 2:
                wait = 30 * (attempt + 1)
                print(f"  Update attempt {attempt + 1} failed (GEAP transient error), retrying in {wait}s...")
                _time.sleep(wait)
            else:
                import logging
                logging.getLogger(__name__).error(
                    f"Update failed after 3 attempts for {engine_id.split('/')[-1]}: {e}"
                )
                raise
    else:
        raise last_err

    shutil.rmtree(build_dir, ignore_errors=True)
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    print(f"  Updated: {resource_name.split('/')[-1]}")
    return resource_name.split("/")[-1]
