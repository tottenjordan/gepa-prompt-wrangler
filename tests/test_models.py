"""Tests for the central model registry."""

import datetime as dt

import pytest

from wrangler.core.models import (
    MODELS,
    ModelSpec,
    blended_cost,
    get_batch_config,
    get_spec,
    resolve_model,
)


def test_every_model_has_complete_metadata():
    """A model without cost, rate limit, or retirement date is a landmine."""
    for name, spec in MODELS.items():
        assert spec.input_cost > 0, f"{name}: missing input cost"
        assert spec.output_cost > 0, f"{name}: missing output cost"
        assert spec.rpm > 0, f"{name}: missing rate limit"
        assert spec.family in ("gemini", "claude"), f"{name}: unknown family"


def test_registry_keys_match_spec_names():
    """A key/name mismatch makes get_spec() return a spec for a different model."""
    for key, spec in MODELS.items():
        assert key == spec.name, f"registry key {key!r} != spec.name {spec.name!r}"


def test_retired_models_are_flagged():
    """Models past their retirement date must be marked, not silently served.

    This uses the real date on purpose, so it is a tripwire rather than a
    tautology: on the day a registered model's announced shutdown passes, CI
    goes red and someone has to make a decision. Retired Vertex model ids
    return 404, so the alternative is discovering it from a failed run.

    To clear a failure, set ``retired=True`` on the spec and migrate callers.
    """
    today = dt.datetime.now(tz=dt.UTC).date()
    for name, spec in MODELS.items():
        if spec.retirement_date and spec.retirement_date <= today:
            assert spec.retired, (
                f"{name} passed its retirement date ({spec.retirement_date}) and now "
                f"returns 404 on Vertex. Set retired=True and migrate callers. {spec.notes}"
            )


def test_gemini_2x_resolves_to_plain_string():
    """Gemini 2.x uses regional endpoints and is passed through as a string."""
    assert resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_unknown_model_raises():
    """An unregistered model id must fail loudly, not silently cost money."""
    with pytest.raises(KeyError, match="not-a-model"):
        blended_cost("not-a-model")


def test_get_spec_raises_for_unknown_model():
    with pytest.raises(KeyError, match="not-a-model"):
        get_spec("not-a-model")


def test_blended_cost_uses_four_to_one_ratio():
    """gemini-2.5-flash: (4 * 0.15 + 1 * 0.60) / 5 == 0.24."""
    assert blended_cost("gemini-2.5-flash") == pytest.approx(0.24)


def test_blended_cost_accepts_custom_costs_for_unregistered_models():
    """Custom costs bypass the registry, so callers can price an ad-hoc model."""
    assert blended_cost("not-a-model", {"input": 4.0, "output": 4.0}) == pytest.approx(4.0)


class TestGetBatchConfig:
    def test_low_rpm_model_gets_smallest_batch(self):
        assert get_batch_config("gemini-3.5-flash") == (4, 15.0, 4)

    def test_mid_rpm_model_gets_medium_batch(self):
        assert get_batch_config("gemini-2.5-flash") == (16, 5.0, 10)

    def test_high_rpm_model_gets_largest_batch(self):
        assert get_batch_config("claude-sonnet-4-6") == (64, 0.0, 20)

    def test_unknown_model_falls_back_to_default_rpm(self):
        """Unregistered ids get 90 RPM -- conservative, not unlimited."""
        assert get_batch_config("some-future-model") == (16, 5.0, 10)

    def test_lookup_is_exact_not_substring(self):
        """The old RATE_LIMITS loop matched by substring.

        ``"gemini-3.5-flash" in "gemini-3.5-flash-lite"`` is true, so a
        hypothetical lite variant silently inherited the parent's limit, and
        which limit won depended on dict insertion order.
        """
        assert "gemini-3.5-flash-lite" not in MODELS
        assert get_batch_config("gemini-3.5-flash-lite") == (16, 5.0, 10)
        assert get_batch_config("gemini-3.5-flash") == (4, 15.0, 4)


