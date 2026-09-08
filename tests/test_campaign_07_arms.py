"""Campaign 07's arms must stay distinguishable, or the campaign measures nothing.

07 is a cost/quality frontier across four model tiers. Two of its arms --
`c07-sonnet46` (claude-sonnet-4-6) and `c07-sonnet5` (claude-sonnet-5) -- ride
the **same agent module**, `sonnet_agent`, whose `config.py` pins
`SONNET_MODEL = claude-sonnet-4-6`. The model is supposed to come from the
manifest and override that.

It did not, for a while. `stage_optimize` passed `model=` from 7219295 but the
KFP optimize component never did, and campaign 07 runs through the pipeline. So
both Claude arms would have optimized claude-sonnet-4-6 and differed only by
label -- the frontier the campaign exists to measure, confounded, with nothing
in the output to reveal it. Fixed, but nothing tested the property, and a
confound that produces plausible numbers is the kind that gets published.

These are cheap manifest-level assertions rather than a pipeline run. They
catch the shapes that silently break the comparison: two arms collapsing to one
model, an unregistered model id, or a sampling parameter the model rejects.
"""

from __future__ import annotations

import glob

import pytest

from wrangler.core.factory import PairFactory
from wrangler.core.models import get_spec

C07_ALL = sorted(glob.glob("manifests/c07-*_manifest.yaml"))
# The four optimized arms. Control arms are excluded from the distinctness
# rules below on purpose: a control exists to share its twin's model and seed,
# differing only in whether GEPA ran. Including them would make "every arm
# declares a distinct model" fail for the very property that makes a control
# a control.
C07 = [f for f in C07_ALL if "ctrl" not in f]


def _arms() -> dict[str, object]:
    arms = {}
    for path in C07:
        manifest = PairFactory.load(path)
        for pair in manifest.enabled_pairs:
            arms[pair.id] = pair
    return arms


def test_the_campaign_has_its_four_optimized_arms():
    assert len(C07) == 4, f"expected four optimized c07 manifests, found {C07}"


def test_the_campaign_has_a_control_arm_per_batch():
    """CLAUDE.md requires a control in every optimization sweep, and forbids
    reusing a floor measured on an earlier run."""
    controls = [f for f in C07_ALL if "ctrl" in f]
    assert len(controls) == 2, f"expected one control per batch, found {controls}"


def test_each_control_shares_a_model_with_an_optimized_arm():
    """A floor only calibrates arms it was measured alongside on the same model."""
    from wrangler.core.factory import PairFactory

    optimized = {PairFactory.load(f).enabled_pairs[0].model for f in C07}
    for path in (f for f in C07_ALL if "ctrl" in f):
        model = PairFactory.load(path).enabled_pairs[0].model
        assert model in optimized, f"{path} controls a model no arm optimizes: {model}"


def test_every_arm_declares_a_distinct_model():
    """The whole campaign is a comparison between these models.

    Two arms resolving to one model does not fail loudly -- it produces two
    plausible rows that happen to describe the same thing.
    """
    arms = _arms()
    models = [p.model for p in arms.values()]
    assert len(set(models)) == len(models), (
        f"c07 arms share a model, so the frontier collapses: "
        f"{sorted((i, p.model) for i, p in arms.items())}"
    )


def test_the_two_sonnet_arms_share_a_module_but_not_a_model():
    """The specific pairing that was confounded, pinned.

    They *should* share the agent module -- that is what makes the model the
    only variable. They must not share the model.
    """
    arms = _arms()
    a, b = arms.get("c07-sonnet46"), arms.get("c07-sonnet5")
    assert a is not None, f"c07-sonnet46 missing; arms are {sorted(arms)}"
    assert b is not None, f"c07-sonnet5 missing; arms are {sorted(arms)}"
    assert (a.agent_module or "") == (b.agent_module or ""), (
        "the two sonnet arms should ride the same agent module so the model is "
        "the only variable between them"
    )
    assert a.model != b.model, "the two sonnet arms must not resolve to one model"


@pytest.mark.parametrize("path", C07)
def test_every_arm_names_a_registered_model(path: str):
    """resolve_model does not raise on an unknown id -- it falls through to the
    Gemini branch and hands back Gemini(model="definitely-not-a-model"). An
    unregistered id therefore fails at inference, mid-campaign."""
    for pair in PairFactory.load(path).enabled_pairs:
        get_spec(pair.model)


@pytest.mark.parametrize("path", C07)
def test_no_arm_sets_a_sampling_param_its_model_rejects(path: str):
    """claude-sonnet-5 and later return 400 for a non-default temperature.

    PairFactory.load() is supposed to reject this at parse time; this asserts
    the campaign's manifests actually load, so a 400 cannot appear hours in.
    """
    manifest = PairFactory.load(path)
    for pair in manifest.enabled_pairs:
        spec = get_spec(pair.model)
        if not spec.supports_sampling_params:
            cfg = getattr(pair, "config", None) or {}
            for param in ("temperature", "top_p", "top_k"):
                assert param not in cfg, (
                    f"{pair.id} sets {param} on {pair.model}, which returns 400 "
                    f"for a non-default value — steer it through the prompt instead"
                )
