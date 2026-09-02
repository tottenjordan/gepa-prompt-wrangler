"""Tests for the deploy-stage engine health gate.

Campaign 01 measured ten byte-identical engines at 0% to 100% reach: six
effectively perfect, two effectively dead, two in between. A dead engine returns
HTTP 200 with no inference rather than an error, so nothing downstream notices
until a third of an eval run has quietly gone missing — and every delta computed
from it is measuring dropout.

Phase B showed redeploying in place redraws the rate, so a bad engine is both
detectable in ~60 one-line requests and fixable. Leaving that as a command
someone has to remember is how it stays unused.
"""

from unittest.mock import patch

from wrangler.orchestration import stages


class _Probe:
    """Scripted gate outcomes, one per probe call."""

    def __init__(self, rates):
        self.rates = list(rates)
        self.calls = []

    def __call__(self, engine_id, n=60, threshold=0.8, **kw):
        self.calls.append(engine_id)
        rate = self.rates.pop(0) if self.rates else 1.0
        return {
            "reached": int(rate * n),
            "n": n,
            "rate": rate,
            "ci_low": max(0.0, rate - 0.1),
            "ci_high": min(1.0, rate + 0.1),
            "threshold": threshold,
            "passed": rate >= threshold,
        }


class TestGateHealthyEngine:
    def test_a_healthy_engine_passes_without_redeploying(self):
        probe = _Probe([0.98])
        redeploys = []
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: redeploys.append(1) or "eng1", probe_fn=probe
        )
        assert out["passed"] is True
        assert out["rerolls"] == 0
        assert redeploys == []

    def test_the_health_record_is_returned_for_persisting(self):
        out = stages.gate_engine_health("eng1", redeploy_fn=lambda: "eng1", probe_fn=_Probe([0.98]))
        for key in ("passed", "rate", "n", "rerolls", "engine_id"):
            assert key in out


class TestGateRerolls:
    def test_a_bad_engine_is_redeployed_and_reprobed(self):
        probe = _Probe([0.05, 0.97])
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: "eng1", probe_fn=probe, max_rerolls=2
        )
        assert out["rerolls"] == 1
        assert out["passed"] is True
        assert len(probe.calls) == 2

    def test_rerolling_stops_at_the_budget(self):
        """A reroll is a fresh draw from the same distribution, not a repair."""
        probe = _Probe([0.0, 0.1, 0.2, 0.3])
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: "eng1", probe_fn=probe, max_rerolls=2
        )
        assert out["rerolls"] == 2
        assert out["passed"] is False
        assert len(probe.calls) == 3  # initial + 2 rerolls

    def test_a_failing_gate_does_not_raise(self):
        """The deploy still happened. Report it and let the run decide."""
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: "eng1", probe_fn=_Probe([0.0]), max_rerolls=0
        )
        assert out["passed"] is False

    def test_a_redeploy_that_returns_a_new_id_is_followed(self):
        probe = _Probe([0.0, 0.99])
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: "eng2", probe_fn=probe, max_rerolls=1
        )
        assert out["engine_id"] == "eng2"
        assert probe.calls == ["eng1", "eng2"]

    def test_a_failed_redeploy_is_reported_not_raised(self):
        def _boom():
            raise RuntimeError("quota")

        out = stages.gate_engine_health(
            "eng1", redeploy_fn=_boom, probe_fn=_Probe([0.0]), max_rerolls=1
        )
        assert out["passed"] is False
        assert "quota" in out["error"]


