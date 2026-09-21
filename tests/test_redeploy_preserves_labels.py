"""Redeploy must not strip the labels deploy set.

CLAUDE.md's reaping policy rests on them: *"Deploy scratch engines with
`labels={"lifecycle": "ephemeral", "campaign": "<id>"}` so they can be found later, and
treat teardown as the last step of a campaign."* `wrangler engines prune` then keeps
anything without that evidence, on the grounds that an unlabelled engine might be someone
else's live work.

**Every campaign arm goes through redeploy**, and until 2026-09-21 the redeploy component
never received `engine_labels_json`. `update_agent_from_source(labels=None)` rebuilds the
config with only the ownership label and `runtimes.update()` overwrites what is there, so
`lifecycle` and `campaign` were silently dropped on the first redeploy of every arm.

Found by reading the labels off campaign 09's engines: the manifest declared
`lifecycle: ephemeral, campaign: "09"`, the pipeline submitted
`engine_labels_json={"lifecycle": "ephemeral", "campaign": "09"}`, and the deployed engines
carried only `{"solution": "promp-wrangler"}`. Eight completed-campaign engines were being
kept by the traffic heuristic because the evidence that would have released them was gone.
"""

from __future__ import annotations

import inspect

from wrangler.pipeline import components


def _source(fn) -> str:
    return inspect.getsource(fn.python_func)


class TestRedeployCarriesTheLabels:
    def test_the_component_accepts_engine_labels(self):
        params = inspect.signature(components.redeploy_single_agent.python_func).parameters
        assert "engine_labels_json" in params, (
            "redeploy does not receive engine_labels_json, so update_agent_from_source is "
            "called with labels=None and runtimes.update() strips lifecycle/campaign -- "
            "the evidence wrangler engines prune needs to reap the engine"
        )

    def test_it_forwards_them_to_the_update(self):
        src = _source(components.redeploy_single_agent)
        assert "labels=" in src.split("update_agent_from_source(")[1].split(")")[0], (
            "engine_labels_json reaches redeploy but is not passed to update_agent_from_source"
        )

    def test_the_ownership_label_is_still_merged_underneath(self):
        """An engine that loses `solution` becomes unreapable by a different route."""
        src = _source(components.redeploy_single_agent)
        assert '"solution": "promp-wrangler"' in src


class TestTheDagWiresItThrough:
    def test_the_redeploy_task_receives_engine_labels_json(self):
        from pathlib import Path

        dag = Path("wrangler/pipeline/dag.py").read_text()
        redeploy_block = dag.split("redeploy_task = comps[")[1].split("redeploy_task.set_")[0]
        assert "engine_labels_json" in redeploy_block, (
            "dag.py forwards engine_labels_json to deploy but not to redeploy"
        )


class TestDeployStillWorks:
    """The deploy path was correct; adding redeploy must not disturb it."""

    def test_deploy_still_merges_manifest_labels_over_the_default(self):
        src = _source(components.deploy_single_agent)
        assert "engine_labels_json" in src
        assert '"solution": "promp-wrangler"' in src
