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
            }
            stages.stage_deploy(exp)
        assert exp.written["deploy"]["p1"]["engine_id"] == "eng-good"

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