class TestRejectedEnginesAreDisposedOf:
    """Every rejected draw leaves an engine behind unless someone deletes it.

    Two reroll rounds leak two engines per pair. On 2026-08-31 that put four
    engines behind the display name `wrangler-opus-agent-v5` and three behind
    `wrangler-lite-agent-v5`, only one of each being the live one.
    """

    def test_a_gate_created_reject_is_discarded(self):
        discarded = []
        ids = iter(["eng2", "eng3"])
        stages.gate_engine_health(
            "eng1",
            redeploy_fn=lambda: next(ids),
            probe_fn=_Probe([0.0, 0.0, 0.99]),
            max_rerolls=2,
            discard_fn=discarded.append,
        )
        # eng1 came from the caller; eng2 the gate made and then rejected.
        assert discarded == ["eng2"]

    def test_the_caller_supplied_engine_is_never_discarded(self):
        """It may be a live deployment we were only asked to check."""
        discarded = []
        stages.gate_engine_health(
            "caller-owned",
            redeploy_fn=lambda: "eng2",
            probe_fn=_Probe([0.0, 0.99]),
            max_rerolls=1,
            discard_fn=discarded.append,
        )
        assert discarded == []

    def test_rejected_ids_are_returned_so_the_caller_can_decide(self):
        out = stages.gate_engine_health(
            "eng1",
            redeploy_fn=lambda: "eng2",
            probe_fn=_Probe([0.0, 0.99]),
            max_rerolls=1,
        )
        assert out["rejected"] == ["eng1"]

    def test_a_passing_engine_leaves_nothing_rejected(self):
        out = stages.gate_engine_health("eng1", redeploy_fn=lambda: "eng2", probe_fn=_Probe([0.99]))
        assert out["rejected"] == []

    def test_a_failed_discard_does_not_abort_the_reroll(self):
        """Leaking an engine is untidy; aborting a repair over it is worse."""

        def _boom(_eid):
            raise RuntimeError("quota")

        out = stages.gate_engine_health(
            "eng1",
            redeploy_fn=lambda: "eng2",
            probe_fn=_Probe([0.0, 0.99]),
            max_rerolls=1,
            discard_fn=_boom,
        )
        assert out["passed"] is True
        assert out["engine_id"] == "eng2"

    def test_no_discard_fn_still_reports_rejects(self):
        out = stages.gate_engine_health(
            "eng1", redeploy_fn=lambda: "eng2", probe_fn=_Probe([0.0, 0.99]), max_rerolls=1
        )
        assert out["rejected"] == ["eng1"]


class TestStageDeployReapsItsOwnRejectedDraw:
    """B2: `stage_deploy` always deploys the engine it hands to the gate, so a
    rejected first draw is unambiguously its to reap -- `gate_engine_health`
    never discards the engine it was *handed* (it might be a live deployment
    the caller only wanted checked). The pipeline component and the example
    script both already reap it; this stage did not, leaking one engine per
    pair on every first-draw failure (~45% of deploys, docs/doe/09-lottery-recheck.md).
    """

    def test_a_rejected_first_draw_is_deleted(self, tmp_path):
        exp = _stub_experiment(tmp_path)
        deleted = []
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-bad"),
            patch.object(stages, "gate_engine_health") as gate,
            patch("wrangler.tools.engines.delete_engine", side_effect=deleted.append),
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": ["eng-bad"],
            }
            stages.stage_deploy(exp)
        assert deleted == ["eng-bad"]

    def test_a_passing_first_draw_deletes_nothing(self, tmp_path):
        exp = _stub_experiment(tmp_path)
        deleted = []
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-good"),
            patch.object(stages, "gate_engine_health") as gate,
            patch("wrangler.tools.engines.delete_engine", side_effect=deleted.append),
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.99,
                "n": 60,
                "rerolls": 0,
                "rejected": [],
            }
            stages.stage_deploy(exp)
        assert deleted == []

    def test_a_failed_discard_does_not_abort_the_run(self, tmp_path):
        """Leaking an engine is untidy; aborting a deploy over it is worse."""
        exp = _stub_experiment(tmp_path)

        def _boom(_eid):
            raise RuntimeError("quota")

        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-bad"),
            patch.object(stages, "gate_engine_health") as gate,
            patch("wrangler.tools.engines.delete_engine", side_effect=_boom),
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": ["eng-bad"],
            }
            stages.stage_deploy(exp)  # must not raise
        assert exp.written["deploy"]["p1"]["engine_id"] == "eng-good"


