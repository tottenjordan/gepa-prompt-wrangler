"""Global configuration — GCP project settings, model resolution, and environment setup."""

import os
from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm

load_dotenv()

# --- GCP Settings ---
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
GCP_STAGING_BUCKET = os.environ.get(
    "GCP_STAGING_BUCKET", f"{GCP_PROJECT_ID}-wrangler-staging"
)

# --- Outputs ---
OUTPUTS_DIR = os.environ.get("OUTPUTS_DIR", "outputs")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")
DIAGRAMS_DIR = os.path.join(REPORTS_DIR, "diagrams")
EVAL_OUTPUT_DIR = os.environ.get("EVAL_OUTPUT_DIR", "eval_outputs")

# --- Model costs per 1M tokens ---
# Source: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing
MODEL_COSTS = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.5},
    "gemini-3.5-flash": {"input": 1.50, "output": 1.65},
    "gemini-3.1-pro-preview": {"input": 4.0, "output": 18.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
}

# --- PaperBanana ---
PAPERBANANA_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", GCP_PROJECT_ID)
PAPERBANANA_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")


def resolve_model(model_str: str):
    """Resolve model string to an ADK-compatible model.

    Gemini 2.x models work in regional endpoints — pass as plain strings.
    Gemini 3.x and Claude models require location=global, so they are
    wrapped with LiteLLM which supports per-model location.
    """
    if model_str.startswith(("gemini-2", "models/")):
        return model_str
    if not model_str.startswith("vertex_ai/"):
        model_str = f"vertex_ai/{model_str}"
    return LiteLlm(model=model_str, vertex_location="global")


def disable_pyopenssl():
    """Neutralize pyopenssl 26.x's context-reuse guard.

    pyopenssl 26.x wraps Context methods with _require_not_used, which
    raises ValueError when concurrent requests mutate a reused SSL context.
    We can't remove pyopenssl (google-auth mTLS needs it), so we unwrap
    all guarded methods back to their originals via __wrapped__.
    """
    try:
        import OpenSSL.SSL as _ssl
        for attr in dir(_ssl.Context):
            method = getattr(_ssl.Context, attr, None)
            if callable(method) and hasattr(method, "__wrapped__"):
                setattr(_ssl.Context, attr, method.__wrapped__)
    except ImportError:
        pass
