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


class TestEngineTierComesFromTheModelNotTheId:
    """The tier written to .env is a property of the model a pair runs, not of
    how someone spelled the pair's id.

    The first fix here replaced a substring scan over ENGINE_LABELS with a
    token-boundary scan (`_match_engine_label`), which stopped "generic-prompt"
    from stealing PRO_ENGINE_ID. But it just relocated the bug: matching is
    still driven by whatever words happen to appear in the id, so two ids
    naming the *same* comparison can still disagree --

        "sonnet-vs-opus"      -> tokens [sonnet, vs, opus]      -> last match "opus"
        "opus-sonnet-compare" -> tokens [opus, sonnet, compare] -> last match "sonnet"

    -- and "vs"/"compare" is exactly this project's naming vocabulary for a
    control-arm comparison. The registry already knows a model's tier
    authoritatively (`get_spec(model).alias`), so `_engine_tier_for_model`
    derives it from `pair.model` instead and the id never enters the decision
    at all -- removing the whole class of bug rather than relocating it.
    """

    def test_the_tier_comes_from_the_models_alias(self):
        from wrangler.orchestration import stages

        assert stages._engine_tier_for_model("gemini-3.1-flash-lite") == "lite"
        assert stages._engine_tier_for_model("gemini-3.5-flash") == "flash"
        assert stages._engine_tier_for_model("gemini-3.1-pro-preview") == "pro"
        assert stages._engine_tier_for_model("claude-sonnet-4-6") == "sonnet"
        assert stages._engine_tier_for_model("claude-opus-4-6") == "opus"

    def test_two_differently_worded_ids_for_the_same_model_now_agree(self):
        """The bug the rework exists to remove: same model, different id
        wording, used to be able to disagree. Deriving from the model instead
        of the id makes the id irrelevant to the outcome, so there is nothing
        left for two wordings to disagree about.
        """
        from wrangler.orchestration import stages

        # "sonnet-vs-opus" and "opus-sonnet-compare" would have differed under
        # id-string matching; the id is not even a parameter here anymore.
        assert stages._engine_tier_for_model("claude-opus-4-6") == "opus"
        assert stages._engine_tier_for_model("claude-opus-4-6") == "opus"

    def test_flash_lite_still_resolves_to_lite(self):
        """gemini-3.1-flash-lite is registered under alias "lite", not "flash"."""
        from wrangler.orchestration import stages

        assert stages._engine_tier_for_model("gemini-3.1-flash-lite") == "lite"

    def test_a_versioned_alias_falls_back_to_its_family_tier(self):
        """claude-sonnet-5's alias is "sonnet5" -- there is no SONNET5_ENGINE_ID,
        only SONNET_ENGINE_ID. Campaign 07 runs claude-sonnet-5, so a health-gate
        reroll on that pair must still land on the one .env var this project's
        five-tier vocabulary actually has for the Sonnet family, or that var
        goes stale exactly the way this module exists to prevent.

        The same fallback -- strip the trailing version digits, check that
        against ENGINE_LABELS -- covers every other versioned alias in the
        registry (opus47, opus48, opus5, lite35, flash36) without a hand-built
        per-alias table that would need updating for every new model version.
        """
        from wrangler.orchestration import stages

        assert stages._engine_tier_for_model("claude-sonnet-5") == "sonnet"
        assert stages._engine_tier_for_model("claude-opus-4-7") == "opus"
        assert stages._engine_tier_for_model("claude-opus-4-8") == "opus"
        assert stages._engine_tier_for_model("claude-opus-5") == "opus"
        assert stages._engine_tier_for_model("gemini-3.5-flash-lite") == "lite"
        assert stages._engine_tier_for_model("gemini-3.6-flash") == "flash"

    def test_fable_is_a_sixth_family_with_no_env_tier_of_its_own(self):
        """fable has no trailing digit and "fable" itself is not one of the five
        ENGINE_LABELS -- there is no FABLE_ENGINE_ID to guess at, so this must
        say "no tier" rather than fabricate a mapping onto an unrelated one.
        """
        from wrangler.orchestration import stages

        assert stages._engine_tier_for_model("claude-fable-5") == ""

    def test_an_unregistered_model_degrades_to_no_tier_rather_than_raising(self):
        """A deploy for an ad-hoc model id must not be taken down by this check."""
        from wrangler.orchestration import stages

        assert stages._engine_tier_for_model("not-a-real-model") == ""

    def test_a_rerolled_engine_updates_the_tier_the_model_names(self, monkeypatch):
        from wrangler.core import env_ids
        from wrangler.orchestration import stages

        calls = []
        monkeypatch.setattr(env_ids, "set_engine_id", lambda label, eid: calls.append((label, eid)))
        stages._sync_env_engine_id("sonnet-vs-opus", "claude-opus-4-6", "eng-999")
        assert calls == [("opus", "eng-999")]

    def test_an_unregistered_models_pair_is_not_synced(self, monkeypatch):
        from wrangler.core import env_ids
        from wrangler.orchestration import stages

        calls = []
        monkeypatch.setattr(env_ids, "set_engine_id", lambda label, eid: calls.append((label, eid)))
        stages._sync_env_engine_id("generic-prompt-v2", "not-a-real-model", "eng-999")
        assert calls == []