class TestGateConfig:
    def test_the_gate_is_on_by_default(self):
        assert stages.health_gate_config({})["enabled"] is True

    def test_it_can_be_turned_off(self):
        cfg = stages.health_gate_config({"health_gate": {"enabled": False}})
        assert cfg["enabled"] is False

    def test_thresholds_and_budgets_are_configurable(self):
        cfg = stages.health_gate_config(
            {"health_gate": {"attempts": 20, "threshold": 0.9, "max_rerolls": 3}}
        )
        assert (cfg["attempts"], cfg["threshold"], cfg["max_rerolls"]) == (20, 0.9, 3)

    def test_defaults_come_from_the_measured_campaign(self):
        from wrangler.tools.boot_probe import GATE_ATTEMPTS, GATE_THRESHOLD

        cfg = stages.health_gate_config({})
        assert cfg["attempts"] == GATE_ATTEMPTS
        assert cfg["threshold"] == GATE_THRESHOLD


class TestManifestCarriesTheConfig:
    """A setting the manifest cannot reach is a setting that silently does nothing.

    `max_metric_calls` was exactly this: the experiment config dropped the
    manifest's block, so the local path fell through to ADK's default and
    "optimized" in ten minutes by returning its seed.
    """

    def test_a_manifest_health_gate_block_is_parsed(self, tmp_path):
        import yaml

        from wrangler.core.factory import PairFactory

        m = tmp_path / "m.yaml"
        m.write_text(
            yaml.safe_dump(
                {
                    "name": "m",
                    "agent_module": "agents/x",
                    "eval_data": "eval.yaml",
                    "health_gate": {"enabled": False, "attempts": 20},
                    "pairs": [{"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "hi"}],
                }
            )
        )
        assert PairFactory.load(str(m)).health_gate == {"enabled": False, "attempts": 20}

    def test_an_absent_block_is_empty_not_missing(self, tmp_path):
        import yaml

        from wrangler.core.factory import PairFactory

        m = tmp_path / "m.yaml"
        m.write_text(
            yaml.safe_dump(
                {
                    "name": "m",
                    "agent_module": "agents/x",
                    "eval_data": "eval.yaml",
                    "pairs": [{"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "hi"}],
                }
            )
        )
        assert PairFactory.load(str(m)).health_gate == {}
        assert stages.health_gate_config({"health_gate": {}})["enabled"] is True


class TestStageDeployIntegration:
    def test_a_fresh_deploy_is_gated_and_the_result_persisted(self, tmp_path):
        exp = _stub_experiment(tmp_path)
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-new"),
            patch.object(stages, "gate_engine_health") as gate,
        ):
            gate.return_value = {
                "engine_id": "eng-new",
                "passed": True,
                "rate": 0.98,
                "n": 60,
                "rerolls": 0,
                "rejected": [],
            }
            stages.stage_deploy(exp)
        gate.assert_called_once()
        assert exp.written["deploy"]["p1"]["health"]["rate"] == 0.98

    def test_a_reroll_updates_the_recorded_engine_id(self, tmp_path):
        """Otherwise eval runs against the engine the gate just rejected."""
        exp = _stub_experiment(tmp_path)
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-bad"),
            patch.object(stages, "gate_engine_health") as gate,
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": ["eng-bad"],
            }
            stages.stage_deploy(exp)
        assert exp.written["deploy"]["p1"]["engine_id"] == "eng-good"

    def test_a_reroll_syncs_the_env_engine_id(self, tmp_path, monkeypatch):
        """Otherwise evaluators and trace-health keep pointing at the dead one."""
        exp = _stub_experiment(tmp_path)
        exp.manifest.pairs[0].id = "sonnet"
        synced = []
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-bad"),
            patch.object(stages, "gate_engine_health") as gate,
            patch(
                "wrangler.core.env_ids.set_engine_id",
                side_effect=lambda label, eid, **kw: synced.append((label, eid)) or True,
            ),
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": ["eng-bad"],
            }
            stages.stage_deploy(exp)
        assert synced == [("sonnet", "eng-good")]

    def test_no_reroll_means_no_env_write(self, tmp_path):
        """The id did not change; touching .env would be noise in a diff."""
        exp = _stub_experiment(tmp_path)
        exp.manifest.pairs[0].id = "sonnet"
        synced = []
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-new"),
            patch.object(stages, "gate_engine_health") as gate,
            patch(
                "wrangler.core.env_ids.set_engine_id",
                side_effect=lambda label, eid, **kw: synced.append((label, eid)) or True,
            ),
        ):
            gate.return_value = {
                "engine_id": "eng-new",
                "passed": True,
                "rate": 0.99,
                "n": 60,
                "rerolls": 0,
                "rejected": [],
            }
            stages.stage_deploy(exp)
        assert synced == []

    def test_a_pair_matching_no_tier_says_so_instead_of_guessing(self, tmp_path, capsys):
        exp = _stub_experiment(tmp_path)
        exp.manifest.pairs[0].id = "some-custom-arm"
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-bad"),
            patch.object(stages, "gate_engine_health") as gate,
        ):
            gate.return_value = {
                "engine_id": "eng-good",
                "passed": True,
                "rate": 0.97,
                "n": 60,
                "rerolls": 1,
                "rejected": ["eng-bad"],
            }
            stages.stage_deploy(exp)
        assert "matches no known model tier" in capsys.readouterr().out

    def test_the_gate_can_be_disabled(self, tmp_path):
        exp = _stub_experiment(tmp_path, config={"health_gate": {"enabled": False}})
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-new"),
            patch.object(stages, "gate_engine_health") as gate,
        ):
            stages.stage_deploy(exp)
        gate.assert_not_called()

    def test_a_preexisting_engine_is_not_gated_by_default(self, tmp_path):
        """Re-probing a reused engine on every stage run is 12 minutes each time."""
        exp = _stub_experiment(tmp_path, engine_id="eng-existing")
        with patch.object(stages, "gate_engine_health") as gate:
            stages.stage_deploy(exp)
        gate.assert_not_called()


