"""Redeploy redraws the health lottery, so it has to be gated too.

`stage_deploy` probes a fresh engine and rerolls while it is below the bar. Redeploy did
not, and redeploy is not a safe operation: campaign 01 measured that updating an engine in
place **redraws its reach rate** (0%->50%, 6%->56%). So a run could gate `eval_before` to a
healthy engine, redeploy the optimized prompt onto it, draw a bad rate, and score
`eval_after` against an engine dropping a third of its cases.

That asymmetry biases every delta this repo publishes in the same direction -- after-side
dropout looks like a regression. Campaign 07's `c07-pro` came back 64/64 on both sides,
which is luck, not design: `redeploy` wrote no `health` key at all, so nothing had checked.

**The trap this file exists to pin.** `gate_engine_health` was written for deploy, where
each reroll produces a *new* engine and rejected ones must be discarded. An in-place update
returns the *same* id, so on the second reroll that id is in `gate_created` and a
`discard_fn` would delete the campaign's own engine. Redeploy must pass `discard_fn=None`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wrangler.orchestration.stages import gate_engine_health


class _Probe:
    """Returns the queued rates in order, then repeats the last one."""

    def __init__(self, *rates):
        self.rates = list(rates)
        self.calls = 0

    def __call__(self, engine_id, n=60, threshold=0.8, **_kw):
        rate = self.rates[min(self.calls, len(self.rates) - 1)]
        self.calls += 1
        # Same shape boot_probe.gate_report renders; a partial dict KeyErrors there.
        return {
            "reached": int(rate * n),
            "n": n,
            "rate": rate,
            "ci_low": max(0.0, rate - 0.1),
            "ci_high": min(1.0, rate + 0.1),
            "threshold": threshold,
            "passed": rate >= threshold,
        }


class TestInPlaceUpdateMustNotDeleteItsOwnEngine:
    """The reason redeploy passes `discard_fn=None`, demonstrated both ways."""

    def test_with_a_discard_fn_the_engine_would_be_deleted(self):
        """Documents the trap rather than asserting desired behaviour.

        This is what happens if someone copies stage_deploy's call verbatim: the second
        reroll deletes the engine the campaign is running on. If `gate_engine_health`
        ever stops doing this, delete this test and the `discard_fn=None` comment with it.
        """
        deleted = []
        gate_engine_health(
            "engine-A",
            redeploy_fn=lambda: "engine-A",  # in-place update: same id back
            probe_fn=_Probe(0.0),  # never passes, so it uses every reroll
            threshold=0.8,
            max_rerolls=2,
            discard_fn=deleted.append,
        )
        assert "engine-A" in deleted, (
            "expected the shared-id path to delete the engine; if this no longer holds, "
            "redeploy's discard_fn=None is no longer load-bearing"
        )

    def test_with_discard_fn_none_the_engine_survives(self):
        deleted = []
        result = gate_engine_health(
            "engine-A",
            redeploy_fn=lambda: "engine-A",
            probe_fn=_Probe(0.0),
            threshold=0.8,
            max_rerolls=2,
            discard_fn=None,
        )
        assert deleted == []
        assert result["engine_id"] == "engine-A", "the engine id must survive the gate"


class TestGateBehaviourOnRedeploy:
    def test_a_healthy_redeploy_does_not_reroll(self):
        updates = []
        result = gate_engine_health(
            "engine-A",
            redeploy_fn=lambda: updates.append(1) or "engine-A",
            probe_fn=_Probe(1.0),
            threshold=0.8,
            discard_fn=None,
        )
        assert result["passed"] is True
        assert result["rerolls"] == 0
        assert updates == [], "a passing engine must not be re-updated"

    def test_an_unhealthy_redeploy_is_retried_until_it_passes(self):
        updates = []
        result = gate_engine_health(
            "engine-A",
            redeploy_fn=lambda: updates.append(1) or "engine-A",
            probe_fn=_Probe(0.1, 0.9),
            threshold=0.8,
            discard_fn=None,
        )
        assert result["passed"] is True
        assert result["rerolls"] == 1
        assert len(updates) == 1

    def test_it_gives_up_rather_than_looping(self):
        """A persistently bad engine must end the gate, not the run.

        Opus failed six consecutive deploys on 2026-08-31; an unbounded retry would
        have spent the campaign on one arm.
        """
        result = gate_engine_health(
            "engine-A",
            redeploy_fn=lambda: "engine-A",
            probe_fn=_Probe(0.0),
            threshold=0.8,
            max_rerolls=2,
            discard_fn=None,
        )
        assert result["passed"] is False
        assert result["rerolls"] == 2


class TestBothRedeployPathsAreGated:
    """Two ways to redeploy, and the same omission in each.

    The deploy equivalent of this test exists because a *third* ungated path shipped a
    0/30 engine. Naming the paths is what stops the next one being missed.
    """

    def test_the_local_stage_gates(self):
        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def stage_redeploy")
        body = src[i : src.index("\ndef ", i + 10)]
        assert "gate_engine_health(" in body, "stage_redeploy does not health-gate"

    def test_the_kfp_component_gates(self):
        """KFP serializes each component alone, so the import must be in the body."""
        # Located via the AST rather than a character window: the component is
        # long, and a window that happens to stop short reports "not gated" for
        # a gated function.
        tree = ast.parse(Path("wrangler/pipeline/components.py").read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "redeploy_single_agent"
        )
        imported = {
            alias.name
            for node in ast.walk(fn)
            if isinstance(node, ast.ImportFrom) and node.module == "wrangler.orchestration.stages"
            for alias in node.names
        }
        assert "gate_engine_health" in imported, (
            "the gate must be imported inside redeploy_single_agent -- KFP serializes "
            "each component in isolation, so a module-level import is absent at runtime"
        )

    @pytest.mark.parametrize(
        ("path", "marker"),
        [
            ("wrangler/orchestration/stages.py", "def stage_redeploy"),
            ("wrangler/pipeline/components.py", "def redeploy_single_agent"),
        ],
    )
    def test_neither_path_passes_a_discard_fn(self, path, marker):
        """Passing one deletes the campaign's engine on the second reroll."""
        src = Path(path).read_text()
        i = src.index(marker)
        body = src[i : i + 9000]
        call = body.index("gate_engine_health(")
        window = body[call : call + 900]
        assert "discard_fn=None" in window, (
            f"{marker} must pass discard_fn=None explicitly -- an in-place update returns "
            "the same engine id, so any discard_fn deletes the engine being updated"
        )


