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


class TestSaveOptimizedPrompt:
    """The save path appends text, so it must not emit a key that already exists."""

    def _setup(self, tmp_path):
        from wrangler.orchestration.experiment import Experiment

        # An agent_module name that does not exist at the repo root, so
        # _manifest_dir() resolves to tmp_path rather than the real project.
        (tmp_path / "agents" / "zz_test_agent").mkdir(parents=True)
        prompts_file = tmp_path / "prompts" / "zz_test_prompts.py"
        prompts_file.parent.mkdir()
        prompts_file.write_text('GENERIC = ""\n\nOPTIMIZED = {\n}\n')

        manifest_data = {
            "name": "test-save",
            "agent_module": "agents/zz_test_agent",
            "eval_data": "eval_data/test.yaml",
            "pairs": [{"id": "flash", "model": "gemini-3.5-flash", "system_prompt": "Hi"}],
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_data))
        exp = Experiment.create(
            str(manifest_path),
            name="test-save",
            base_dir=str(tmp_path / "experiments"),
            version="wrangler_v5",
        )
        return exp, prompts_file

    def test_repeated_version_does_not_duplicate_key(self, tmp_path):
        import ast

        from wrangler.orchestration.stages import _save_optimized_prompt

        exp, prompts_file = self._setup(tmp_path)
        pair = exp.manifest.pairs[0]

        _save_optimized_prompt(exp, pair, "first prompt")
        _save_optimized_prompt(exp, pair, "second prompt")

        tree = ast.parse(prompts_file.read_text())
        keys = [
            k.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "OPTIMIZED" for t in node.targets)
            for k in node.value.keys
        ]
        assert keys == ["wrangler_v5", "wrangler_v5_2"]

    def test_prompts_dir_found_beside_agents_not_at_project_root(self, tmp_path):
        """The real layout nests `agents/` and `prompts/` under an agent dir.

        `_manifest_dir()` returns the *project root* — the dir the manifest's
        relative paths resolve against — but `prompts/` lives beside `agents/`,
        one level down. Resolving it against the root alone silently found
        nothing and discarded the optimized prompt with a warning. The smoke
        test on 2026-08-21 lost its GEPA result that way.
        """
        from wrangler.orchestration.experiment import Experiment
        from wrangler.orchestration.stages import _save_optimized_prompt

        (tmp_path / "zz_proj" / "agents" / "zz_test_agent").mkdir(parents=True)
        prompts_file = tmp_path / "zz_proj" / "prompts" / "zz_test_prompts.py"
        prompts_file.parent.mkdir()
        prompts_file.write_text('GENERIC = ""\n\nOPTIMIZED = {\n}\n')

        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(
            yaml.dump(
                {
                    "name": "test-nested",
                    "agent_module": "zz_proj/agents/zz_test_agent",
                    "eval_data": "eval_data/test.yaml",
                    "pairs": [{"id": "flash", "model": "gemini-3.5-flash", "system_prompt": "Hi"}],
                }
            )
        )
        exp = Experiment.create(
            str(manifest_path),
            name="test-nested",
            base_dir=str(tmp_path / "experiments"),
            version="wrangler_v5",
        )

        _save_optimized_prompt(exp, exp.manifest.pairs[0], "the optimized prompt")

        namespace = {}
        exec(compile(prompts_file.read_text(), str(prompts_file), "exec"), namespace)  # noqa: S102
        assert namespace["OPTIMIZED"]["wrangler_v5"]["prompt"] == "the optimized prompt"

    def test_both_prompts_remain_loadable(self, tmp_path):
        from wrangler.orchestration.stages import _save_optimized_prompt

        exp, prompts_file = self._setup(tmp_path)
        pair = exp.manifest.pairs[0]

        _save_optimized_prompt(exp, pair, "first prompt")
        _save_optimized_prompt(exp, pair, "second prompt")

        namespace = {}
        exec(compile(prompts_file.read_text(), str(prompts_file), "exec"), namespace)  # noqa: S102
        optimized = namespace["OPTIMIZED"]
        assert optimized["wrangler_v5"]["prompt"] == "first prompt"
        assert optimized["wrangler_v5_2"]["prompt"] == "second prompt"


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


class TestCoveragePersisted:
    """Coverage must survive into the artifact, not just the console.

    `run_batch_eval` prints an UNEVEN METRIC COVERAGE warning, but the stage
    record is what anyone reads afterwards — and comparing a before/after delta
    is only meaningful if both sides scored the same cases. The first sweep run
    recorded `coverage: None` because stage_eval did not persist the field.
    """

    def test_eval_stage_record_includes_coverage(self):
        import inspect

        from wrangler.orchestration import stages

        src = inspect.getsource(stages.stage_eval)
        assert '"coverage": result.coverage' in src, (
            "stage_eval must persist result.coverage into the stage record"
        )

    def test_eval_result_exposes_coverage(self):
        from wrangler.eval.evaluator import EvalResult

        assert EvalResult(coverage={"safety_v1": 3}).coverage == {"safety_v1": 3}


class TestOptimizeBudgetIsPlumbed:
    """GEPA's budget must reach the optimizer, or there is no search.

    `stage_optimize` used to call `optimize()` without `max_metric_calls`, so
    it fell through to ADK's default of 100. One generation over a 49-case
    train set costs ~100 calls, so GEPA got a single draw of variants and
    returned the seed whenever none beat it — which is exactly what the
    2026-08-21 pilot did, in 10 minutes, against a manifest asking for 150.
    """

    def test_manifest_pipeline_block_is_parsed(self):
        from wrangler.core.factory import PairFactory

        m = PairFactory.load("manifests/pipeline_sonnet_manifest.yaml")
        assert isinstance(m.pipeline.get("max_metric_calls"), int)
        assert m.pipeline["max_metric_calls"] > 100, (
            "one generation over the 49-case train set costs ~100 calls, so a "
            "budget at or below that buys no search at all"
        )

    def test_experiment_config_carries_the_budget(self, tmp_path):
        from wrangler.orchestration.experiment import Experiment

        exp = Experiment.create(
            "manifests/pipeline_sonnet_manifest.yaml",
            name="budget-check",
            base_dir=str(tmp_path),
        )
        from wrangler.core.factory import PairFactory

        expected = PairFactory.load("manifests/pipeline_sonnet_manifest.yaml").pipeline[
            "max_metric_calls"
        ]
        assert exp.config["defaults"]["max_metric_calls"] == expected, (
            "the experiment config dropped the manifest's pipeline block, which "
            "is how the budget became unreachable from the local path"
        )

    def test_stage_optimize_passes_it_through(self):
        import inspect

        from wrangler.orchestration import stages

        src = inspect.getsource(stages.stage_optimize)
        assert "max_metric_calls=max_calls" in src
        assert 'defaults", {}).get("max_metric_calls' in src
