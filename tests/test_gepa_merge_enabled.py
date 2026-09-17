"""`use_merge` is off in the installed gepa, and ADK gives no way to turn it on.

`gepa.optimize()` takes 46 arguments; ADK forwards 8, and `use_merge` is not among them. The
installed gepa defaults it to **False** while the published guidance says it defaults True
and recommends keeping it on.

Merge proposes a candidate combining two Pareto-frontier parents that win on *different*
examples, costing one re-evaluation per attempt and capped by `max_merge_invocations`
(default 5). The reason to want it here: GEPA+Merge is reported to produce prompts **up to
9.2x shorter while scoring higher**, and prompt length is the mechanism this repo
hypothesised, failed to support from its own arm-level data, and has not otherwise been able
to act on.

Turning it on means injecting the argument at the `gepa.optimize` call, since the config
object has nowhere to put it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def inject():
    from wrangler.optimize.optimizer import _gepa_extra_kwargs

    return _gepa_extra_kwargs


def test_merge_is_requested(inject):
    assert inject().get("use_merge") is True


def test_the_installed_default_is_still_off():
    """If gepa ever flips its default, this patch becomes redundant -- notice then.

    Not a failure either way; it tells you whether the injection is still doing work.
    """
    import inspect

    import gepa

    default = inspect.signature(gepa.optimize).parameters["use_merge"].default
    assert default in (True, False)


def test_only_arguments_gepa_actually_accepts_are_injected(inject):
    """A stray kwarg would TypeError nine hours in, at the one call that matters."""
    import inspect

    import gepa

    accepted = set(inspect.signature(gepa.optimize).parameters)
    assert set(inject()) <= accepted, set(inject()) - accepted


class TestTheWrapperActuallyInjects:
    """Behavioural, with a stub whose SIGNATURE declares the parameter.

    A stub taking `*args, **kwargs` would fail here for the wrong reason: the wrapper
    filters against the live signature, so a signature-less stub advertises nothing and
    nothing is injected. That is correct behaviour and a misleading test -- it caught me
    out once while verifying this by hand.
    """

    @staticmethod
    def _install(monkeypatch, seen):
        import gepa

        import wrangler.optimize.optimizer as wo

        def fake_optimize(seed_candidate=None, trainset=None, use_merge=False, **kw):
            seen["use_merge"] = use_merge
            return "result"

        monkeypatch.setattr(gepa, "optimize", fake_optimize)
        wo._patch_gepa_optimize()
        return gepa

    def test_merge_is_injected_when_the_caller_is_silent(self, monkeypatch):
        seen = {}
        gepa = self._install(monkeypatch, seen)
        gepa.optimize(seed_candidate={}, trainset=[])
        assert seen["use_merge"] is True

    def test_an_explicit_caller_value_wins(self, monkeypatch):
        """Raise the floor; never override what ADK decides to start forwarding."""
        seen = {}
        gepa = self._install(monkeypatch, seen)
        gepa.optimize(seed_candidate={}, trainset=[], use_merge=False)
        assert seen["use_merge"] is False

    def test_patching_twice_does_not_double_wrap(self, monkeypatch):
        import gepa

        import wrangler.optimize.optimizer as wo

        seen = {}
        self._install(monkeypatch, seen)
        first = gepa.optimize
        wo._patch_gepa_optimize()
        assert gepa.optimize is first


def test_adk_still_does_not_forward_it(inject):
    """The whole reason this exists. If ADK starts forwarding it, drop the injection."""
    import inspect

    from google.adk.optimization import gepa_root_agent_prompt_optimizer as g

    src = inspect.getsource(g)
    call = src[src.index("return gepa.optimize(") : src.index("return gepa.optimize(") + 600]
    assert "use_merge" not in call, (
        "ADK now forwards use_merge itself -- remove the injection rather than "
        "fighting it, and check what value it forwards"
    )