class TestTheVerdictIsRecorded:
    """`redeploy` wrote no health key, so nobody could tell a gated run from a lucky one."""

    @pytest.mark.parametrize(
        ("path", "marker"),
        [
            ("wrangler/orchestration/stages.py", "def stage_redeploy"),
            ("wrangler/pipeline/components.py", "def redeploy_single_agent"),
        ],
    )
    def test_health_is_written_into_the_stage_record(self, path, marker):
        src = Path(path).read_text()
        i = src.index(marker)
        body = src[i : i + 9000]
        assert '"health"' in body, (
            f"{marker} must record the gate verdict; c07-pro's redeploy stage had no "
            "health key, so its clean 64/64 after-side could not be distinguished from luck"
        )


class TestStageRedeployIntegration:
    """Drives the real `stage_redeploy`, so it checks wiring rather than presence.

    A structural test proves `gate_engine_health(` appears in the function. It cannot
    tell you the config was read, the verdict was stored, or the update was retried.
    """

    @staticmethod
    def _exp(tmp_path, config):
        class _Pair:
            id = "p1"
            model = "m"
            agent_module = "agents/x"
            engine_id = "engine-A"
            enabled = True
            disabled_reason = ""

        class _Manifest:
            agent_module = "agents/x"

            def __init__(self):
                self.pairs = [_Pair()]

        class _Exp:
            version = "v1"

            def __init__(self):
                self.manifest = _Manifest()
                self.config = config
                self.dir = tmp_path
                self.written = {}

            def check_gate(self, *_a, **_k):
                return True, ""

            def read_stage(self, stage):
                if stage == "deploy":
                    return {"p1": {"engine_id": "engine-A"}}
                if stage == "optimize":
                    return {"p1": {"optimized_prompt": "an evolved prompt"}}
                return {}

            def merge_pair(self, stage, pid, data):
                self.written.setdefault(stage, {}).setdefault(pid, {}).update(data)

        return _Exp()

    def _run(self, monkeypatch, tmp_path, config, rates):
        from wrangler.orchestration import stages

        updates = []
        monkeypatch.setattr(
            stages.deployer, "update_agent_from_source", lambda **kw: updates.append(kw)
        )
        monkeypatch.setattr(stages, "_default_probe", _Probe(*rates))
        # A manifest dir the stage can join paths against.
        monkeypatch.setattr(stages, "_manifest_dir", lambda _exp: tmp_path)
        exp = self._exp(tmp_path, config)
        stages.stage_redeploy(exp)
        return exp, updates

    def test_a_bad_draw_is_retried_and_the_verdict_recorded(self, monkeypatch, tmp_path):
        exp, updates = self._run(
            monkeypatch,
            tmp_path,
            {"health_gate": {"enabled": True, "threshold": 0.8, "attempts": 4}},
            rates=(0.1, 0.95),
        )
        health = exp.written["redeploy"]["p1"]["health"]
        assert health["passed"] is True
        assert health["rerolls"] == 1
        assert len(updates) == 2, "expected the initial update plus one reroll"

    def test_a_disabled_gate_updates_once_and_records_that_it_skipped(self, monkeypatch, tmp_path):
        exp, updates = self._run(
            monkeypatch, tmp_path, {"health_gate": {"enabled": False}}, rates=(0.0,)
        )
        health = exp.written["redeploy"]["p1"]["health"]
        assert health.get("skipped") is True
        assert len(updates) == 1, "a disabled gate must not probe or reroll"
