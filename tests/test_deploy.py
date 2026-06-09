"""Tests for wrangler.core.deploy — agent deployment/update on GEAP."""

import pytest
from unittest.mock import patch, MagicMock


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
        from wrangler.core.deploy import deploy_agent, REQUIREMENTS

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
        mock_remote.resource_name = "projects/test-project/locations/us-central1/reasoningEngines/12345"
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
