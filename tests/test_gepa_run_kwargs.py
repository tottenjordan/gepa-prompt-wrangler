"""Tests for the per-run arguments injected into `gepa.optimize()`.

The injection hook is the only route this repo has to `gepa.optimize`'s other 38
arguments, because ADK forwards 8 and has no config field for the rest. Everything here
guards a failure that costs a nine-hour stage: a `TypeError` at the single call, a seed
that differs between processes, or a stopper that silently never fires.
"""

from __future__ import annotations

import subprocess
import sys
from typing import ClassVar

import pytest

from wrangler.optimize import optimizer as opt


@pytest.fixture(autouse=True)
def _clean_run_kwargs():
    """Run-scoped state must not leak between tests any more than between arms."""
    opt._GEPA_RUN_KWARGS.clear()
    yield
    opt._GEPA_RUN_KWARGS.clear()


class TestDefaultsUnchanged:
    def test_with_no_run_state_only_use_merge_is_injected(self):
        """The pre-existing behaviour must be byte-identical when nothing sets a
        per-run value. `use_merge` is inert but deliberate — see the docstring and
        tests/test_merge_is_inert.py — so it has to survive this refactor."""
        assert opt._gepa_extra_kwargs() == {"use_merge": True}

    def test_run_kwargs_are_merged_over_the_constants(self):
        """A run that explicitly asks for something must win over the defaults."""
        opt._GEPA_RUN_KWARGS["use_merge"] = False
        opt._GEPA_RUN_KWARGS["seed"] = 7
        assert opt._gepa_extra_kwargs() == {"use_merge": False, "seed": 7}


class TestSignatureFilter:
    """The filter is what turns 'gepa renamed an argument' into 'not passed' rather
    than a TypeError nine hours in. It has to keep holding for the new keys."""

    def test_unknown_keys_are_dropped_not_passed(self, monkeypatch):
        import gepa

        seen = {}

        def fake_optimize(**kwargs):
            seen.update(kwargs)
            return "result"

        monkeypatch.setattr(gepa, "optimize", fake_optimize)
        monkeypatch.setitem(opt._GEPA_PATCH_STATE, "optimize", False)
        opt._GEPA_RUN_KWARGS["definitely_not_a_gepa_argument"] = 1
        opt._patch_gepa_optimize()
        try:
            gepa.optimize(seed_candidate={})
        finally:
            opt._GEPA_PATCH_STATE["optimize"] = False

        assert "definitely_not_a_gepa_argument" not in seen

    def test_seed_and_stop_callbacks_are_real_gepa_arguments(self):
        """If either stops being accepted, the filter drops it silently and early
        stopping quietly stops happening. Fail loudly here instead."""
        import inspect

        import gepa

        params = set(inspect.signature(gepa.optimize).parameters)
        assert {"seed", "stop_callbacks"} <= params

    def test_adk_does_not_forward_these_itself(self):
        """The injection is only correct while ADK leaves them unset — caller-supplied
        values win, so if ADK started forwarding a seed we would be a no-op, not a bug,
        but the docs claiming we control it would be wrong."""
        import inspect

        from google.adk.optimization import gepa_root_agent_prompt_optimizer as m

        src = inspect.getsource(m)
        call = src[src.index("gepa.optimize(") :]
        call = call[: call.index(")\n")]
        assert "seed=" not in call
        assert "stop_callbacks=" not in call


class TestSeedDerivation:
    def test_distinct_arms_get_distinct_seeds(self):
        assert opt.seed_for_arm("c10-on-r1") != opt.seed_for_arm("c10-on-r2")

    def test_seed_is_stable_across_processes(self):
        """`hash()` is salted by PYTHONHASHSEED and would differ between the deploy and
        optimize stages of one campaign. md5 does not, and this is the test that would
        have caught a switch to the builtin."""
        code = (
            "from wrangler.optimize.optimizer import seed_for_arm; print(seed_for_arm('c10-on-r1'))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=True,
                env={"PYTHONHASHSEED": str(n), "PATH": "/usr/bin:/bin"},
            ).stdout.strip()
            for n in (0, 1, 2)
        }
        assert len(runs) == 1, f"seed varied with PYTHONHASHSEED: {runs}"

    def test_seed_is_a_plain_int(self):
        """gepa annotates `seed: int`; a numpy or str type would fail deep inside."""
        assert type(opt.seed_for_arm("x")) is int


class TestStopperConstruction:
    def test_uses_gepas_own_stopper_class(self):
        """Not a local lookalike: gepa type-checks nothing here, so a duck-typed
        stand-in would work until gepa's engine started introspecting `stoppers`
        (which it already does, to find a MaxMetricCallsStopper)."""
        from gepa.utils.stop_condition import NoImprovementStopper

        stopper = NoImprovementStopper(15)
        assert isinstance(stopper, NoImprovementStopper)
        assert stopper.max_iterations_without_improvement == 15

    def test_a_stopper_at_the_recommended_patience_fires_on_a_plateau(self):
        """Guards the case the replay warned about: the stopper swallows exceptions
        and returns False, so a broken one looks like 'never converged'."""
        from gepa.utils.stop_condition import NoImprovementStopper

        class View:
            program_full_scores_val_set: ClassVar[list[float]] = [0.9]

        stopper = NoImprovementStopper(15)
        fired = [stopper(View()) for _ in range(20)]
        assert fired[15] is True
        assert not any(fired[:15])