class TestResolveModel:
    def test_models_prefix_passes_through(self):
        assert resolve_model("models/gemini-pro") == "models/gemini-pro"

    def test_gemini_3x_returns_gemini_object(self):
        result = resolve_model("gemini-3.5-flash")
        assert not isinstance(result, str)
        assert result.model == "gemini-3.5-flash"

    def test_claude_returns_claude_object(self):
        result = resolve_model("claude-sonnet-4-6")
        assert not isinstance(result, str)
        assert result.model == "claude-sonnet-4-6"


def test_model_spec_is_immutable():
    """Specs are frozen so a caller cannot mutate shared cost data in place."""
    spec = get_spec("gemini-2.5-flash")
    assert isinstance(spec, ModelSpec)
    with pytest.raises(AttributeError):
        spec.input_cost = 0.0


# Sites that intentionally keep a literal model id, with the reason. Anything
# not listed here must import from the registry instead.
LITERAL_EXCEPTIONS = {
    (
        "wrangler/pipeline/components.py",
        "gemini-3.5-flash",
    ): "KFP serializes each @dsl.component body in isolation, so the component "
    "cannot import the registry at runtime.",
    (
        "wrangler/pipeline/components.py",
        "gemini-3.1-flash-image",
    ): "Same KFP isolation rule; also a PaperBanana image model rather than an "
    "agent or judge model, so it has no cost or RPM to register.",
}

# Ids allowed to be absent from MODELS. Only for models the framework never
# runs an agent or a judge on, so they have no cost or rate limit to record.
UNREGISTERED_MODEL_IDS = {
    "gemini-3.1-flash-image": "PaperBanana chart renderer, not an agent or judge model.",
}


def _model_id_literals() -> list[tuple[str, str, int]]:
    """Yield (relative path, model id, line) for every model id string in wrangler/.

    Walks the AST rather than grepping so comments (absent from the AST) and
    docstrings are exempt: prose is allowed to name a model, code is not.

    A version component is required, which is what separates a model id from a
    manifest pair id — `wrangler init` writes pairs called "gemini-flash" and
    "claude-sonnet", and those are labels the user picks, not models.
    """
    import ast
    import re
    from pathlib import Path

    pattern = re.compile(r"^(gemini|claude)-[\w.\-]*\d[\w.\-]*$")
    found = []

    for path in sorted(Path("wrangler").rglob("*.py")):
        rel = path.as_posix()
        if rel == "wrangler/core/models.py":
            continue
        tree = ast.parse(path.read_text(), filename=rel)
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        found.extend(
            (rel, node.value, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and pattern.match(node.value)
        )

    return found


def test_every_model_id_literal_is_registered():
    """Tier 1: wherever a literal survives, it must name a model we know about.

    An unregistered id has no cost, no rate limit, and no retirement date, so it
    silently prices at zero and throttles at the default.
    """
    offenders = [
        f"{path}:{line}: {model}"
        for path, model, line in _model_id_literals()
        if model not in MODELS and model not in UNREGISTERED_MODEL_IDS
    ]
    assert not offenders, (
        "Unregistered model ids found — add them to wrangler/core/models.py:\n"
        + "\n".join(offenders)
    )


def test_no_model_id_literals_outside_the_registry():
    """Tier 2: even a registered id should not be hardcoded outside the registry.

    This is the test that makes a model migration a one-file edit. When it
    fails, the fix is to import a named role constant from wrangler.core.models
    — or, if the site genuinely cannot import (see the KFP components), to add
    it to LITERAL_EXCEPTIONS with the reason.
    """
    offenders = [
        f"{path}:{line}: {model}"
        for path, model, line in _model_id_literals()
        if (path, model) not in LITERAL_EXCEPTIONS
    ]
    assert not offenders, (
        "Hardcoded model ids found. Import a constant from wrangler.core.models, "
        "or add an entry to LITERAL_EXCEPTIONS explaining why this one cannot:\n"
        + "\n".join(offenders)
    )