def _stub_experiment(tmp_path, config=None, engine_id=""):
    """Minimal Experiment stand-in exercising the real stage_deploy."""

    class _Pair:
        id = "p1"
        model = "m"
        system_prompt = "prompt"
        agent_module = "agents/x"
        enabled = True
        disabled_reason = ""

        def __init__(self, eid):
            self.engine_id = eid

    class _Manifest:
        agent_module = "agents/x"
        eval_data = "eval.yaml"

        def __init__(self, pair):
            self.pairs = [pair]

    class _Exp:
        version = "v1"

        def __init__(self):
            self.manifest = _Manifest(_Pair(engine_id))
            self.config = config or {}
            self.dir = tmp_path
            self.written = {}

        def check_gate(self, *_a, **_k):
            return True, ""

        def read_stage(self, _stage):
            return {}

        def merge_pair(self, stage, pid, data):
            self.written.setdefault(stage, {}).setdefault(pid, {}).update(data)

    return _Exp()


class TestAllThreeDeployPathsAreGated:
    """There are three ways to deploy here, and only one was gated.

    stage_deploy (local), examples/multi_model_agents/deploy_agents.py (the
    script that actually deployed the v4 set), and the KFP deploy component.
    The script shipped a 0/30 engine on 2026-08-31 precisely because it was
    ungated, so a test naming all three is worth more than a comment.
    """

    def test_the_local_stage_gates(self):
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        assert "gate_engine_health(" in src

    def test_the_example_script_gates(self):
        from pathlib import Path

        src = Path("examples/multi_model_agents/deploy_agents.py").read_text()
        assert "gate_engine_health" in src

    def test_the_kfp_component_gates(self):
        """KFP serializes each component in isolation, so the import must be
        inside the function body or it is simply absent at runtime."""
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        i = src.index("def deploy_single_agent")
        body = src[i : i + 6000]
        assert "gate_engine_health" in body
        # Parsed rather than string-matched: the import is legitimately
        # multi-line now that the component also pulls in enforce_health_gate
        # and health_gate_config, and an exact-text assertion just breaks on
        # formatting without checking anything more.
        import ast

        tree = ast.parse(body[: body.rindex("\n")] + "\n    pass\n")
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "wrangler.orchestration.stages"
            for alias in node.names
        }
        assert "gate_engine_health" in imported, (
            "the gate must be imported inside the component body -- KFP "
            "serializes each component in isolation"
        )