class TestCollidingTierClaimsAreNotWritten:
    """The version-stripping fallback is genuinely useful with no peer -- an
    evaluator looking for "the sonnet engine" wants SONNET_ENGINE_ID to point
    at claude-sonnet-5 when that is the only Sonnet-family pair in play. The
    fallback only becomes wrong when a *peer* exists: manifests/c07-sonnet46_
    manifest.yaml (claude-sonnet-4-6, alias "sonnet") and manifests/c07-sonnet5_
    manifest.yaml (claude-sonnet-5, alias "sonnet5" -> family "sonnet") both
    write SONNET_ENGINE_ID. Last writer wins, and evaluators/trace-health then
    resolve to whichever arm rerolled most recently -- the wrong arm's .env
    entry silently overwritten, which is the exact failure class C1 exists to
    remove.

    Deleting the fallback is not the fix: it would also kill the useful no-peer
    case, and per docs/doe/07-cost-quality-frontier.md the c07-sonnet46 and
    c07-sonnet5 arms run as two separate manifests (confirmed: `wrangler run`
    and `Experiment.create` each take exactly one manifest path, and nothing
    merges two manifests' pairs into one `stage_deploy` call), so resolving
    collisions from one run's pair list cannot see this specific pairing --
    it is a real improvement for a manifest that enables two family-mates
    directly, not a complete fix for the two-manifest case. `contested` lets
    `stage_deploy` pass in a global view of what this run's enabled pairs
    claim, so a manifest that *does* enable two family-mates together is
    caught, while the ordinary no-peer case still writes.
    """

    def test_two_pairs_claiming_the_same_tier_are_not_written(self, monkeypatch):
        from wrangler.core import env_ids
        from wrangler.orchestration import stages

        calls = []
        monkeypatch.setattr(env_ids, "set_engine_id", lambda label, eid: calls.append((label, eid)))
        contested = {"sonnet": ["c07-sonnet46", "c07-sonnet5"]}
        stages._sync_env_engine_id(
            "c07-sonnet46", "claude-sonnet-4-6", "eng-A", contested=contested
        )
        stages._sync_env_engine_id("c07-sonnet5", "claude-sonnet-5", "eng-B", contested=contested)
        assert calls == []

    def test_an_uncontested_tier_still_writes(self, monkeypatch):
        """A single sonnet-5 pair with no peer must still resolve to sonnet."""
        from wrangler.core import env_ids
        from wrangler.orchestration import stages

        calls = []
        monkeypatch.setattr(env_ids, "set_engine_id", lambda label, eid: calls.append((label, eid)))
        stages._sync_env_engine_id(
            "c07-sonnet5", "claude-sonnet-5", "eng-B", contested={"sonnet": ["c07-sonnet5"]}
        )
        assert calls == [("sonnet", "eng-B")]

    def test_no_contested_map_behaves_as_the_uncontested_case(self, monkeypatch):
        """Every existing caller omits `contested`; it must not change their behaviour."""
        from wrangler.core import env_ids
        from wrangler.orchestration import stages

        calls = []
        monkeypatch.setattr(env_ids, "set_engine_id", lambda label, eid: calls.append((label, eid)))
        stages._sync_env_engine_id("sonnet-vs-opus", "claude-opus-4-6", "eng-999")
        assert calls == [("opus", "eng-999")]

    def test_stage_deploy_resolves_collisions_from_its_own_pair_list(self, tmp_path, capsys):
        """End to end: two enabled pairs in one manifest that collide on a tier."""
        from unittest.mock import patch

        from wrangler.orchestration import stages

        class _Pair:
            agent_module = "agents/x"
            system_prompt = "prompt"
            enabled = True
            disabled_reason = ""

            def __init__(self, pid, model, eid):
                self.id = pid
                self.model = model
                self.engine_id = eid

        class _Manifest:
            agent_module = "agents/x"
            eval_data = "eval.yaml"

            def __init__(self, pairs):
                self.pairs = pairs

        class _Exp:
            version = "v1"

            def __init__(self, pairs):
                self.manifest = _Manifest(pairs)
                self.config = {}
                self.dir = tmp_path
                self.written = {}

            def check_gate(self, *_a, **_k):
                return True, ""

            def read_stage(self, _stage):
                return {}

            def merge_pair(self, stage, pid, data):
                self.written.setdefault(stage, {}).setdefault(pid, {}).update(data)

        exp = _Exp(
            [
                _Pair("c07-sonnet46", "claude-sonnet-4-6", ""),
                _Pair("c07-sonnet5", "claude-sonnet-5", ""),
            ]
        )

        def _deploy(agent_module, model, instruction, display_name):
            return "eng-bad-46" if model == "claude-sonnet-4-6" else "eng-bad-5"

        def _gate(engine_id, redeploy_fn, **kw):
            good = "eng-good-46" if engine_id == "eng-bad-46" else "eng-good-5"
            return {
                "engine_id": good,
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": [engine_id],
            }

        synced = []
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", side_effect=_deploy),
            patch.object(stages, "gate_engine_health", side_effect=_gate),
            patch(
                "wrangler.core.env_ids.set_engine_id",
                side_effect=lambda label, eid, **kw: synced.append((label, eid)) or True,
            ),
            patch("wrangler.tools.engines.delete_engine"),
        ):
            stages.stage_deploy(exp)

        assert synced == []
        out = capsys.readouterr().out
        assert "c07-sonnet46" in out
        assert "c07-sonnet5" in out
