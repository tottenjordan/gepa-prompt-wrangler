"""Tests for experiment management and stage functions."""

import json

import pytest
import yaml

from wrangler.core.factory import Manifest
from wrangler.orchestration.experiment import STAGE_GATES, STAGES, Experiment


@pytest.fixture
def manifest_yaml(tmp_path):
    """Create a minimal manifest YAML for testing."""
    manifest = {
        "name": "test-experiment",
        "description": "Test experiment",
        "agent_module": "agents/test_agent",
        "eval_data": "eval_data/test.yaml",
        "pairs": [
            {
                "id": "pair-a",
                "model": "gemini-3.5-flash",
                "system_prompt": "You are a helpful assistant.",
                "agent_module": "agents/a_agent.py",
                "engine_id": "1111111111",
            },
            {
                "id": "pair-b",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a helpful assistant.",
                "agent_module": "agents/b_agent.py",
                "engine_id": "2222222222",
            },
        ],
        "eval_config": {"judge_model": "gemini-3.5-flash"},
    }
    path = tmp_path / "manifest.yaml"
    with open(path, "w") as f:
        yaml.dump(manifest, f)
    return path


@pytest.fixture
def experiment(manifest_yaml, tmp_path):
    """Create a fresh experiment from manifest."""
    return Experiment.create(
        manifest_yaml, name="test-exp", version="v1", base_dir=tmp_path / "experiments"
    )


class TestExperimentCreate:
    def test_creates_directory_structure(self, experiment):
        assert experiment.dir.exists()
        assert (experiment.dir / "config.yaml").exists()
        assert (experiment.dir / "manifest.json").exists()
        assert (experiment.dir / "stages").is_dir()
        assert (experiment.dir / "reports").is_dir()
        assert (experiment.dir / "images").is_dir()

    def test_config_yaml_content(self, experiment):
        cfg = experiment.config
        assert cfg["experiment"]["name"] == "test-exp"
        assert cfg["experiment"]["version"] == "v1"
        assert len(cfg["pairs"]) == 2
        assert cfg["pairs"][0]["id"] == "pair-a"
        assert cfg["pairs"][1]["id"] == "pair-b"
        assert cfg["agent_module"] == "agents/test_agent"
        assert cfg["eval_data"] == "eval_data/test.yaml"

    def test_manifest_json_initialized(self, experiment):
        with open(experiment.dir / "manifest.json") as f:
            tracking = json.load(f)
        assert tracking["experiment"] == "test-exp"
        assert tracking["version"] == "v1"
        for stage in STAGES:
            assert tracking["stages"][stage]["status"] == "pending"

    def test_duplicate_raises(self, experiment, manifest_yaml, tmp_path):
        with pytest.raises(FileExistsError):
            Experiment.create(manifest_yaml, name="test-exp", base_dir=tmp_path / "experiments")

    def test_default_name_from_manifest(self, manifest_yaml, tmp_path):
        exp = Experiment.create(manifest_yaml, base_dir=tmp_path / "exp2")
        assert exp.name == "test-experiment"

    def test_default_version(self, manifest_yaml, tmp_path):
        exp = Experiment.create(manifest_yaml, base_dir=tmp_path / "exp3")
        assert exp.version == "wrangler_v1"


class TestExperimentLoad:
    def test_load_existing(self, experiment):
        loaded = Experiment.load(experiment.dir)
        assert loaded.name == experiment.name
        assert loaded.version == experiment.version
        assert loaded.dir == experiment.dir

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Experiment.load(tmp_path / "nonexistent")


class TestExperimentManifest:
    def test_manifest_property(self, experiment):
        m = experiment.manifest
        assert isinstance(m, Manifest)
        assert m.name == "test-exp"
        assert len(m.pairs) == 2
        assert m.pairs[0].id == "pair-a"
        assert m.pairs[0].model == "gemini-3.5-flash"
        assert m.pairs[1].id == "pair-b"
        assert m.agent_module == "agents/test_agent"
        assert m.eval_data == "eval_data/test.yaml"

    def test_pair_ids(self, experiment):
        assert experiment.pair_ids == ["pair-a", "pair-b"]


