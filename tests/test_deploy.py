"""Tests for wrangler.core.deploy — agent deployment/update on GEAP."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_missing_config_py_is_fatal(self, tmp_path):
        """A package with no config.py deploys fine and then fails to start.

        The failure surfaces as an opaque GEAP "failed to be updated" twenty
        minutes later, so it has to be caught at build time.
        """
        import pytest

        from wrangler.core.deploy import build_source_package

        orphan = tmp_path / "nowhere" / "agent.py"
        orphan.parent.mkdir()
        orphan.write_text("root_agent = None\n")

        with pytest.raises(FileNotFoundError, match=r"config\.py not found"):
            build_source_package(str(orphan), "Prompt", "gemini-3.5-flash", str(tmp_path / "build"))

    def test_accepts_a_dotted_module_path(self, tmp_path, monkeypatch):
        """`a.b.c_agent` is the importable form and gets passed by hand a lot.

        `Path()` accepts it silently -- `.parent` is `"."` -- so without
        normalisation it builds a package with no config.py.
        """
        from wrangler.core.deploy import build_source_package

        _make_agent_tree(tmp_path)
        monkeypatch.chdir(tmp_path)
        dotted = "multi_model_agents.agents.sonnet_agent"

        result = build_source_package(dotted, "Prompt", "gemini-3.5-flash", str(tmp_path / "build"))

        assert (Path(result) / "config.py").exists()

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
        """A .env next to config.py must not be tarballed up to GEAP.

        The fixture writes one deliberately: without it this test passes
        vacuously, which is how the build package shipped a secrets file for
        as long as it did. Nothing on the server reads it — the copy lands at
        /code/_geap_build_pkg/.env while load_dotenv() searches /code — so it
        was upload-only risk. The MCP vars travel via `mcp_env_from_environ`.
        """
        from wrangler.core.deploy import build_source_package

        agent_module = _make_agent_tree(tmp_path)
        env_file = Path(agent_module).parent.parent / ".env"
        env_file.write_text("SEARCH_MCP_SERVER=http://fake\n")
        assert env_file.exists(), "premise: a .env exists next to config.py"

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

    def test_shipped_config_resolves_the_real_agent_model(self, tmp_path):
        """The emitted config.py must survive `resolve_model` with a project set.

        GEAP imports config.py at container start, so anything the build step
        does to it that only breaks at runtime shows up as an opaque "Reasoning
        Engine failed to be updated" after a ~10 minute build. A textual rewrite
        of this file once hoisted a guard's body and left the substituted return
        outside it, producing `UnboundLocalError: cannot access local variable
        '_proj'` on every deploy with a project configured.
        """
        import subprocess
        import sys

        from wrangler.core.deploy import build_source_package

        real_agent = "examples/multi_model_agents/agents/sonnet_agent"
        build_dir = str(tmp_path / "build")
        build_source_package(real_agent, "Prompt", "claude-sonnet-4-6", build_dir)

        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import config; print(config.resolve_model('claude-sonnet-4-6'))",
            ],
            cwd=build_dir,
            env={
                "PATH": "/usr/bin:/bin",
                "GCP_PROJECT_ID": "test-proj",
                "GOOGLE_CLOUD_PROJECT": "test-proj",
                "GOOGLE_GENAI_USE_VERTEXAI": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        assert probe.returncode == 0, probe.stderr

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


class TestBuildWithoutMcp:
    """A build mode with no MCP toolsets, so startup cost becomes a variable.

    Every measurement of the empty-stream defect (silent-failures.md #5) has
    been taken against an agent that performs three MCP handshakes at import,
    so "GEAP routes to booting workers" and "this agent boots slowly" are
    confounded. Deploying the normal package with ``env_vars={}`` does not
    separate them -- the generated registry.py raises at import when no MCP
    URLs are set, so the container crash-loops instead of booting fast.

    This mode is the experiment's independent variable, and it doubles as the
    minimal reproduction shipped with the escalation.
    """

    def test_no_registry_is_written(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(
            _make_agent_tree(tmp_path), "Prompt", "gemini-3.5-flash", build_dir, include_mcp=False
        )
        assert not (Path(build_dir) / "registry.py").exists()

    def test_app_does_not_reference_mcp(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(
            _make_agent_tree(tmp_path), "Prompt", "gemini-3.5-flash", build_dir, include_mcp=False
        )
        app = (Path(build_dir) / "app.py").read_text()
        assert "get_mcp_tools" not in app
        assert "registry" not in app
        assert "MCP_SERVER" not in app

    def test_everything_else_is_still_written(self, tmp_path):
        """Only the toolset changes. A missing config.py fails twenty minutes later."""
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(
            _make_agent_tree(tmp_path), "Prompt", "gemini-3.5-flash", build_dir, include_mcp=False
        )
        build_path = Path(build_dir)
        for name in ("app.py", "config.py", "instruction.txt", "requirements.txt", "__init__.py"):
            assert (build_path / name).exists(), name

    def test_generated_app_is_valid_python(self, tmp_path):
        """The container imports this; a syntax error is a twenty-minute round trip."""
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(
            _make_agent_tree(tmp_path), "Prompt", "gemini-3.5-flash", build_dir, include_mcp=False
        )
        compile((Path(build_dir) / "app.py").read_text(), "app.py", "exec")

    def test_instruction_still_travels(self, tmp_path):
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(
            _make_agent_tree(tmp_path),
            "Bare probe prompt",
            "gemini-3.5-flash",
            build_dir,
            include_mcp=False,
        )
        assert (Path(build_dir) / "instruction.txt").read_text() == "Bare probe prompt"

    def test_default_is_unchanged(self, tmp_path):
        """The MCP path is what every real deployment uses; it must not move."""
        from wrangler.core.deploy import build_source_package

        build_dir = str(tmp_path / "build")
        build_source_package(_make_agent_tree(tmp_path), "Prompt", "gemini-3.5-flash", build_dir)
        assert (Path(build_dir) / "registry.py").exists()
        assert "get_mcp_tools" in (Path(build_dir) / "app.py").read_text()


class TestOtelSpanExport:
    """Span batches were being dropped under load, taking online eval's input
    with them. The tuning below is the only handle we have on the exporter --
    the Vertex SDK builds it with no arguments, so env vars are the interface.
    See docs/notes/silent-failures.md #8."""

    def test_tuning_reaches_the_deploy_config(self):
        from wrangler.core.deploy import _build_source_config

        env = _build_source_config("_geap_build_pkg", "n")["env_vars"]

        # Telemetry has to be on at all for any of the rest to matter.
        assert env["GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"] == "true"
        # The exporter gives its first attempt the entire deadline, so this
        # ceiling is what decides whether a slow export ever lands.
        assert env["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] == "30"
        assert env["OTEL_BSP_SCHEDULE_DELAY"] == "2000"
        assert env["OTEL_BSP_MAX_EXPORT_BATCH_SIZE"] == "128"

    def test_processor_timeout_exceeds_exporter_timeout(self):
        """The batch processor must not give up before the exporter does.

        These are different units in the upstream SDK — the processor is in
        milliseconds, the exporter in seconds — which is an easy way to set a
        60s-looking value that is really 60ms, or to raise one and silently
        invert the ordering.
        """
        from wrangler.core.deploy import _build_source_config

        env = _build_source_config("_geap_build_pkg", "n")["env_vars"]
        exporter_sec = float(env["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"])
        processor_sec = float(env["OTEL_BSP_EXPORT_TIMEOUT"]) / 1000

        assert processor_sec > exporter_sec, (
            f"processor gives up at {processor_sec}s, exporter still trying until {exporter_sec}s"
        )

    def test_caller_env_vars_still_win(self):
        """The pipeline passes overrides through `env_vars`; tuning must not
        outrank them, or the localhost MCP overrides break."""
        from wrangler.core.deploy import _build_source_config

        env = _build_source_config(
            "_geap_build_pkg", "n", env_vars={"OTEL_BSP_SCHEDULE_DELAY": "9999"}
        )["env_vars"]

        assert env["OTEL_BSP_SCHEDULE_DELAY"] == "9999"


class TestInstanceScaling:
    """GEAP scales to zero by default, and a request that lands on a booting
    worker returns HTTP 200 with an empty body rather than an error."""

    def test_absent_by_default(self, monkeypatch):
        """No scaling keys unless something asks for them.

        `_build_source_config` reads the GEAP_* vars off the ambient process
        environment, and conftest loads the developer's `.env` — so this test
        started failing the moment a real `GEAP_MIN_INSTANCES` was set. Clear
        them explicitly: the assertion is about the default, not about what
        happens to be in `.env` today.
        """
        from wrangler.core.deploy import _build_source_config

        for var in ("GEAP_MIN_INSTANCES", "GEAP_MAX_INSTANCES", "GEAP_CONTAINER_CONCURRENCY"):
            monkeypatch.delenv(var, raising=False)
        config = _build_source_config("_geap_build_pkg", "n")
        assert "min_instances" not in config
        assert "max_instances" not in config

    def test_explicit_arguments(self):
        from wrangler.core.deploy import _build_source_config

        config = _build_source_config("_geap_build_pkg", "n", min_instances=2, max_instances=4)
        assert config["min_instances"] == 2
        assert config["max_instances"] == 4

    def test_environment_fallback(self, monkeypatch):
        from wrangler.core.deploy import _build_source_config

        monkeypatch.setenv("GEAP_MIN_INSTANCES", "3")
        monkeypatch.setenv("GEAP_CONTAINER_CONCURRENCY", "8")
        config = _build_source_config("_geap_build_pkg", "n")
        assert config["min_instances"] == 3
        assert config["container_concurrency"] == 8

    def test_explicit_argument_beats_environment(self, monkeypatch):
        from wrangler.core.deploy import _build_source_config

        monkeypatch.setenv("GEAP_MIN_INSTANCES", "3")
        config = _build_source_config("_geap_build_pkg", "n", min_instances=1)
        assert config["min_instances"] == 1

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_reaches_the_deploy_call(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_client.return_value.agent_engines.create.return_value = MagicMock(
            resource_name="projects/p/locations/l/reasoningEngines/1"
        )
        deploy_agent_from_source(
            _make_agent_tree(tmp_path), "gemini-3.5-flash", "P", "n", min_instances=2
        )

        config = mock_client.return_value.agent_engines.create.call_args.kwargs["config"]
        assert config["min_instances"] == 2


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
    def test_config_includes_labels(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["labels"] == {"solution": "promp-wrangler"}

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_custom_display_name(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import deploy_agent_from_source

        mock_remote = MagicMock()
        mock_remote.resource_name = "projects/p/locations/l/reasoningEngines/99"
        mock_client.return_value.agent_engines.create.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        deploy_agent_from_source(agent_module, "gemini-3.5-flash", "Prompt", "custom-name")

        call_kwargs = mock_client.return_value.agent_engines.create.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["display_name"] == "custom-name"

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

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_full_resource_name_passthrough(self, mock_client, mock_vertexai, tmp_path):
        from wrangler.core.deploy import update_agent_from_source

        full_name = "projects/p/locations/l/reasoningEngines/99"
        mock_remote = MagicMock()
        mock_remote.resource_name = full_name
        mock_client.return_value.agent_engines.update.return_value = mock_remote

        agent_module = _make_agent_tree(tmp_path)
        update_agent_from_source(full_name, agent_module, "gemini-3.5-flash", "Prompt", "test")

        call_kwargs = mock_client.return_value.agent_engines.update.call_args
        assert (call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")) == full_name

    @patch("wrangler.core.deploy.vertexai")
    @patch("wrangler.core.deploy._get_client")
    def test_update_config_includes_labels(self, mock_client, mock_vertexai, tmp_path):
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
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["labels"] == {"solution": "promp-wrangler"}


# --- Call-site tests ---
#
# cli.py and runner.py used to call the cloudpickle path. CLAUDE.md documents
# that path as broken on GEAP -- cloudpickle captures module references
# (registry.py, config.py, prompts/) that do not exist server-side -- so
# `wrangler deploy manifest.yaml` took the failing route while `wrangler run`
# and the KFP pipeline took the working one. These tests pin the call sites.


def _manifest_with_agent_dir(tmp_path, *, agent_module="agents/demo", pair_agent_module=None):
    """Write a manifest plus the agent directory it points at."""
    import yaml

    (tmp_path / agent_module).mkdir(parents=True)
    pair = {"id": "p1", "model": "gemini-3.5-flash", "system_prompt": "Be helpful."}
    if pair_agent_module is not None:
        pair["agent_module"] = pair_agent_module
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.dump(
            {
                "name": "test",
                "agent_module": agent_module,
                "eval_data": "eval.yaml",
                "pairs": [pair],
            }
        )
    )
    return str(path)


class TestRunnerDeployCallSites:
    def test_deploy_pair_uses_source_deployment(self, tmp_path, monkeypatch):
        from wrangler.orchestration import runner as runner_mod

        seen = {}
        monkeypatch.setattr(
            runner_mod.deployer,
            "deploy_agent_from_source",
            lambda **kw: seen.update(kw) or "engine-123",
        )

        pipeline = runner_mod.WranglerPipeline(_manifest_with_agent_dir(tmp_path))
        pair = pipeline.manifest.pairs[0]
        assert pipeline._deploy_pair(pair) == "engine-123"
        assert seen["agent_module"] == str(tmp_path / "agents" / "demo")
        assert seen["model"] == "gemini-3.5-flash"
        assert seen["instruction"] == "Be helpful."
        assert seen["display_name"] == "p1"

    def test_redeploy_pair_sends_the_optimized_prompt(self, tmp_path, monkeypatch):
        """Phase 4 must redeploy what GEPA produced, not the seed prompt.

        The old cloudpickle path got there by mutating ``agent.instruction`` on
        a freshly imported agent object. The source-based path passes the text
        straight through, so this is the seam where a redeploy could silently
        ship the original prompt and make the after-scores meaningless.
        """
        from wrangler.orchestration import runner as runner_mod

        seen = {}
        monkeypatch.setattr(
            runner_mod.deployer,
            "update_agent_from_source",
            lambda **kw: seen.update(kw) or "engine-123",
        )

        pipeline = runner_mod.WranglerPipeline(_manifest_with_agent_dir(tmp_path))
        pair = pipeline.manifest.pairs[0]
        pair.system_prompt = "OPTIMIZED BY GEPA"
        pipeline._redeploy_pair(pair, "engine-123")
        assert seen["engine_id"] == "engine-123"
        assert seen["instruction"] == "OPTIMIZED BY GEPA"

    def test_pair_agent_module_overrides_the_manifest_default(self, tmp_path):
        from wrangler.orchestration.runner import WranglerPipeline

        (tmp_path / "agents" / "other").mkdir(parents=True)
        path = _manifest_with_agent_dir(tmp_path, pair_agent_module="agents/other")
        pipeline = WranglerPipeline(path)
        resolved = pipeline._agent_module_path(pipeline.manifest.pairs[0])
        assert resolved == str(tmp_path / "agents" / "other")

    def test_agent_module_path_falls_back_to_the_bare_reference(self, tmp_path):
        """A manifest may name a path relative to the CWD rather than to itself.

        ``_load_agent`` allowed both before this migration; dropping the
        fallback would break those manifests at deploy time only.
        """
        from wrangler.orchestration.runner import WranglerPipeline

        path = _manifest_with_agent_dir(tmp_path, pair_agent_module="somewhere/else")
        pipeline = WranglerPipeline(path)
        resolved = pipeline._agent_module_path(pipeline.manifest.pairs[0])
        assert resolved == "somewhere/else"


class TestCliDeployCallSite:
    def test_manifest_deploy_uses_source_based_path(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from wrangler.cli import main
        from wrangler.orchestration import runner as runner_mod

        seen = {}
        monkeypatch.setattr(
            runner_mod.deployer,
            "deploy_agent_from_source",
            lambda **kw: seen.update(kw) or "engine-123",
        )

        result = CliRunner().invoke(main, ["deploy", _manifest_with_agent_dir(tmp_path)])
        assert result.exit_code == 0, result.output
        assert seen["display_name"] == "p1"
        assert "engine-123" in result.output


def test_cloudpickle_entrypoints_are_gone():
    """The legacy names must not come back through a merge or a revert.

    They are not deprecated -- they are known-broken against GEAP, so a caller
    reaching one is a failed deployment, not a slow one.
    """
    import wrangler.core
    import wrangler.core.deploy as deploy_mod

    for name in ("deploy_agent", "update_agent", "REQUIREMENTS"):
        assert not hasattr(deploy_mod, name), f"wrangler.core.deploy.{name} is back"
        assert not hasattr(wrangler.core, name), f"wrangler.core.{name} is back"