class TestRequiredGateStopsTheRun:
    """A characterisation run may not proceed on an engine the gate rejected.

    The gate has always been advisory: both call sites checked
    `if not health["passed"]` and then only warned. The campaign-06 validation
    arm drew three engines, all failed (1.7% reach after two rerolls), and the
    eval ran against the worst of them anyway -- producing 88% coverage whose
    missing 12% is non-random dropout on a known-sick engine.

    Advisory is right for a production sweep, which may reasonably proceed on a
    degraded engine and say so. It is wrong for a run whose entire output is a
    noise measurement, because the noise it measures is then the engine's.
    Hence a manifest key rather than a change of default.
    """

    def test_required_is_off_by_default(self):
        assert stages.health_gate_config({})["required"] is False

    def test_required_can_be_turned_on(self):
        cfg = stages.health_gate_config({"health_gate": {"required": True}})
        assert cfg["required"] is True

    def test_a_failing_gate_raises_when_required(self):
        import pytest

        health = {"passed": False, "rate": 0.017, "n": 60, "rerolls": 2, "engine_id": "e1"}
        with pytest.raises(stages.EngineHealthError, match="c06-ctrl"):
            stages.enforce_health_gate(health, required=True, pair_id="c06-ctrl")

    def test_a_failing_gate_only_warns_when_not_required(self):
        health = {"passed": False, "rate": 0.017, "n": 60, "rerolls": 2, "engine_id": "e1"}
        assert stages.enforce_health_gate(health, required=False, pair_id="p") is False

    def test_a_passing_gate_never_raises(self):
        health = {"passed": True, "rate": 0.99, "n": 60, "rerolls": 0, "engine_id": "e1"}
        assert stages.enforce_health_gate(health, required=True, pair_id="p") is True

    def test_both_paths_use_the_same_enforcement(self):
        """The local and KFP paths have diverged before -- `enabled_pairs` did.

        Neither may hand-roll the decision; both call the one function.
        """
        from pathlib import Path

        for path in ("wrangler/orchestration/stages.py", "wrangler/pipeline/components.py"):
            src = Path(path).read_text()
            assert "enforce_health_gate" in src, f"{path} must route through the shared check"

    def test_the_campaign_06_manifests_require_it(self):
        """The arms whose only output is a noise floor must not run on a sick engine."""
        import glob
        import pathlib

        import yaml

        manifests = sorted(glob.glob("manifests/c06-*_manifest.yaml"))
        assert manifests, "expected the campaign 06 manifests"
        for path in manifests:
            cfg = yaml.safe_load(pathlib.Path(path).read_text())
            assert cfg.get("health_gate", {}).get("required") is True, (
                f"{path} measures a noise floor; it may not run on a rejected engine"
            )


