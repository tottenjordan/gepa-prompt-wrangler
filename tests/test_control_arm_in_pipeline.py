"""A control arm must run in the SAME pipeline job as the arms it calibrates.

CLAUDE.md requires every sweep to carry an arm whose prompt does not change, run "under
identical conditions" as the real arms. Until now `skip_optimize` was a **pipeline-level**
parameter, so satisfying that meant a second pipeline job — different submit, different
container scheduling, and a floor measured somewhere other than where it is applied.

Campaign 09 needs all three arms in one job. The mechanism is a short-circuit inside the
optimize component rather than a `dsl.If` on a `ParallelFor` loop item, which is fragile
across KFP versions.

**The control arm is not "optimize, but skipped".** It must return its prompt byte-identical,
because `PairAnalysis.is_control` detects a control by `original_prompt == optimized_prompt`
— and because redeploy and eval_after then run against the same prompt, which is exactly
what makes the arm measure the floor.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from wrangler.pipeline import components


def _component_source() -> str:
    """The optimize component's body, as KFP will serialize it."""
    return inspect.getsource(components.optimize_single_agent.python_func)


class TestTheShortCircuitExists:
    def test_the_component_reads_the_per_pair_flag(self):
        source = _component_source()
        assert "skip_optimize" in source, (
            "the optimize component does not consult the per-pair skip_optimize flag, so a "
            "control arm cannot share a pipeline job with the arms it calibrates"
        )

    def test_it_short_circuits_before_calling_gepa(self):
        """The guard must precede the optimize() call, or it costs ~10 h to skip nothing."""
        source = _component_source()
        guard = source.find("skip_optimize")
        gepa_call = source.find("optimized_prompt = optimize(")
        assert guard != -1, "skip_optimize not found in the component body"
        assert gepa_call != -1, "the optimize() call site moved; re-anchor this test"
        assert guard < gepa_call, (
            "skip_optimize is consulted after optimize() is called — a control arm would "
            "pay for a full GEPA run and then discard it"
        )

    def test_the_component_still_parses(self):
        """KFP serializes the body; a syntax error here fails nine hours in, not at import."""
        ast.parse(inspect.getsource(components.optimize_single_agent.python_func))


class TestTheControlArmContract:
    def test_the_prompt_is_returned_unchanged(self):
        """Byte-identical, not 'equivalent' — is_control compares with ==."""
        source = _component_source()
        assert "return original_prompt" in source, (
            "the control path must return the ORIGINAL prompt object so that "
            "original_prompt == optimized_prompt and PairAnalysis.is_control fires"
        )

    def test_is_control_detects_an_unchanged_prompt(self):
        """The downstream half of the contract, asserted rather than assumed."""
        from wrangler.reporting.analyzer import PairAnalysis

        same = PairAnalysis(
            pair_id="c", model="m", original_prompt="hello", optimized_prompt="hello"
        )
        changed = PairAnalysis(
            pair_id="o", model="m", original_prompt="hello", optimized_prompt="hello!"
        )
        assert same.is_control is True
        assert changed.is_control is False

    def test_an_empty_prompt_is_not_a_control_arm(self):
        """Guards the degenerate case: nothing recorded must not masquerade as unchanged."""
        from wrangler.reporting.analyzer import PairAnalysis

        blank = PairAnalysis(pair_id="x", model="m", original_prompt="", optimized_prompt="")
        assert blank.is_control is False

    def test_the_control_path_still_writes_a_stage_artifact(self):
        """Without it the arm is invisible to the analyzer and to `wrangler report`."""
        source = _component_source()
        head = source[: source.find("optimized_prompt = optimize(")]
        assert "stages/optimize" in head, (
            "the short-circuit returns without writing stages/optimize/<pair>.json, so the "
            "control arm would not appear in the report that needs it"
        )


class TestTheExistingPipelineLevelFlagStillWorks:
    def test_the_dag_still_branches_on_the_pipeline_parameter(self):
        """Existing manifests use the pipeline-level skip_optimize; do not break them."""
        dag_source = Path("wrangler/pipeline/dag.py").read_text()
        assert "skip_optimize == False" in dag_source, (
            "the pipeline-level skip_optimize branch disappeared — manifests that use it "
            "would silently start running a ~10 h optimize stage"
        )
