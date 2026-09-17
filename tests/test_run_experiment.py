"""`scripts/run_experiment.py` — the documented entry point for a DOE campaign.

Two behaviours carry weight and are asserted rather than trusted:

1. **`--dry-run` makes no GCP call.** A dry-run that authenticates is not a dry-run; it
   fails on an expired token and tells you nothing about the plan.
2. **A manifest-schema file fails with a message that names the difference.** The two YAML
   shapes are not interchangeable and `PairFactory.load()`'s native error
   (`missing required field: 'name'`) says nothing about why.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from scripts import run_experiment


def _experiment(tmp_path, pairs_yaml: str, *, defaults: str = "num_runs: 2"):
    exp = tmp_path / "c09"
    exp.mkdir()
    (exp / "config.yaml").write_text(
        textwrap.dedent(f"""
        experiment:
          name: c09
          description: fixture
          version: c09
        agent_module: examples/multi_model_agents/agents/flash_agent
        eval_data: examples/multi_model_agents/eval_data/eval_cases.yaml
        source_manifest: manifests/c09_manifest.yaml
        defaults:
          {defaults}
        pairs:
        {textwrap.indent(textwrap.dedent(pairs_yaml), "        ").rstrip()}
        """).lstrip()
    )
    return exp / "config.yaml"


TWO_ARMS = """
- id: c09-rationale-on
  model: gemini-3.5-flash
  system_prompt: hello
- id: c09-control
  model: gemini-3.5-flash
  system_prompt: hello
  skip_optimize: true
"""


class TestDryRunTouchesNothing:
    def test_it_constructs_no_gcp_client(self, tmp_path, monkeypatch, capsys):
        """The load-bearing property. Poison the clients; a dry-run must not reach them."""
        from wrangler.core import clients

        def explode(*a, **k):
            raise AssertionError("--dry-run constructed a GCP client")

        monkeypatch.setattr(clients, "agent_client", explode)
        monkeypatch.setattr(sys, "argv", ["x", str(_experiment(tmp_path, TWO_ARMS)), "--dry-run"])

        assert run_experiment.main() == 0
        assert "Dry run" in capsys.readouterr().out

    def test_it_reports_each_arm_and_its_factors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["x", str(_experiment(tmp_path, TWO_ARMS)), "--dry-run"])
        run_experiment.main()
        out = capsys.readouterr().out
        assert "c09-rationale-on" in out
        assert "CONTROL (no optimize)" in out, "the control arm is not identified as one"

    def test_runs_overrides_the_config_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, "argv", ["x", str(_experiment(tmp_path, TWO_ARMS)), "--runs", "5", "--dry-run"]
        )
        run_experiment.main()
        assert "num_runs=5" in capsys.readouterr().out

    def test_it_falls_back_to_the_config_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["x", str(_experiment(tmp_path, TWO_ARMS)), "--dry-run"])
        run_experiment.main()
        assert "num_runs=2" in capsys.readouterr().out


class TestItWarnsAboutAMissingControlArm:
    def test_no_control_arm_is_called_out(self, tmp_path, monkeypatch, capsys):
        """CLAUDE.md requires one in every sweep; silence here is how it gets forgotten."""
        only_optimizing = """
        - id: solo
          model: gemini-3.5-flash
          system_prompt: hello
        """
        monkeypatch.setattr(
            sys, "argv", ["x", str(_experiment(tmp_path, only_optimizing)), "--dry-run"]
        )
        run_experiment.main()
        assert "no control arm" in capsys.readouterr().out.lower()


class TestSchemaConfusionFailsUsefully:
    def test_a_manifest_names_the_difference(self, tmp_path, monkeypatch):
        manifest = tmp_path / "m.yaml"
        manifest.write_text("name: looks-like-a-manifest\npairs: []\n")
        monkeypatch.setattr(sys, "argv", ["x", str(manifest), "--dry-run"])

        with pytest.raises(SystemExit) as excinfo:
            run_experiment.main()
        message = str(excinfo.value)
        assert "MANIFEST" in message
        assert "experiment create" in message, "the error does not say how to fix it"


class TestLocalRunsAreRefused:
    def test_without_pipeline_it_refuses(self, tmp_path, monkeypatch):
        """A 10-24 h campaign must not be tied to somebody's shell by default."""
        monkeypatch.setattr(sys, "argv", ["x", str(_experiment(tmp_path, TWO_ARMS))])
        assert run_experiment.main() == 2
