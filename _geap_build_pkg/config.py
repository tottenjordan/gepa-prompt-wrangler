"""Global configuration — GCP project settings, MCP server URLs, model configs, and eval params."""

import os
import warnings

from dotenv import load_dotenv

warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*")

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
GCP_STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET", f"{GCP_PROJECT_ID}-geap-staging")
AGENT_GATEWAY_PATH = os.environ.get("AGENT_GATEWAY_PATH", "")
AGENT_GATEWAY_EGRESS_PATH = os.environ.get("AGENT_GATEWAY_EGRESS_PATH", "")

SEARCH_MCP_URL = os.environ.get("SEARCH_MCP_URL", "http://localhost:8001/mcp")
BOOKING_MCP_URL = os.environ.get("BOOKING_MCP_URL", "http://localhost:8002/mcp")
EXPENSE_MCP_URL = os.environ.get("EXPENSE_MCP_URL", "http://localhost:8003/mcp")

# Agent Registry — MCP server resource names (global location)
AGENT_REGISTRY_LOCATION = os.environ.get("AGENT_REGISTRY_LOCATION", "us-central1")
# Default to "" rather than subscripting: importing this module must not require
# the MCP env, or tests and any local `import config` fail where deployment works.
SEARCH_MCP_SERVER = os.environ.get("SEARCH_MCP_SERVER", "")
BOOKING_MCP_SERVER = os.environ.get("BOOKING_MCP_SERVER", "")
EXPENSE_MCP_SERVER = os.environ.get("EXPENSE_MCP_SERVER", "")

# Fallback: map Agent Registry server names → Cloud Run URLs
MCP_SERVER_URLS = {
    SEARCH_MCP_SERVER: SEARCH_MCP_URL,
    BOOKING_MCP_SERVER: BOOKING_MCP_URL,
    EXPENSE_MCP_SERVER: EXPENSE_MCP_URL,
}

# The OTel settings that actually reach a deployed agent live in
# `_OTEL_ENV_VARS` in wrangler/core/deploy.py, which is what builds the GEAP
# env_vars. A copy used to sit here too, with nothing reading it — a dict that
# reads like configuration but is not applied is how the un-tuned exporter
# settings would have quietly outlived the fix in docs/notes/silent-failures.md
# #8. Removed rather than synced, because a second copy only drifts again.

AGENT_MODEL = os.environ.get("AGENT_MODEL", "gemini-3.5-flash")


GLOBAL_LOCATION = "global"
_VERTEX_TRUTHY = {"1", "true", "True", "TRUE"}


def is_regional_model(model_str: str) -> bool:
    """True if `model_str` is served from a regional Vertex AI endpoint."""
    return model_str.startswith(("gemini-2", "models/"))


def model_location(model_str: str) -> str:
    """Return the Vertex AI location that serves `model_str`.

    Gemini 2.x → regional (GCP_REGION). Gemini 3.x and every Anthropic
    (Claude) model → "global"; they are not servable from a region, and
    asking for one fails with "Publisher Model ... is not servable in region
    us-central1".

    Kept in sync with wrangler/core/models.py:model_location — see that
    docstring for why this cannot be driven by GOOGLE_CLOUD_LOCATION.

    Reads the environment at *call* time, not the module constant bound at
    import. The pipeline components set GCP_REGION inside the component body,
    after the tarball is extracted and possibly after this module was already
    imported; binding at import made the deployed copy route on a stale region
    while wrangler's copy — which always read at call time — did not.
    """
    if is_regional_model(model_str):
        return os.environ.get("GCP_REGION", "us-central1")
    return GLOBAL_LOCATION


def resolve_model(model_str: str):
    """Resolve model string to an ADK-compatible model.

    Gemini 2.x passes through as a plain string. Gemini 3.x and Claude get
    their location from `model_location` pinned into the model object, so a
    stale or platform-imposed GOOGLE_CLOUD_LOCATION cannot break them.
    """
    if is_regional_model(model_str):
        return model_str

    location = model_location(model_str)
    # Env at call time, not the import-time constant -- same reason as
    # model_location above. A project set after import lands in the Claude
    # resource path here, and getting it wrong routes to the wrong project.
    project = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    if model_str.startswith("claude"):
        from google.adk.models.anthropic_llm import Claude

        if not project:
            return Claude(model=model_str)
        # ADK's Claude reads project + location out of a full resource path
        # and ignores GOOGLE_CLOUD_LOCATION when one is given.
        return Claude(
            model=f"projects/{project}/locations/{location}/publishers/anthropic/models/{model_str}"
        )

    from google.adk.models.google_llm import Gemini

    if not project or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "") not in _VERTEX_TRUTHY:
        return Gemini(model=model_str)
    # client_kwargs is forwarded to google.genai.Client, which picks its
    # endpoint host from `location`.
    return Gemini(
        model=model_str,
        client_kwargs={"vertexai": True, "project": project, "location": location},
    )


# Multi-model router (5-tier: lite → flash → pro → sonnet → opus)
LITE_MODEL = os.environ.get("LITE_MODEL", "gemini-3.1-flash-lite")
FLASH_MODEL = os.environ.get("FLASH_MODEL", "gemini-3.5-flash")
PRO_MODEL = os.environ.get("PRO_MODEL", "gemini-3.1-pro-preview")
SONNET_MODEL = os.environ.get("SONNET_MODEL", "claude-sonnet-4-6")
OPUS_MODEL = os.environ.get("OPUS_MODEL", "claude-opus-5")
COMPLEXITY_THRESHOLD_HIGH = float(os.environ.get("COMPLEXITY_THRESHOLD_HIGH", "0.65"))
CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "gemini-3.1-flash-lite")
SIMULATOR_MODEL = os.environ.get("SIMULATOR_MODEL", "gemini-3.5-flash")

# Evaluation
EVAL_OUTPUT_DIR = os.environ.get("EVAL_OUTPUT_DIR", "eval_outputs")
BQ_EVAL_DATASET = os.environ.get("BQ_EVAL_DATASET", "geap_workshop_logs")

# No engine ids here on purpose. A deployment id is not configuration — it names
# one particular Agent Engine that may since have been redeployed or deleted, and
# a stale default sends every caller that forgets to set the env var at a resource
# that is not theirs. Pass the id in at the call site (CLI flag, manifest
# `engine_id`, or an env var read where it is used) and decide redeploy-vs-new per
# deployment.


def disable_pyopenssl():
    """Neutralize pyopenssl 26.x's context-reuse guard.

    pyopenssl 26.x wraps Context methods with _require_not_used, which
    raises ValueError when concurrent requests mutate a reused SSL context.
    We can't remove pyopenssl (google-auth mTLS needs it), so we unwrap
    all guarded methods back to their originals via __wrapped__.
    """
    try:
        import OpenSSL.SSL as _ssl  # noqa: N811  # ty: ignore[unresolved-import]

        for attr in dir(_ssl.Context):
            method = getattr(_ssl.Context, attr, None)
            if callable(method) and hasattr(method, "__wrapped__"):
                setattr(_ssl.Context, attr, method.__wrapped__)
    except ImportError:
        pass
