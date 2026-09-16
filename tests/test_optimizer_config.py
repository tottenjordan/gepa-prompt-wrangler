"""GEPA's reflection config has to suit the model that writes the prompts.

ADK ships one default for every optimizer model: `thinking_budget=10240`, which maps to
Anthropic's `{"type": "enabled", "budget_tokens": 10240}`. Claude Opus 4.7 and later reject
that outright -- probed against Vertex on 2026-09-16:

    adaptive (-1)    -> OK
    enabled (10240)  -> 400 '"thinking.type.enabled" is not supported for this model'

so the config is built per model rather than taken as given.
"""

from __future__ import annotations

import pytest

from wrangler.core.models import DEFAULT_OPTIMIZER_MODEL
from wrangler.optimize.optimizer_config import ADK_DEFAULT_THINKING_BUDGET, build_optimizer_config


class TestClaude:
    def test_claude_gets_adaptive_thinking(self):
        cfg = build_optimizer_config("claude-opus-4-8")
        assert cfg.thinking_config.thinking_budget == -1

    def test_the_budget_maps_to_a_param_anthropic_accepts(self):
        """The assertion that matters -- checked against ADK's real mapper, not a guess."""
        from google.adk.models.anthropic_llm import _build_anthropic_thinking_param

        param = _build_anthropic_thinking_param(build_optimizer_config("claude-opus-4-8"))
        assert param == {"type": "adaptive"}, (
            f"got {param}. Vertex returns 400 for thinking.type.enabled on Opus 4.7+."
        )

    def test_max_output_tokens_exceeds_any_manual_budget(self):
        """Anthropic requires budget_tokens < max_tokens, and ADK's Claude default is 8192.

        Even on a model that accepted `enabled`, 10240 against 8192 is a 400. The config
        carries max_output_tokens so the caller is not relying on that default.
        """
        cfg = build_optimizer_config("claude-opus-4-8")
        assert cfg.max_output_tokens > ADK_DEFAULT_THINKING_BUDGET

    def test_thinking_level_is_never_set(self):
        """ADK rejects thinking_level for Anthropic before the request is even built."""
        cfg = build_optimizer_config("claude-opus-4-8")
        assert cfg.thinking_config.thinking_level is None


class TestGemini:
    def test_gemini_keeps_adks_explicit_budget(self):
        """No reason to diverge from ADK where its default works; a needless difference
        is one more thing to explain when a Gemini optimize run behaves oddly."""
        cfg = build_optimizer_config("gemini-3.5-flash")
        assert cfg.thinking_config.thinking_budget == ADK_DEFAULT_THINKING_BUDGET

    def test_gemini_still_gets_thoughts(self):
        cfg = build_optimizer_config("gemini-3.5-flash")
        assert cfg.thinking_config.include_thoughts is True


class TestTheGenerationSplit:
    """The branch reads `supports_sampling_params` as a generation marker. Pin that."""

    def test_the_registry_field_still_marks_the_right_generation(self):
        """If these ever diverge, the branch in optimizer_config.py is the line that breaks.

        `supports_sampling_params=False` means "Opus 4.7+, Sonnet 5, Fable 5" -- the same
        set that requires adaptive thinking. Two different vendor behaviours, one cutoff.
        """
        from wrangler.core.models import MODELS

        adaptive = {n for n, s in MODELS.items() if not s.supports_sampling_params}
        assert "claude-opus-4-8" in adaptive
        assert "claude-sonnet-5" in adaptive
        assert "claude-sonnet-4-6" not in adaptive, (
            "Sonnet 4.6 now reports supports_sampling_params=False. If the vendor cutoffs "
            "for sampling params and for thinking.type have diverged, this field can no "
            "longer stand in for the thinking generation -- split them."
        )

    @pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"])
    def test_every_new_generation_claude_gets_adaptive(self, model):
        assert build_optimizer_config(model).thinking_config.thinking_budget == -1


def test_the_shipped_default_is_configured_correctly():
    """Whatever DEFAULT_OPTIMIZER_MODEL is, the config it gets must be usable."""
    cfg = build_optimizer_config(DEFAULT_OPTIMIZER_MODEL)
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget is not None, (
        "a None budget makes ADK raise before the request is sent"
    )
