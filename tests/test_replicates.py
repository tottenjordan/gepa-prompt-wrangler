"""Tests for `replicates: N` in a manifest.

A replicate is deliberately just another pair, so most of the value here is in proving
that nothing *else* changed: every existing manifest must produce a byte-identical pair
list, because the pair-id list feeds `run_id` and a stray suffix would invalidate the
KFP cache of every campaign in flight.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wrangler.core.factory import AgentPromptPair, PairFactory

MANIFESTS = Path(__file__).parent.parent / "manifests"


def _write(tmp_path, pairs, name="m.yaml"):
    doc = {
        "name": "t",
        "agent_module": "agents.example_agent",
        "eval_data": "data/eval.yaml",
        "pairs": pairs,
    }
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc))
    return path


BASE = {"id": "arm", "model": "gemini-3.5-flash", "system_prompt": "be helpful"}


class TestBackwardCompatibility:
    @pytest.mark.parametrize("entry", [BASE, {**BASE, "replicates": 1}])
    def test_one_replicate_leaves_the_id_untouched(self, tmp_path, entry):
        """No suffix, no new pair. `replicates: 1` and omitting it must be identical."""
        manifest = PairFactory.load(_write(tmp_path, [entry]))
        assert [p.id for p in manifest.pairs] == ["arm"]
        assert manifest.pairs[0].replicate_of == ""

    def test_every_checked_in_manifest_is_unaffected(self):
        """The load-bearing regression guard: no shipped manifest uses `replicates`,
        so every one of them must still produce the pair ids it produced before."""
        for path in sorted(MANIFESTS.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text())
            expected = [
                entry.get("id", f"pair-{i + 1}") for i, entry in enumerate(raw.get("pairs", []))
            ]
            assert [p.id for p in PairFactory.load(path).pairs] == expected, path.name


class TestExpansion:
    def test_two_replicates_become_two_pairs(self, tmp_path):
        manifest = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2}]))
        assert [p.id for p in manifest.pairs] == ["arm-r1", "arm-r2"]

    def test_replicates_differ_only_in_identity(self, tmp_path):
        """Everything that defines the *condition* must be shared, or the replicates
        are measuring two different things."""
        manifest = PairFactory.load(
            _write(tmp_path, [{**BASE, "replicates": 3, "description": "d", "tags": ["x"]}])
        )
        varying = {"id", "replicate_of"}
        fields = {f for f in AgentPromptPair.__dataclass_fields__ if f not in varying}
        first = manifest.pairs[0]
        for other in manifest.pairs[1:]:
            for f in fields:
                assert getattr(first, f) == getattr(other, f), f

    def test_replicate_of_records_the_shared_condition(self, tmp_path):
        """Grouping replicates must not depend on a regex over ids users also hand-pick."""
        manifest = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2}]))
        assert {p.replicate_of for p in manifest.pairs} == {"arm"}

    def test_a_control_arm_replicates_too(self, tmp_path):
        """A replicated control is a better floor estimate and is explicitly wanted;
        `skip_optimize` must carry across."""
        manifest = PairFactory.load(
            _write(tmp_path, [{**BASE, "replicates": 2, "skip_optimize": True}])
        )
        assert all(p.skip_optimize for p in manifest.pairs)
        assert len(manifest.pairs) == 2

    def test_disabled_replicated_pairs_stay_out_of_enabled_pairs(self, tmp_path):
        """`enabled_pairs` is what the pipeline reads; expansion must not smuggle a
        disabled condition back in."""
        manifest = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2, "enabled": False}]))
        assert len(manifest.pairs) == 2
        assert manifest.enabled_pairs == []


class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1, 2.5, "2", True])
    def test_rejects_non_positive_integers(self, tmp_path, bad):
        with pytest.raises(ValueError, match="replicates"):
            PairFactory.load(_write(tmp_path, [{**BASE, "replicates": bad}]))

    def test_rejects_replicates_with_a_pinned_engine(self, tmp_path):
        """All replicates would evaluate one deployment, measuring the opposite of the
        search variance they exist to sample."""
        with pytest.raises(ValueError, match="engine_id"):
            PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2, "engine_id": "123"}]))

    def test_error_names_the_pair(self, tmp_path):
        """Same rule as the temperature check: fail where the message can say which."""
        with pytest.raises(ValueError, match="'arm'"):
            PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 0}]))


class TestDownstreamPlumbing:
    def test_replicates_reach_the_pipeline_payload(self, tmp_path):
        """`_pairs_json` iterates enabled_pairs, so expansion needs no DAG change."""
        from wrangler.pipeline.deploy_pipeline import _pairs_json

        manifest = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2}]))
        assert [p["id"] for p in _pairs_json(manifest)] == ["arm-r1", "arm-r2"]

    def test_replicates_change_the_run_id_inputs(self, tmp_path):
        """run_id hashes the pair-id list, so replicates key their own artifacts and
        cannot overwrite each other in GCS."""
        one = PairFactory.load(_write(tmp_path, [BASE], "a.yaml"))
        two = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 2}], "b.yaml"))
        assert [p.id for p in one.enabled_pairs] != [p.id for p in two.enabled_pairs]

    def test_each_replicate_gets_a_distinct_gepa_seed(self, tmp_path):
        """The whole point: distinct ids produce distinct search schedules."""
        from wrangler.optimize.optimizer import seed_for_arm

        manifest = PairFactory.load(_write(tmp_path, [{**BASE, "replicates": 3}]))
        seeds = {seed_for_arm(p.id) for p in manifest.pairs}
        assert len(seeds) == 3