class TestTheFourBlockersFoundInReview:
    """Regressions for defects a 953-test suite did not catch.

    All four lived where the tests do not look: three in the KFP component
    bodies, which are serialized in isolation and never executed by a unit
    test, and one in the wiring between a stage artifact and the reporter.
    Each test below fails against the code as it was before the fix.
    """

    def _component_body(self, name: str) -> str:
        from pathlib import Path

        src = Path("wrangler/pipeline/components.py").read_text()
        i = src.index(f"def {name}")
        rest = src[i:]
        j = rest.find("\n@dsl.component", 1)
        return rest[: j if j != -1 else len(rest)]

    def test_the_kfp_optimize_component_pins_the_manifests_model(self):
        """Otherwise two c07 arms on the same agent module optimize one model.

        `stage_optimize` has passed `model=` since 7219295; this path did not,
        so claude-sonnet-5 and claude-sonnet-4-6 -- both riding sonnet_agent --
        would have differed only by label, confounding the frontier campaign 07
        exists to measure.
        """
        import ast

        body = self._component_body("optimize_single_agent")
        tree = ast.parse("def _f():\n" + "\n".join("    " + ln for ln in body.splitlines()))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "optimize":
                assert any(kw.arg == "model" for kw in node.keywords), (
                    "optimize() must be told the manifest's model, not the "
                    "one the _opt module imports"
                )
                return
        raise AssertionError("no optimize() call found in the component")

    def test_the_kfp_deploy_component_honours_enabled(self):
        """A manifest that turns the gate off must not pay 12 min of probing."""
        body = self._component_body("deploy_single_agent")
        assert 'gate_cfg["enabled"]' in body, (
            "the KFP path reads the gate config but must also honour `enabled`; "
            "the local path guards the whole gate with it"
        )

    def test_the_kfp_deploy_component_does_not_double_delete(self):
        """gate_engine_health already discards the draws it created.

        Re-deleting all of `rejected` guarantees a NotFound logged as
        'could not discard', a warning that reads like a leak and is not one.
        """
        body = self._component_body("deploy_single_agent")
        assert 'for stale in health["rejected"]' not in body, (
            "only the handed-in engine (rejected[0]) needs deleting here"
        )

    def test_a_required_gate_failure_still_records_the_engine(self, tmp_path):
        """Otherwise the engine is up, costing money, and named nowhere.

        Every c06 manifest sets required: true, so this fires on exactly the
        runs where an orphan is most likely.
        """
        import pytest

        from wrangler.orchestration import stages

        exp = _stub_experiment(
            tmp_path,
            config={"health_gate": {"required": True, "max_rerolls": 0, "attempts": 5}},
        )
        with (
            patch.object(stages.deployer, "deploy_agent_from_source", return_value="eng-sick"),
            patch.object(stages, "_default_probe", _Probe([0.0])),
            patch.object(stages, "_sync_env_engine_id", lambda *_a, **_k: None),
            pytest.raises(stages.EngineHealthError),
        ):
            stages.stage_deploy(exp)

        recorded = exp.written.get("deploy", {}).get("p1", {})
        assert recorded.get("engine_id"), (
            "the deploy record must be persisted before the gate raises, or the "
            "engine it names is orphaned"
        )

    def test_the_report_results_carry_token_usage(self):
        """The measured-spend columns read a key nothing produced.

        token_usage was written into the eval stage artifacts and never
        forwarded into the dict the reporter reads, so both new cost columns
        rendered n/a on every real report -- while the unit test passed,
        because it built the dict with token_usage already in it.
        """
        from wrangler.orchestration.stages import _sum_token_usage

        summed = _sum_token_usage(
            {"token_usage": {"input_tokens": 10, "output_tokens": 5}},
            {},
            {"token_usage": {"input_tokens": 1, "output_tokens": 2}},
        )
        assert summed == {"input_tokens": 11, "output_tokens": 7}

    def test_no_token_data_stays_absent_rather_than_zero(self):
        """$0.00 reads as 'this run was free', not 'we did not measure it'."""
        from wrangler.orchestration.stages import _sum_token_usage

        assert _sum_token_usage({}, {}, {}) == {}

    def test_stage_report_forwards_token_usage(self):
        """The wiring, not the summer -- this is the half that was missing."""
        import ast
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def stage_report")
        body = src[i : src.index("\ndef ", i + 1) if "\ndef " in src[i + 1 :] else len(src)]
        assert "_sum_token_usage(" in body, (
            "stage_report must forward token usage into the results dict the reporter reads"
        )
        _ = ast
