"""Per-pair campaign factors reach the pipeline without a DAG signature change.

Campaign 09 varies rationale forwarding *between arms in one pipeline job*, and runs its
control arm in that same job. Both are per-pair properties, so they ride in the pair dict
that `deploy_pipeline` builds and `dag.py` already forwards verbatim as `pair_json`.

The reason this is a test rather than a note: the defaults must be **behaviour-preserving**.
Every existing manifest omits these keys, and a pair that silently arrives with
`forward_rationale=False` would disable ADK patch 4b for a campaign that never asked to.
"""

from __future__ import annotations

import textwrap

import pytest

from wrangler.core.factory import AgentPromptPair, PairFactory


def _manifest(tmp_path, pairs_yaml: str):
    path = tmp_path / "m.yaml"
    path.write_text(
        textwrap.dedent(f"""
        name: c09-test
        description: fixture
        agent_module: examples/multi_model_agents/agents/flash_agent
        eval_data: examples/multi_model_agents/eval_data/eval_cases.yaml
        pairs:
        {textwrap.indent(textwrap.dedent(pairs_yaml), "        ").rstrip()}
        """).lstrip()
    )
    return PairFactory.load(str(path))


class TestDefaultsArePreserving:
    def test_a_pair_omitting_both_keys_keeps_todays_behaviour(self):
        """The important one: every manifest in the repo omits these."""
        pair = AgentPromptPair(id="x", model="gemini-3.5-flash", system_prompt="p")
        assert pair.forward_rationale is True, (
            "defaulting to False would silently disable ADK patch 4b for every existing "
            "manifest -- the rationale would stop reaching GEPA's reflector with no diff"
        )
        assert pair.skip_optimize is False

    def test_loading_a_manifest_without_the_keys_still_works(self, tmp_path):
        manifest = _manifest(
            tmp_path,
            """
            - id: plain
              model: gemini-3.5-flash
              system_prompt: hello
            """,
        )
        pair = manifest.pairs[0]
        assert (pair.forward_rationale, pair.skip_optimize) == (True, False)


class TestTheFactorsRoundTrip:
    @pytest.mark.parametrize("value", [True, False])
    def test_forward_rationale_survives_the_manifest(self, tmp_path, value):
        manifest = _manifest(
            tmp_path,
            f"""
            - id: arm
              model: gemini-3.5-flash
              system_prompt: hello
              forward_rationale: {str(value).lower()}
            """,
        )
        assert manifest.pairs[0].forward_rationale is value

    def test_a_control_pair_declares_skip_optimize(self, tmp_path):
        manifest = _manifest(
            tmp_path,
            """
            - id: control
              model: gemini-3.5-flash
              system_prompt: hello
              skip_optimize: true
            """,
        )
        assert manifest.pairs[0].skip_optimize is True


class TestTheFactorsReachThePipeline:
    """`dag.py` forwards the whole pair dict as `pair_json`, so this is the only hop."""

    def test_pairs_json_carries_both_keys(self, tmp_path):
        from wrangler.pipeline import deploy_pipeline

        # NB: the ids are `rationale-on` / `rationale-off`, not bare `on` / `off`.
        # YAML 1.1 parses a bare `on` as boolean True, so `- id: on` silently becomes
        # `id: True` and the pair vanishes from any lookup by name. Campaign 09's real
        # arm ids are safe for the same reason -- only the bare keyword is truthy.
        manifest = _manifest(
            tmp_path,
            """
            - id: rationale-on
              model: gemini-3.5-flash
              system_prompt: hello
            - id: rationale-off
              model: gemini-3.5-flash
              system_prompt: hello
              forward_rationale: false
              skip_optimize: true
            """,
        )
        built = deploy_pipeline._pairs_json(manifest)
        by_id = {p["id"]: p for p in built}

        assert by_id["rationale-on"]["forward_rationale"] is True
        assert by_id["rationale-on"]["skip_optimize"] is False
        assert by_id["rationale-off"]["forward_rationale"] is False
        assert by_id["rationale-off"]["skip_optimize"] is True

    def test_the_existing_keys_are_untouched(self, tmp_path):
        """Adding fields must not drop the ones components already read."""
        from wrangler.pipeline import deploy_pipeline

        manifest = _manifest(
            tmp_path,
            """
            - id: arm
              model: gemini-3.5-flash
              system_prompt: hello
            """,
        )
        entry = deploy_pipeline._pairs_json(manifest)[0]
        for key in ("id", "model", "system_prompt", "engine_id", "agent_module", "costs"):
            assert key in entry, f"{key} disappeared from pairs_json"
