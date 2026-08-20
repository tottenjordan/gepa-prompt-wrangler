"""Tests for wrangler.orchestration.stages — modular pipeline stage functions."""

import pytest
import yaml

from wrangler.core.factory import AgentPromptPair, Manifest
from wrangler.orchestration.stages import _filter_pairs, _resolve_eval_path


def _make_manifest(pairs=None):
    if pairs is None:
        pairs = [
            AgentPromptPair(id="flash", model="gemini-3.5-flash", system_prompt="Be helpful."),
            AgentPromptPair(id="sonnet", model="claude-sonnet-4-6", system_prompt="Be thorough."),
        ]
    return Manifest(
        name="test",
        description="",
        agent_module="agents/example_agent",
        eval_data="eval_data/test.yaml",
        pairs=pairs,
    )


class TestFilterPairs:
    def test_returns_all_when_no_filter(self):
        m = _make_manifest()
        result = _filter_pairs(m, None)
        assert len(result) == 2

    def test_filters_by_id(self):
        m = _make_manifest()
        result = _filter_pairs(m, "flash")
        assert len(result) == 1
        assert result[0].id == "flash"

    def test_unknown_id_raises(self):
        m = _make_manifest()
        with pytest.raises(KeyError):
            _filter_pairs(m, "nonexistent")


class TestResolveEvalPath:
    def test_resolves_from_manifest_dir(self, tmp_path):
        eval_file = tmp_path / "eval_data" / "test.yaml"
        eval_file.parent.mkdir(parents=True)
        eval_file.write_text("eval_cases: []")

        m = _make_manifest()
        result = _resolve_eval_path(m, manifest_dir=tmp_path)
        assert result == eval_file

    def test_falls_back_to_raw_path(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        m = _make_manifest()
        result = _resolve_eval_path(m, manifest_dir=empty_dir)
        assert str(result) == "eval_data/test.yaml"


class TestStageDeployWithExperiment:
    def _create_experiment(self, tmp_path):
        from wrangler.orchestration.experiment import Experiment

        manifest_data = {
            "name": "test-deploy",
            "agent_module": "agents/example_agent",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {
                    "id": "flash",
                    "model": "gemini-3.5-flash",
                    "system_prompt": "Be helpful.",
                    "engine_id": "existing-123",
                },
            ],
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_data))
        return Experiment.create(
            str(manifest_path), name="test-deploy", base_dir=str(tmp_path / "experiments")
        )

    def test_reuses_existing_engine_id(self, tmp_path):
        from wrangler.orchestration.stages import stage_deploy

        exp = self._create_experiment(tmp_path)
        stage_deploy(exp)

        deploy_data = exp.read_stage("deploy")
        assert deploy_data["flash"]["engine_id"] == "existing-123"
        assert deploy_data["flash"]["source"] == "config"


class TestStageEvalGating:
    def test_eval_before_blocked_without_deploy(self, tmp_path):
        from wrangler.orchestration.experiment import Experiment
        from wrangler.orchestration.stages import stage_eval

        manifest_data = {
            "name": "test-gate",
            "agent_module": "agents/example_agent",
            "eval_data": "eval_data/test.yaml",
            "pairs": [{"id": "flash", "model": "gemini-3.5-flash", "system_prompt": "Hi"}],
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_data))
        exp = Experiment.create(
            str(manifest_path), name="test-gate", base_dir=str(tmp_path / "experiments")
        )

        stage_eval(exp, phase="before")
        assert exp.read_stage("eval_before") == {}
