"""Tests for manifest parsing and agent-prompt pair factory."""

import pytest
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

    def _manifest_with_temperature(self, tmp_path, model, temperature):
        manifest = {
            "name": "test",
            "agent_module": "agents/test",
            "pairs": [
                {
                    "id": "p1",
                    "model": model,
                    "system_prompt": "a",
                    "temperature": temperature,
                }
            ],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))
        return str(path)

    def test_temperature_rejected_for_models_that_reject_sampling_params(self, tmp_path):
        """claude-opus-5 returns a 400 for a non-default temperature.

        Catching it at manifest load names the offending pair; catching it at
        run time surfaces as an SDK stack trace partway through a deployment.
        """
        from wrangler.core.factory import PairFactory

        path = self._manifest_with_temperature(tmp_path, "claude-opus-5", 0.2)
        with pytest.raises(ValueError, match="rejects sampling parameters"):
            PairFactory.load(path)

    def test_default_temperature_is_allowed_on_those_models(self, tmp_path):
        """Only a *non-default* value 400s, so the implicit 1.0 must still load.

        Every existing manifest omits temperature, and the dataclass default
        fills in 1.0 — rejecting that would break all of them.
        """
        from wrangler.core.factory import PairFactory

        path = self._manifest_with_temperature(tmp_path, "claude-opus-5", 1.0)
        assert PairFactory.load(path).pairs[0].temperature == 1.0

    def test_temperature_allowed_on_models_that_accept_it(self, tmp_path):
        from wrangler.core.factory import PairFactory

        path = self._manifest_with_temperature(tmp_path, "claude-opus-4-6", 0.2)
        assert PairFactory.load(path).pairs[0].temperature == 0.2

    def test_unregistered_model_does_not_block_a_temperature(self, tmp_path):
        """An id we know nothing about gets the benefit of the doubt.

        The registry cannot know about a model added to Vertex yesterday, and
        refusing to load is worse than letting the API decide.
        """
        from wrangler.core.factory import PairFactory

        path = self._manifest_with_temperature(tmp_path, "some-future-model", 0.2)
        assert PairFactory.load(path).pairs[0].temperature == 0.2

    def test_file_not_found_raises(self, tmp_path):
        from wrangler.core.factory import PairFactory

        with pytest.raises((FileNotFoundError, OSError)):
            PairFactory.load(str(tmp_path / "nonexistent.yaml"))
