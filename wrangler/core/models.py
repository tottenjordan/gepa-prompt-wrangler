"""Central model registry — the single source of truth for model metadata.

Every model id used anywhere in this repo must be registered here with its
cost, rate limit, and retirement date. Nothing else should hardcode a model
string. See CODE_STANDARDS.md section 8.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

BLENDED_INPUT_WEIGHT = 4
BLENDED_OUTPUT_WEIGHT = 1

# RPM assumed for an id that is not registered. Deliberately mid-range: high
# enough not to stall a legitimate new model, low enough not to trigger 429s.
DEFAULT_RPM = 90


@dataclass(frozen=True)
class ModelSpec:
    """Everything the framework needs to know about a model.

    Costs are USD per 1M tokens. `rpm` drives inference throttling.
    `retirement_date` is the earliest announced shutdown; see
    docs/notes/model-lifecycle.md.
    """

    name: str
    family: str  # "gemini" | "claude"
    input_cost: float
    output_cost: float
    rpm: int
    retirement_date: dt.date | None = None
    retired: bool = False
    notes: str = ""


# Costs source:
# https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing
MODELS: dict[str, ModelSpec] = {
    # --- Gemini 2.x — RETIRING 2026-10-16, regional endpoints ---
    "gemini-2.5-flash": ModelSpec(
        "gemini-2.5-flash",
        "gemini",
        0.15,
        0.60,
        100,
        retirement_date=dt.date(2026, 10, 16),
        notes="Successor: gemini-3.6-flash",
    ),
    "gemini-2.5-pro": ModelSpec(
        "gemini-2.5-pro",
        "gemini",
        1.25,
        10.0,
        80,
        retirement_date=dt.date(2026, 10, 16),
        notes="Successor: gemini-3.1-pro-preview",
    ),
    # --- Gemini 3.x — global endpoint ---
    "gemini-3.1-flash-lite": ModelSpec("gemini-3.1-flash-lite", "gemini", 0.25, 1.5, 5),
    "gemini-3.5-flash": ModelSpec("gemini-3.5-flash", "gemini", 1.50, 9.0, 5),
    "gemini-3.1-pro-preview": ModelSpec(
        "gemini-3.1-pro-preview",
        "gemini",
        4.0,
        18.0,
        5,
        notes="Preview id — expect churn; may be repointed after GA",
    ),
    # --- Claude — global/multi-region endpoints ---
    "claude-sonnet-4-6": ModelSpec("claude-sonnet-4-6", "claude", 3.0, 15.0, 2000),
    "claude-opus-4-6": ModelSpec(
        "claude-opus-4-6",
        "claude",
        5.0,
        25.0,
        800,
        retirement_date=dt.date(2027, 2, 5),
    ),
    "claude-opus-4-7": ModelSpec("claude-opus-4-7", "claude", 5.0, 25.0, 800),
    "claude-opus-4-8": ModelSpec("claude-opus-4-8", "claude", 5.0, 25.0, 800),
    "claude-fable-5": ModelSpec("claude-fable-5", "claude", 10.0, 50.0, 800),
}


def get_spec(model: str) -> ModelSpec:
    """Return the spec for `model`, raising KeyError if unregistered."""
    if model not in MODELS:
        msg = (
            f"Model {model!r} is not registered in wrangler.core.models.MODELS. "
            f"Add it with cost, rate limit, and retirement date."
        )
        raise KeyError(msg)
    return MODELS[model]


def blended_cost(model: str, custom_costs: dict[str, float] | None = None) -> float:
    """Estimated cost per 1M tokens assuming a 4:1 input:output token ratio."""
    if custom_costs is not None:
        inp, out = custom_costs["input"], custom_costs["output"]
    else:
        spec = get_spec(model)
        inp, out = spec.input_cost, spec.output_cost
    weight = BLENDED_INPUT_WEIGHT + BLENDED_OUTPUT_WEIGHT
    return (BLENDED_INPUT_WEIGHT * inp + BLENDED_OUTPUT_WEIGHT * out) / weight


def blended_cost_for_report(model: str) -> float:
    """`blended_cost` for report rendering, where an unregistered id must not abort.

    A report spanning ten model pairs should not fail outright because one of
    them used an ad-hoc id, so this returns 0.0 instead of raising. It logs a
    warning naming the model, which is the part that matters: a $0.00 row with
    no explanation is how an unpriced model gets mistaken for a free one.

    Cost-critical paths should call `blended_cost` and let the KeyError through.
    """
    try:
        return blended_cost(model)
    except KeyError:
        log.warning(
            "Model %r is not in the registry; reporting its cost as $0.00. "
            "Add it to wrangler/core/models.py to price it correctly.",
            model,
        )
        return 0.0


def get_batch_config(model: str) -> tuple[int, float, int]:
    """Return (batch_size, delay_seconds, max_workers) based on the model's RPM.

    Lookup is exact. The predecessor matched by substring over a rate-limit
    dict, so ``"gemini-2.5-flash"`` matched ``"gemini-2.5-flash-lite"`` and the
    winner depended on dict insertion order.
    """
    spec = MODELS.get(model)
    rpm = spec.rpm if spec else DEFAULT_RPM
    if rpm <= 10:
        return 4, 15.0, 4
    if rpm <= 100:
        return 16, 5.0, 10
    return 64, 0.0, 20


def resolve_model(model_str: str):
    """Resolve a model string to an ADK-compatible model object.

    Gemini 2.x works on regional endpoints — passed through as a plain string.
    Gemini 3.x uses the native Gemini class; Claude uses the native Claude
    class. Both read GOOGLE_CLOUD_LOCATION from the environment, which must
    be "global".
    """
    if model_str.startswith(("gemini-2", "models/")):
        return model_str
    if model_str.startswith("claude"):
        from google.adk.models.anthropic_llm import Claude

        return Claude(model=model_str)
    from google.adk.models.google_llm import Gemini

    return Gemini(model=model_str)
