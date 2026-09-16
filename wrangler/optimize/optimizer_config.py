"""The reflection-request config for GEPA's optimizer model.

GEPA's optimizer model reads the eval failures and writes the next candidate prompt. ADK
ships one `model_configuration` for every such model —
`ThinkingConfig(include_thoughts=True, thinking_budget=10240)` — and that default is
Gemini-shaped. A *positive* budget maps to Anthropic's
`{"type": "enabled", "budget_tokens": …}`, which Claude Opus 4.7 and later reject.

Probed against Vertex on 2026-09-16 with `claude-opus-4-8`, because every claim here is
about the *API*, not about ADK, and ADK's mapper alone cannot tell you which the vendor
accepts:

    adaptive (-1)    -> OK
    enabled (10240)  -> 400 '"thinking.type.enabled" is not supported for this model'

Nothing here patches ADK. `model_configuration` is a plain field on
`GEPARootAgentPromptOptimizerConfig`, and `_gepa_utils.generate_reflection_response`
forwards `config.max_output_tokens` to Anthropic's `max_tokens`, so both knobs are already
reachable from the caller.
"""

from __future__ import annotations

from google.genai import types as genai_types

from ..core.models import get_spec

# ADK's own default, mirrored rather than imported: it lives in a Pydantic field default on
# `GEPARootAgentPromptOptimizerConfig`, and reaching into `model_fields` to read it would
# make this module fail on a field rename instead of quietly keeping a sane number.
# `tests/test_adk_optimizer_model.py` watches ADK's default for drift.
ADK_DEFAULT_THINKING_BUDGET = 10240

# Anthropic's adaptive mode: the model chooses its own thinking depth. Required for Opus 4.7
# and later, recommended for Opus 4.6 and Sonnet 4.6 where "enabled" is deprecated.
ADAPTIVE_THINKING_BUDGET = -1

# Thinking tokens and the written prompt share this budget. Campaign 07's optimized prompts
# reached ~3,900 characters (~1k tokens), so the headroom here is almost all thinking.
# Set explicitly because ADK's `Claude.max_tokens` default is 8192, which is *below* the
# 10240 budget ADK would otherwise ask for -- a combination that 400s on its own terms.
CLAUDE_MAX_OUTPUT_TOKENS = 16384


def _needs_adaptive_thinking(model: str) -> bool:
    """Is this model in the generation that rejects `thinking.type: "enabled"`?

    **Reading `supports_sampling_params` as a generation marker, not for its literal
    meaning.** That flag marks Claude Opus 4.7 and later, plus Sonnet 5 and Fable 5 — which
    is exactly the set that requires adaptive thinking. Two unrelated vendor behaviours
    happen to share one cutoff, and leaning on that is cheaper than a second field that can
    drift out of step with the first.

    If the vendor ever moves one cutoff without the other, **this is the line that breaks**,
    and `test_the_registry_field_still_marks_the_right_generation` is the test that says so.
    """
    spec = get_spec(model)
    return spec.provider == "Anthropic" and not spec.supports_sampling_params


def build_optimizer_config(model: str) -> genai_types.GenerateContentConfig:
    """The `model_configuration` to hand GEPA for a given optimizer model.

    Gemini keeps ADK's default. Diverging there would be a difference with no reason behind
    it, and one more thing to rule out when a Gemini optimize run behaves oddly.
    """
    if _needs_adaptive_thinking(model):
        return genai_types.GenerateContentConfig(
            thinking_config=genai_types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=ADAPTIVE_THINKING_BUDGET,
            ),
            max_output_tokens=CLAUDE_MAX_OUTPUT_TOKENS,
        )
    return genai_types.GenerateContentConfig(
        thinking_config=genai_types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=ADK_DEFAULT_THINKING_BUDGET,
        )
    )
