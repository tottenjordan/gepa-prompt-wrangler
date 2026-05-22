"""Tests for wrangler.cli — Click CLI commands."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from wrangler.cli import main


class TestInitCommand:
    def test_creates_manifest(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init"])
            assert result.exit_code == 0
            assert Path("manifest.yaml").exists()

    def test_custom_output_path(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", "--output", "custom.yaml"])
            assert result.exit_code == 0
            assert Path("custom.yaml").exists()

    def test_refuses_overwrite(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("manifest.yaml").write_text("existing")
            result = runner.invoke(main, ["init"])
            assert result.exit_code != 0
            assert "already exists" in result.output

    def test_manifest_has_expected_structure(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init"])
            with open("manifest.yaml") as f:
                manifest = yaml.safe_load(f)
            assert "name" in manifest
            assert "agent_module" in manifest
            assert "eval_data" in manifest
            assert "pairs" in manifest
            assert len(manifest["pairs"]) >= 2
            assert "eval_config" in manifest


class TestRunCommand:
    def test_dry_run_parses_manifest(self, tmp_path):
        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "eval_data": "eval.yaml",
            "pairs": [
                {"id": "p1", "model": "gemini-2.0-flash", "system_prompt": "test"},
            ],
        }
        manifest_path = tmp_path / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(manifest, f)
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(manifest_path), "--dry-run"])
        assert result.exit_code == 0
        assert "Pairs: 1" in result.output

    def test_missing_manifest_errors(self):
        runner = CliRunner()
        result = runner.invoke(main, ["run", "nonexistent.yaml", "--dry-run"])
        assert result.exit_code != 0


class TestInspectCommand:
    @patch("wrangler.inspector.AgentInspector.inspect")
    @patch("wrangler.inspector.AgentInspector.to_yaml")
    def test_inspect_calls_inspector(self, mock_to_yaml, mock_inspect):
        from wrangler.inspector import AgentSpec
        mock_inspect.return_value = AgentSpec(name="test", model="m", instruction="i", tools=[])
        mock_to_yaml.return_value = "agent:\n  name: test\n"
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "some/path"])
        mock_inspect.assert_called_once_with("some/path")


class TestEvalCommand:
    def test_missing_engine_id_errors(self, tmp_path):
        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "eval_data": "eval.yaml",
            "pairs": [{"id": "p1", "model": "m", "system_prompt": "s"}],
        }
        manifest_path = tmp_path / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(manifest, f)
        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(manifest_path)])
        assert result.exit_code != 0 or "engine-id" in result.output.lower()


class TestReportCommand:
    def test_no_results_files(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("empty_outputs").mkdir()
            result = runner.invoke(main, ["report", "empty_outputs"])
            assert "No results files found" in result.output
