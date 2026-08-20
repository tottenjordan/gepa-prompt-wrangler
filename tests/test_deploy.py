"""Tests for wrangler.core.deploy — agent deployment/update on GEAP."""

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestDeployAgent:
    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_returns_engine_id(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import deploy_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/12345"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        result = deploy_agent(agent, display_name="test")
        assert result == "12345"

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_config_includes_labels(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import deploy_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        deploy_agent(agent)

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["labels"] == {"solution": "promp-wrangler"}

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_config_includes_requirements(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import REQUIREMENTS, deploy_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        deploy_agent(agent)

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["requirements"] == REQUIREMENTS

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_custom_display_name(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import deploy_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        deploy_agent(agent, display_name="custom-name")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["display_name"] == "custom-name"


class TestUpdateAgent:
    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    @patch("wrangler.core.deploy.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.core.deploy.GCP_REGION", "us-central1")
    def test_short_engine_id_expanded(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import update_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = (
            "projects/test-project/locations/us-central1/reasoningEngines/12345"
        )
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        update_agent(agent, "12345")

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        name = call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")
        assert "projects/test-project" in name
        assert "12345" in name

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_full_resource_name_passthrough(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import update_agent

        full_name = "projects/p/locations/l/reasoningEngines/99"
        mock_remote = MagicMock()
        mock_remote.resource_name = full_name
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        update_agent(agent, full_name)

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        name = call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")
        assert name == full_name

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_update_config_includes_labels(self, mock_client, mock_vertexai):
        from wrangler.core.deploy import update_agent

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent = MagicMock()
        agent.name = "test-agent"
        update_agent(agent, "projects/p/locations/l/reasoningEngines/99")

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["labels"] == {"solution": "promp-wrangler"}


# --- Source-based deployment tests ---


def _make_agent_tree(tmp_path):
    """Create a minimal agent directory tree for build_source_package tests.

    Mirrors the real layout: config.py/registry.py/prompts/ live alongside
    the agents/ directory, not inside it.
    """
    project_dir = tmp_path / "multi_model_agents"
    project_dir.mkdir()

    (project_dir / "config.py").write_text(
        'SEARCH_MCP_SERVER = "s"\nBOOKING_MCP_SERVER = "b"\n'
        'EXPENSE_MCP_SERVER = "e"\ndef resolve_model(m): return m\n'
    )
    (project_dir / "registry.py").write_text(
        "from config import SEARCH_MCP_SERVER\ndef get_mcp_tools(name): return name\n"
    )
    prompts_dir = project_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.py").write_text('PROMPT = "hello"\n')

    agents_dir = project_dir / "agents"
    agents_dir.mkdir()
    agent_file = agents_dir / "sonnet_agent.py"
    agent_file.write_text("root_agent = None\n")

    return str(agent_file)


class TestBuildSourcePackage:
    def test_creates_expected_files(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        result = build_source_package(
            agent_module, "Test instruction", "gemini-3.5-flash", build_dir
        )

        build_path = Path(result)
        assert (build_path / "app.py").exists()
        assert (build_path / "config.py").exists()
        assert (build_path / "registry.py").exists()
        assert (build_path / "instruction.txt").exists()
        assert (build_path / "requirements.txt").exists()
        assert (build_path / "__init__.py").exists()
        assert (build_path / "prompts" / "default.py").exists()

    def test_instruction_content(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "My custom prompt", "gemini-3.5-flash", build_dir)

        content = (Path(build_dir) / "instruction.txt").read_text()
        assert content == "My custom prompt"

    def test_app_py_contains_model(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "claude-sonnet-4-6", build_dir)

        app_content = (Path(build_dir) / "app.py").read_text()
        assert "claude-sonnet-4-6" in app_content

    def test_app_py_contains_agent_name(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)

        app_content = (Path(build_dir) / "app.py").read_text()
        assert "sonnet_agent" in app_content

    def test_no_env_file_shipped(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)

        assert not (Path(build_dir) / ".env").exists()

    def test_config_mcp_vars_use_get(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        # Create a config.py with hard os.environ["KEY"] lookups
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "config.py").write_text(
            "import os\n"
            'SEARCH_MCP_SERVER = os.environ["SEARCH_MCP_SERVER"]\n'
            'BOOKING_MCP_SERVER = os.environ["BOOKING_MCP_SERVER"]\n'
            'EXPENSE_MCP_SERVER = os.environ["EXPENSE_MCP_SERVER"]\n'
        )
        agents_dir = project_dir / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "test_agent.py"
        agent_file.write_text("")

        build_dir = str(tmp_path / "build")
        build_source_package(str(agent_file), "Prompt", "gemini-3.5-flash", build_dir)

        config_text = (Path(build_dir) / "config.py").read_text()
        assert 'os.environ["SEARCH_MCP_SERVER"]' not in config_text
        assert 'os.environ.get("SEARCH_MCP_SERVER", "")' in config_text
        assert 'os.environ.get("BOOKING_MCP_SERVER", "")' in config_text
        assert 'os.environ.get("EXPENSE_MCP_SERVER", "")' in config_text

    def test_no_pycache_in_prompts(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        # Create a __pycache__ dir in prompts
        pycache = tmp_path / "multi_model_agents" / "prompts" / "__pycache__"
        pycache.mkdir()
        (pycache / "default.cpython-312.pyc").write_bytes(b"\x00")

        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)

        assert not (Path(build_dir) / "prompts" / "__pycache__").exists()

    def test_registry_uses_google_auth(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)

        registry_content = (Path(build_dir) / "registry.py").read_text()
        assert "GoogleAuth" in registry_content
        assert "google.auth" in registry_content
        assert "McpToolset" in registry_content
        assert "SEARCH_MCP_URL" in registry_content

    def test_cleans_existing_build_dir(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        Path(build_dir).mkdir()
        (Path(build_dir) / "stale.txt").write_text("old")

        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)
        assert not (Path(build_dir) / "stale.txt").exists()

    def test_requirements_content(self, tmp_path):
        from wrangler.core.deploy import _SOURCE_REQUIREMENTS, build_source_package

        agent_module = _make_agent_tree(tmp_path)
        build_dir = str(tmp_path / "build")
        build_source_package(agent_module, "Prompt", "gemini-3.5-flash", build_dir)

        reqs = (Path(build_dir) / "requirements.txt").read_text().strip().split("\n")
        assert reqs == _SOURCE_REQUIREMENTS


class TestDeployAgentFromSource:
    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_returns_engine_id(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/12345"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        result = deploy_agent_from_source(
            agent_module,
            "gemini-3.5-flash",
            "Test prompt",
            "test-agent",
        )
        assert result == "12345"

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_config_uses_source_packages(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert "source_packages" in config
        assert "entrypoint_module" in config
        assert config["entrypoint_object"] == "app"
        assert config["agent_framework"] == "google-adk"

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_no_agent_param(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        assert "agent" not in (call_kwargs.kwargs or {})

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_config_includes_class_methods(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import _ADK_CLASS_METHODS, deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["class_methods"] == _ADK_CLASS_METHODS
        assert len(config["class_methods"]) == 13

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_cleans_up_build_dir(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "test")

        assert not Path("/tmp/geap_build_pkg").exists()


class TestUpdateAgentFromSource:
    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    @patch("wrangler.core.deploy.GCP_PROJECT_ID", "test-project")
    @patch("wrangler.core.deploy.GCP_REGION", "us-central1")
    def test_short_engine_id_expanded(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import update_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = (
            "projects/test-project/locations/us-central1/reasoningEngines/12345"
        )
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        update_agent_from_source("12345", agent_module, "gemini-3.5-flash", "New prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        name = call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")
        assert "projects/test-project" in name
        assert "12345" in name

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_update_uses_source_packages(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import update_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        update_agent_from_source(
            "projects/p/locations/l/reasoningEngines/99",
            agent_module,
            "gemini-3.5-flash",
            "Updated prompt",
            "test",
        )

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert "source_packages" in config
        assert config["agent_framework"] == "google-adk"

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_no_agent_param_on_update(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import update_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        update_agent_from_source(
            "projects/p/locations/l/reasoningEngines/99",
            agent_module,
            "gemini-3.5-flash",
            "Prompt",
            "test",
        )

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        assert "agent" not in (call_kwargs.kwargs or {})
