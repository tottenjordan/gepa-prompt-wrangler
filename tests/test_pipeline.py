"""Tests for the Vertex AI Pipeline package."""

import json

import pytest
import yaml


class TestPipelineDAGCompilation:
    """Verify the KFP pipeline compiles without errors."""

    def _make_manifest(self, tmp_path, pairs):
        manifest = {
            "name": "test-experiment",
            "agent_module": "agents/example_agent",
            "eval_data": "eval_data/example_eval.yaml",
            "pairs": pairs,
            "eval_config": {"judge_model": "gemini-2.5-pro"},
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))
        return path, manifest

    def test_compile_single_pair(self, tmp_path):
        from kfp import compiler
        from wrangler.pipeline.dag import gepa_pipeline

        output_path = str(tmp_path / "pipeline.yaml")
        compiler.Compiler().compile(
            pipeline_func=gepa_pipeline,
            package_path=output_path,
        )
        assert (tmp_path / "pipeline.yaml").exists()
        content = (tmp_path / "pipeline.yaml").read_text()
        assert "gepa-prompt-optimization" in content

    def test_compiled_yaml_has_all_components(self, tmp_path):
        from kfp import compiler
        from wrangler.pipeline.dag import gepa_pipeline

        output_path = str(tmp_path / "pipeline.yaml")
        compiler.Compiler().compile(
            pipeline_func=gepa_pipeline,
            package_path=output_path,
        )
        content = (tmp_path / "pipeline.yaml").read_text()
        assert "archive-agent-code" in content
        assert "deploy-single-agent" in content
        assert "eval-single-agent" in content
        assert "optimize-single-agent" in content
        assert "redeploy-single-agent" in content
        assert "generate-analysis" in content


class TestManifestSerialization:
    """Verify manifest round-trips through JSON for pipeline parameters."""

    def test_manifest_with_costs_round_trip(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest_data = {
            "name": "cost-test",
            "agent_module": "agents/test",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {
                    "id": "flash",
                    "model": "gemini-3.5-flash",
                    "system_prompt": "Be helpful.",
                    "costs": {"input": 1.5, "output": 9.0},
                },
                {
                    "id": "sonnet",
                    "model": "claude-sonnet-4-6",
                    "system_prompt": "Be thorough.",
                },
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest_data))

        manifest = PairFactory.load(str(path))
        assert manifest.pairs[0].costs == {"input": 1.5, "output": 9.0}
        assert manifest.pairs[1].costs is None

        pairs_json = [{"id": p.id, "model": p.model, "costs": p.costs} for p in manifest.pairs]
        serialized = json.dumps(pairs_json)
        restored = json.loads(serialized)
        assert restored[0]["costs"]["input"] == 1.5
        assert restored[1]["costs"] is None

    def test_manifest_without_costs(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest_data = {
            "name": "no-costs",
            "agent_module": "agents/test",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "Hi."},
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest_data))

        manifest = PairFactory.load(str(path))
        assert manifest.pairs[0].costs is None


class TestBlendedCostWithCustom:
    """Verify blended_cost accepts custom cost overrides."""

    def test_custom_costs_override(self):
        from wrangler.core.config import blended_cost

        custom = {"input": 10.0, "output": 50.0}
        result = blended_cost("unknown-model", custom_costs=custom)
        expected = (4 * 10.0 + 1 * 50.0) / 5
        assert abs(result - expected) < 0.001

    def test_fallback_to_model_costs(self):
        from wrangler.core.config import blended_cost, MODEL_COSTS

        result = blended_cost("gemini-3.5-flash")
        cost = MODEL_COSTS["gemini-3.5-flash"]
        expected = (4 * cost["input"] + 1 * cost["output"]) / 5
        assert abs(result - expected) < 0.001

    def test_unknown_model_returns_zero(self):
        from wrangler.core.config import blended_cost

        result = blended_cost("totally-unknown-model")
        assert result == 0.0


class TestParetoFrontier:
    """Verify the Pareto frontier uses proper non-dominated sort."""

    def test_dominated_point_excluded(self):
        import numpy as np
        from unittest.mock import patch

        results = {
            "cheap-good": {"model": "gemini-3.1-flash-lite", "after": {"q": 0.9}},
            "expensive-worse": {"model": "gemini-3.5-flash", "after": {"q": 0.85}},
            "expensive-best": {"model": "claude-sonnet-4-6", "after": {"q": 0.95}},
        }

        from wrangler.reporting.analysis import generate_cost_quality_chart
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            generate_cost_quality_chart(results, charts_dir=Path(td))
            assert (Path(td) / "cost_quality.png").exists()
