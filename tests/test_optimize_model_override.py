"""The model GEPA optimizes must be the model the manifest names.

`optimize()` loads its agent from a module — `sonnet_opt/__init__.py` imports
`sonnet_agent`, which builds itself from `SONNET_MODEL` in config.py. The
manifest's `model:` never reached it. A pair declaring
`model: claude-sonnet-5` would therefore **deploy sonnet-5 and optimize
sonnet-4-6**, and the eval_after would be attributed to the wrong model.

Nothing failed when that happened. The run completed, the report rendered, and
the number was simply about a different model than its label claimed — which is
the shape of defect this repo keeps finding, so it gets a test rather than a
comment.

`optimize()` already overrides the *instruction* the same way (`initial_instruction`).
The model override is the missing half.
"""

import types

import pytest


class _Agent:
    """Stands in for an LlmAgent: only `.model`, `.name`, `.instruction` matter here."""

    def __init__(self, model="claude-sonnet-4-6"):
        self.model = model
        self.name = "stub_agent"
        self.instruction = "from the module"


def _module(agent):
    return types.SimpleNamespace(agent=types.SimpleNamespace(root_agent=agent))


class TestApplyModelOverride:
    def test_an_explicit_model_replaces_the_modules(self):
        from wrangler.optimize.optimizer import _apply_model_override

        a = _Agent("claude-sonnet-4-6")
        _apply_model_override(a, "gemini-3.5-flash", tag="")
        assert "gemini-3.5-flash" in str(a.model)

    def test_no_model_leaves_the_module_alone(self):
        """Every existing caller passes nothing; none of them may change behaviour."""
        from wrangler.optimize.optimizer import _apply_model_override

        a = _Agent("claude-sonnet-4-6")
        _apply_model_override(a, "", tag="")
        assert a.model == "claude-sonnet-4-6"

    def test_the_override_goes_through_resolve_model(self):
        """A bare Claude id is not servable; it needs the global resource path."""
        from wrangler.optimize.optimizer import _apply_model_override

        a = _Agent("gemini-3.5-flash")
        _apply_model_override(a, "claude-sonnet-4-6", tag="")
        # resolve_model returns a Claude object carrying the full resource name,
        # not the bare id — that is the whole point of routing through it.
        resolved = a.model
        is_object = not isinstance(resolved, str)
        assert is_object or "publishers/anthropic" in resolved

    def test_an_unregistered_model_fails_loudly(self):
        """`resolve_model` does not raise on a typo.

        It falls through to the Gemini branch and returns
        Gemini(model="definitely-not-a-model"), so without an explicit registry
        check a mistyped id would quietly optimize a nonexistent Gemini model.
        """
        from wrangler.optimize.optimizer import _apply_model_override

        with pytest.raises(KeyError):
            _apply_model_override(_Agent(), "definitely-not-a-model", tag="")

    def test_a_registered_model_does_not_raise(self):
        from wrangler.optimize.optimizer import _apply_model_override

        _apply_model_override(_Agent(), "gemini-3.5-flash", tag="")


class TestOptimizeAcceptsModel:
    def test_optimize_exposes_a_model_parameter(self):
        import inspect

        from wrangler.optimize.optimizer import optimize

        assert "model" in inspect.signature(optimize).parameters

    def test_the_model_parameter_defaults_to_empty(self):
        import inspect

        from wrangler.optimize.optimizer import optimize

        assert inspect.signature(optimize).parameters["model"].default == ""


class TestCallersThreadItThrough:
    """A parameter nothing passes is a parameter that does nothing."""

    def test_the_local_stage_passes_the_pairs_model(self):
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def stage_optimize")
        body = src[i : i + 4000]
        assert "model=pair.model" in body, "stage_optimize must pass the pair's model"

    def test_the_kfp_component_passes_the_pairs_model(self):
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        i = src.index("def optimize_single_agent")
        body = src[i : i + 8000]
        assert "optimize(" in body
        assert "model=" in body
