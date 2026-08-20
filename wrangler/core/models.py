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
    # False for Claude Opus 4.7 and later, which return 400 when temperature,
    # top_p, or top_k is set to a non-default value. Prompt for the behavior
    # instead. https://platform.claude.com/docs/en/about-claude/model-deprecations
    supports_sampling_params: bool = True
    notes: str = ""
    # Short key used for this model in manifests, agent dirs, and reports
    # (e.g. "opus47"). Empty means the model is not offered as an agent pair,
    # which is why it is absent from MODEL_MAP and AGENT_ORDER.
    alias: str = ""

    @property
    def provider(self) -> str:
        """Display name of the vendor, for report grouping."""
        return {"gemini": "Google", "claude": "Anthropic"}[self.family]


# Costs are standard (non-batch, non-cached) list price per 1M tokens, verified
# 2026-08-20 against:
#   https://ai.google.dev/gemini-api/docs/pricing
#   https://platform.claude.com/docs/en/about-claude/pricing
#
# Retirement dates come from the vendor deprecation pages. Two caveats:
#   - Anthropic's dates are "not sooner than", and they apply to Anthropic-operated
#     platforms. Google Cloud sets its own schedule for partner models, so treat the
#     Claude dates as an early-warning floor rather than a contract.
#   - Google publishes two dates for the 2.x shutdown: 2026-10-16 for the Gemini
#     Developer API and 2026-10-20 for Agent Platform. We record the earlier one.
MODELS: dict[str, ModelSpec] = {
    # --- Gemini 2.x — RETIRING 2026-10-16, regional endpoints ---
    "gemini-2.5-flash": ModelSpec(
        "gemini-2.5-flash",
        "gemini",
        0.30,
        2.50,
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
    "gemini-3.1-flash-lite": ModelSpec(
        "gemini-3.1-flash-lite",
        "gemini",
        0.25,
        1.5,
        5,
        retirement_date=dt.date(2027, 5, 7),
        notes="Successor: gemini-3.5-flash-lite",
        alias="lite",
    ),
    "gemini-3.5-flash-lite": ModelSpec(
        "gemini-3.5-flash-lite", "gemini", 0.30, 2.50, 5, alias="lite35"
    ),
    "gemini-3.5-flash": ModelSpec(
        "gemini-3.5-flash",
        "gemini",
        1.50,
        9.0,
        5,
        retirement_date=dt.date(2027, 5, 19),
        alias="flash",
    ),
    "gemini-3.6-flash": ModelSpec(
        "gemini-3.6-flash",
        "gemini",
        0.75,
        3.75,
        5,
        notes=(
            "GA since 2026-07-21, but on the short-term availability track: retires 45 "
            "days after a replacement ships, with no date published in advance. Cheaper "
            "and newer than gemini-3.5-flash, but 3.5-flash is the stable pin (shutdown "
            "not before 2027-05-19). Price rises to 1.50/7.50 on 2027-01-01."
        ),
        alias="flash36",
    ),
    "gemini-3.1-pro-preview": ModelSpec(
        "gemini-3.1-pro-preview",
        "gemini",
        4.0,
        18.0,
        5,
        notes="Preview id — expect churn; may be repointed after GA. Costs are the >200k tier",
        alias="pro",
    ),
    # --- Claude — global/multi-region endpoints ---
    # Costs assume the global endpoint, which the repo pins via
    # GOOGLE_CLOUD_LOCATION=global. Regional and multi-region endpoints carry a
    # 10% premium on top of these for Claude 4.5 and later.
    "claude-sonnet-4-6": ModelSpec(
        "claude-sonnet-4-6",
        "claude",
        3.0,
        15.0,
        2000,
        retirement_date=dt.date(2027, 2, 17),
        alias="sonnet",
    ),
    "claude-sonnet-5": ModelSpec(
        "claude-sonnet-5",
        "claude",
        2.0,
        10.0,
        2000,
        retirement_date=dt.date(2027, 6, 30),
        supports_sampling_params=False,
        notes="Cheaper than sonnet-4-6 in both directions",
        alias="sonnet5",
    ),
    "claude-opus-4-6": ModelSpec(
        "claude-opus-4-6",
        "claude",
        5.0,
        25.0,
        800,
        retirement_date=dt.date(2027, 2, 5),
        alias="opus",
    ),
    "claude-opus-4-7": ModelSpec(
        "claude-opus-4-7",
        "claude",
        5.0,
        25.0,
        800,
        retirement_date=dt.date(2027, 4, 16),
        supports_sampling_params=False,
        notes="First model on the newer tokenizer: ~30% more tokens for the same text",
        alias="opus47",
    ),
    "claude-opus-4-8": ModelSpec(
        "claude-opus-4-8",
        "claude",
        5.0,
        25.0,
        800,
        retirement_date=dt.date(2027, 5, 28),
        supports_sampling_params=False,
        alias="opus48",
    ),
    "claude-opus-5": ModelSpec(
        "claude-opus-5",
        "claude",
        5.0,
        25.0,
        800,
        retirement_date=dt.date(2027, 7, 24),
        supports_sampling_params=False,
        alias="opus5",
    ),
    "claude-fable-5": ModelSpec(
        "claude-fable-5",
        "claude",
        10.0,
        50.0,
        800,
        retirement_date=dt.date(2027, 6, 9),
        supports_sampling_params=False,
        alias="fable",
    ),
}


# --- Named roles ---------------------------------------------------------
#
# Change these to change the framework's defaults. Every default in wrangler/
# points at one of them, so a model migration is an edit to this block.
#
# The judge constants do not all hold the same value, because that is what the
# code did before they were named. Naming them is what made the inconsistency
# visible at all — it was spread over 12 literals in 10 files.
#
# A judge change silently re-scores every metric, so old and new reports stop
# being comparable. That is why the scoring judges below are NOT being migrated
# opportunistically: see docs/notes/model-lifecycle.md and Task 3.2b of
# docs/plans/2026-08-20-repo-modernization.md, which requires an A/B run against
# the smoke evalset before any of them moves.

# Judge for GEPA optimization, batch eval, and eval-set conversion. This is the
# main scoring path.
#
# DEADLINE: gemini-2.5-flash retires 2026-10-16. When it 404s, GEPA optimization
# AND batch eval both stop, not just inference. The guard test
# test_default_models_are_not_near_retirement turns this into a red build 30
# days out.
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"

# Judge used when a manifest's eval_config omits judge_model, and in the
# manifest scaffold `wrangler init` writes. Already on 3.x — so the claim that a
# 3.x judge is unproven here is not quite true; one scoring path has been using
# one. Worth weighing in Task 3.2b.
DEFAULT_MANIFEST_JUDGE_MODEL = "gemini-3.5-flash"

# Judge written into generated scaffolding (wrangler inspect, prompt registry).
# Migrated off gemini-2.5-pro ahead of the scoring judges: this value is emitted
# into config files that do not exist yet, so there is no baseline to keep
# comparable, and shipping a scaffold that names a model retiring in weeks is a
# defect on its own. Same pro tier as before; see the churn note on the spec.
DEFAULT_SCAFFOLD_JUDGE_MODEL = "gemini-3.1-pro-preview"

# Multi-judge ensemble. Order matters: the first is the tie-breaker. Scoring
# path — holds at 2.5 with DEFAULT_JUDGE_MODEL, and moves with it.
DEFAULT_JUDGE_ENSEMBLE = ["gemini-2.5-pro", "gemini-2.5-flash"]

# Agent model for the manifest scaffold's example pairs. Deliberately
# gemini-3.5-flash and not the cheaper, newer gemini-3.6-flash: 3.6 is on the
# short-term availability track and can retire 45 days after a replacement
# ships, with no date announced in advance. A framework default wants the stable
# pin. Pick 3.6 explicitly in a manifest when the cost matters more.
DEFAULT_AGENT_MODEL = "gemini-3.5-flash"
DEFAULT_AGENT_MODEL_ALT = "claude-sonnet-4-6"

# --- Derived views -------------------------------------------------------

# Vendor per model id, for grouping in reports.
PROVIDERS: dict[str, str] = {name: spec.provider for name, spec in MODELS.items()}

# Short agent key -> model id, and the display order reports iterate in.
# Only models offered as agent pairs carry an alias.
MODEL_MAP: dict[str, str] = {spec.alias: name for name, spec in MODELS.items() if spec.alias}
AGENT_ORDER: list[str] = list(MODEL_MAP)


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
