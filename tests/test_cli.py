"""Tests for wrangler.cli — Click CLI commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
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
    @patch("wrangler.tools.inspector.AgentInspector.inspect")
    @patch("wrangler.tools.inspector.AgentInspector.to_yaml")
    def test_inspect_calls_inspector(self, mock_to_yaml, mock_inspect):
        from wrangler.tools.inspector import AgentSpec

        mock_inspect.return_value = AgentSpec(name="test", model="m", instruction="i", tools=[])
        mock_to_yaml.return_value = "agent:\n  name: test\n"
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "some/path"])
        assert result.exit_code == 0
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


class TestPipelineRunCommand:
    @patch("wrangler.pipeline.deploy_pipeline.deploy_pipeline")
    def test_pipeline_run_submits_job(self, mock_deploy):
        mock_deploy.return_value = {
            "run_id": "run-20260609-120000",
            "job_id": "gepa-run-20260609-120000",
            "dashboard_uri": "https://console.cloud.google.com/vertex-ai/...",
        }
        runner = CliRunner()
        with runner.isolated_filesystem():
            manifest = {
                "name": "test",
                "agent_module": "agents/test",
                "eval_data": "eval.yaml",
                "pairs": [{"id": "p1", "model": "m", "system_prompt": "s"}],
            }
            Path("manifest.yaml").write_text(yaml.dump(manifest))
            result = runner.invoke(main, ["pipeline", "run", "manifest.yaml"])
            assert result.exit_code == 0
            assert "run-20260609-120000" in result.output
            assert "Dashboard" in result.output

    @patch("wrangler.pipeline.deploy_pipeline.deploy_pipeline")
    def test_pipeline_run_with_custom_run_id(self, mock_deploy):
        mock_deploy.return_value = {
            "run_id": "my-run",
            "job_id": "gepa-my-run",
            "dashboard_uri": "https://...",
        }
        runner = CliRunner()
        with runner.isolated_filesystem():
            manifest = {
                "name": "test",
                "agent_module": "agents/test",
                "eval_data": "eval.yaml",
                "pairs": [{"id": "p1", "model": "m", "system_prompt": "s"}],
            }
            Path("manifest.yaml").write_text(yaml.dump(manifest))
            result = runner.invoke(main, ["pipeline", "run", "manifest.yaml", "--run-id", "my-run"])
            assert result.exit_code == 0
            mock_deploy.assert_called_once()
            assert mock_deploy.call_args.kwargs.get("run_id") == "my-run"


class TestPipelineStatusCommand:
    @patch("google.cloud.aiplatform.PipelineJob")
    @patch("google.cloud.aiplatform.init")
    def test_pipeline_status_shows_state(self, mock_init, mock_pj_class):
        mock_job = MagicMock()
        mock_job.display_name = "gepa-test-pipeline"
        mock_job.state = "PIPELINE_STATE_SUCCEEDED"
        mock_job.create_time = "2026-06-09T12:00:00"
        mock_job.end_time = "2026-06-09T13:00:00"
        mock_pj_class.get.return_value = mock_job

        runner = CliRunner()
        result = runner.invoke(main, ["pipeline", "status", "some-job-id"])
        assert result.exit_code == 0
        assert "gepa-test-pipeline" in result.output


class TestRunDryRunMultiPair:
    def test_dry_run_shows_all_pairs(self, tmp_path):
        manifest = {
            "name": "multi-test",
            "agent_module": "agents/test",
            "eval_data": "eval.yaml",
            "pairs": [
                {"id": "flash", "model": "gemini-3.5-flash", "system_prompt": "Be helpful."},
                {"id": "sonnet", "model": "claude-sonnet-4-6", "system_prompt": "Be thorough."},
            ],
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest))
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(manifest_path), "--dry-run"])
        assert result.exit_code == 0
        assert "Pairs: 2" in result.output
        assert "flash" in result.output
        assert "sonnet" in result.output


class TestGenerateEvalsetCommand:
    @patch("wrangler.core.converter.generate_sampler_config")
    @patch("wrangler.core.converter.generate_gepa_evalset")
    @patch("wrangler.core.converter.load_eval_file")
    def test_generate_evalset(self, mock_load, mock_gen, mock_sampler, tmp_path):
        mock_load.return_value = [{"prompt": "test", "expected_response": "ok"}] * 10
        mock_gen.return_value = str(tmp_path / "eval_set.evalset.json")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "generate-evalset",
                "--from",
                str(tmp_path / "eval.yaml"),
                "--output",
                str(tmp_path / "output"),
                "-n",
                "5",
            ],
        )
        assert result.exit_code == 0
        assert "Loaded 10 eval cases" in result.output
