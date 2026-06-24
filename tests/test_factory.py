"""Tests for manifest parsing and agent-prompt pair factory."""

import pytest
import tempfile
from pathlib import Path

import yaml


class TestPairFactory:
    def test_load_valid_manifest(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "eval_data": "eval_data/test.yaml",
            "pairs": [
                {"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "Be helpful."},
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = PairFactory.load(str(path))
        assert result.name == "test"
        assert len(result.pairs) == 1
        assert result.pairs[0].id == "p1"
        assert result.pairs[0].model == "gemini-3.5-flash"

    def test_missing_required_field_raises(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {"name": "test", "pairs": []}
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        with pytest.raises(ValueError, match="agent_module"):
            PairFactory.load(str(path))

    def test_pair_missing_model_raises(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "pairs": [{"id": "p1", "system_prompt": "hello"}],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        with pytest.raises(ValueError, match="model"):
            PairFactory.load(str(path))

    def test_auto_generated_pair_ids(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "pairs": [
                {"model": "gemini-3.5-flash", "system_prompt": "a"},
                {"model": "claude-sonnet-4-6", "system_prompt": "b"},
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = PairFactory.load(str(path))
        assert result.pairs[0].id == "pair-1"
        assert result.pairs[1].id == "pair-2"

    def test_get_pair_by_id(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "pairs": [
                {"id": "flash", "model": "gemini-3.5-flash", "system_prompt": "a"},
                {"id": "sonnet", "model": "claude-sonnet-4-6", "system_prompt": "b"},
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = PairFactory.load(str(path))
        assert result.get_pair("sonnet").model == "claude-sonnet-4-6"

    def test_get_nonexistent_pair_raises(self, tmp_path):
        from wrangler.core.factory import PairFactory

        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "pairs": [{"id": "p1", "model": "m", "system_prompt": "s"}],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = PairFactory.load(str(path))
        with pytest.raises(KeyError):
            result.get_pair("nonexistent")

    def test_file_not_found_raises(self, tmp_path):
        from wrangler.core.factory import PairFactory
        with pytest.raises((FileNotFoundError, OSError)):
            PairFactory.load(str(tmp_path / "nonexistent.yaml"))
