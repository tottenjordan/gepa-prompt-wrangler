"""Global configuration — GCP project settings, model resolution, and environment setup."""

import os

from dotenv import load_dotenv

from .models import (
    BLENDED_INPUT_WEIGHT,
    BLENDED_OUTPUT_WEIGHT,
    MODELS,
    blended_cost,
    get_batch_config,
    resolve_model,
)

load_dotenv()

# --- GCP Settings ---
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
GCP_STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET", f"{GCP_PROJECT_ID}-wrangler-staging")

# --- Outputs ---
OUTPUTS_DIR = os.environ.get("OUTPUTS_DIR", "outputs")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")
DIAGRAMS_DIR = os.path.join(REPORTS_DIR, "diagrams")
EVAL_OUTPUT_DIR = os.environ.get("EVAL_OUTPUT_DIR", "eval_outputs")

# --- Model metadata ---
# wrangler/core/models.py is the single source of truth. The two dicts below are
# derived views kept for the existing importers of config.MODEL_COSTS /
# config.RATE_LIMITS; new code should import from .models directly.
MODEL_COSTS = {
    name: {"input": spec.input_cost, "output": spec.output_cost} for name, spec in MODELS.items()
}
RATE_LIMITS = {name: spec.rpm for name, spec in MODELS.items()}

# blended_cost / get_batch_config / resolve_model are imported above purely so
# that `from wrangler.core.config import resolve_model` keeps working; __all__ is
# what tells ruff these re-exports are deliberate rather than dead imports.
__all__ = [
    "BLENDED_INPUT_WEIGHT",
    "BLENDED_OUTPUT_WEIGHT",
    "DIAGRAMS_DIR",
    "EVAL_OUTPUT_DIR",
    "GCP_PROJECT_ID",
    "GCP_REGION",
    "GCP_STAGING_BUCKET",
    "MODELS",
    "MODEL_COSTS",
    "OUTPUTS_DIR",
    "RATE_LIMITS",
    "REPORTS_DIR",
    "blended_cost",
    "disable_pyopenssl",
    "get_batch_config",
    "resolve_model",
]


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