class TestStageIO:
    def test_read_empty_stage(self, experiment):
        assert experiment.read_stage("deploy") == {}

    def test_write_and_read_stage(self, experiment):
        data = {"pair-a": {"engine_id": "123"}, "pair-b": {"engine_id": "456"}}
        experiment.write_stage("deploy", data)
        assert experiment.read_stage("deploy") == data

    def test_merge_pair(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        data = experiment.read_stage("deploy")
        assert data["pair-a"]["engine_id"] == "123"

        experiment.merge_pair("deploy", "pair-b", {"engine_id": "456"})
        data = experiment.read_stage("deploy")
        assert "pair-a" in data
        assert "pair-b" in data

    def test_merge_pair_overwrites(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "old"})
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "new"})
        data = experiment.read_stage("deploy")
        assert data["pair-a"]["engine_id"] == "new"

    def test_stage_path(self, experiment):
        assert experiment.stage_path("deploy") == experiment.dir / "stages" / "deploy.json"


class TestTracking:
    def test_update_tracking_partial(self, experiment):
        experiment.update_tracking("deploy", "pair-a", "complete")
        tracking = experiment._read_tracking()
        assert tracking["stages"]["deploy"]["status"] == "partial"
        assert tracking["stages"]["deploy"]["pairs"]["pair-a"]["status"] == "complete"

    def test_update_tracking_complete(self, experiment):
        experiment.update_tracking("deploy", "pair-a", "complete")
        experiment.update_tracking("deploy", "pair-b", "complete")
        tracking = experiment._read_tracking()
        assert tracking["stages"]["deploy"]["status"] == "complete"

    def test_merge_pair_updates_tracking(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        tracking = experiment._read_tracking()
        assert tracking["stages"]["deploy"]["pairs"]["pair-a"]["status"] == "complete"


class TestStatus:
    def test_status_all_pending(self, experiment):
        st = experiment.status()
        for stage in STAGES:
            assert st[stage]["status"] == "pending"
            assert st[stage]["pairs_complete"] == []
            assert len(st[stage]["pairs_remaining"]) == 2

    def test_status_after_partial(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        st = experiment.status()
        assert st["deploy"]["status"] == "partial"
        assert st["deploy"]["pairs_complete"] == ["pair-a"]
        assert st["deploy"]["pairs_remaining"] == ["pair-b"]

    def test_status_after_complete(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        experiment.merge_pair("deploy", "pair-b", {"engine_id": "456"})
        st = experiment.status()
        assert st["deploy"]["status"] == "complete"
        assert len(st["deploy"]["pairs_complete"]) == 2
        assert st["deploy"]["pairs_remaining"] == []

    def test_print_status(self, experiment, capsys):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        experiment.print_status()
        output = capsys.readouterr().out
        assert "test-exp" in output
        assert "deploy" in output


class TestPhaseGates:
    def test_deploy_has_no_gate(self, experiment):
        ok, msg = experiment.check_gate("deploy")
        assert ok

    def test_eval_before_needs_deploy(self, experiment):
        ok, msg = experiment.check_gate("eval_before")
        assert not ok
        assert "deploy" in msg

    def test_eval_before_passes_after_deploy(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        experiment.merge_pair("deploy", "pair-b", {"engine_id": "456"})
        ok, msg = experiment.check_gate("eval_before")
        assert ok

    def test_gate_partial_deploy_fails(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        ok, msg = experiment.check_gate("eval_before")
        assert not ok
        assert "pair-b" in msg

    def test_gate_with_pair_id(self, experiment):
        experiment.merge_pair("deploy", "pair-a", {"engine_id": "123"})
        ok, msg = experiment.check_gate("eval_before", pair_id="pair-a")
        assert ok

        ok, msg = experiment.check_gate("eval_before", pair_id="pair-b")
        assert not ok

    def test_optimize_needs_eval_before(self, experiment):
        ok, msg = experiment.check_gate("optimize")
        assert not ok
        assert "eval_before" in msg

    def test_all_gates_defined(self):
        assert STAGE_GATES["eval_before"] == "deploy"
        assert STAGE_GATES["optimize"] == "eval_before"
        assert STAGE_GATES["redeploy"] == "optimize"
        assert STAGE_GATES["eval_after"] == "redeploy"
        assert STAGE_GATES["report"] == "eval_after"


class TestCLIExperiment:
    def test_experiment_create(self, manifest_yaml, tmp_path):
        from click.testing import CliRunner

        from wrangler.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "experiment",
                "create",
                str(manifest_yaml),
                "--name",
                "cli-test",
                "--dir",
                str(tmp_path / "exp"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Created experiment" in result.output
        assert (tmp_path / "exp" / "cli-test" / "config.yaml").exists()

    def test_status_command(self, experiment):
        from click.testing import CliRunner

        from wrangler.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["status", str(experiment.dir)])
        assert result.exit_code == 0, result.output
        assert "test-exp" in result.output
        assert "deploy" in result.output
