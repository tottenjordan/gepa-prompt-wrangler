"""Tests for wrangler.inspector — agent introspection and tool discovery."""

import pytest
import yaml

from wrangler.tools.inspector import AgentInspector, AgentSpec, ToolSpec, _inspect_function_tool


class TestToolSpec:
    def test_defaults(self):
        ts = ToolSpec(name="t", description="d")
        assert ts.parameters == {}
        assert ts.tool_type == "function"


class TestAgentSpec:
    def test_defaults(self):
        a = AgentSpec(name="a", model="m", instruction="i")
        assert a.tools == []


class TestInspectFunctionTool:
    def test_basic_function_extraction(self):
        def search_flights(origin: str, destination: str):
            """Search for available flights."""

        spec = _inspect_function_tool(search_flights)
        assert spec.name == "search_flights"
        assert spec.description == "Search for available flights."
        assert "origin" in spec.parameters
        assert "destination" in spec.parameters

    def test_typed_parameters(self):
        def func(count: int, price: float, active: bool):
            """Test."""

        spec = _inspect_function_tool(func)
        assert spec.parameters["count"]["type"] == "integer"
        assert spec.parameters["price"]["type"] == "number"
        assert spec.parameters["active"]["type"] == "boolean"

    def test_unannotated_defaults_to_string(self):
        def func(name):
            """Test."""

        spec = _inspect_function_tool(func)
        assert spec.parameters["name"]["type"] == "string"

    def test_required_vs_optional(self):
        def func(required_param: str, optional_param: str = "default"):
            """Test."""

        spec = _inspect_function_tool(func)
        assert spec.parameters["required_param"]["required"] is True
        assert spec.parameters["optional_param"]["required"] is False

    def test_self_param_excluded(self):
        def method(self, name: str):
            """Test."""

        spec = _inspect_function_tool(method)
        assert "self" not in spec.parameters
        assert "name" in spec.parameters


class TestAgentInspectorToYaml:
    def test_yaml_output_structure(self):
        spec = AgentSpec(
            name="test_agent",
            model="gemini-2.0-flash",
            instruction="Be helpful.",
            tools=[
                ToolSpec(name="tool1", description="First tool"),
                ToolSpec(name="tool2", description="Second tool"),
            ],
        )
        result = AgentInspector.to_yaml(spec)
        parsed = yaml.safe_load(result)
        assert parsed["agent"]["name"] == "test_agent"
        assert parsed["agent"]["model"] == "gemini-2.0-flash"
        assert len(parsed["agent"]["tools"]) == 2

    def test_empty_tools_list(self):
        spec = AgentSpec(name="a", model="m", instruction="i", tools=[])
        result = AgentInspector.to_yaml(spec)
        parsed = yaml.safe_load(result)
        assert parsed["agent"]["tools"] == []


class TestAgentInspectorInspect:
    def test_inspect_valid_agent_module(self, tmp_path):
        agent_dir = tmp_path / "my_agent"
        agent_dir.mkdir()
        (agent_dir / "__init__.py").write_text(
            "from types import SimpleNamespace\n"
            "root_agent = SimpleNamespace(\n"
            "    name='test', model='gemini-2.0-flash',\n"
            "    instruction='Be helpful.', tools=[]\n"
            ")\n"
            "agent = SimpleNamespace(root_agent=root_agent)\n"
        )
        spec = AgentInspector.inspect(str(agent_dir))
        assert spec.name == "test"
        assert spec.model == "gemini-2.0-flash"

    def test_inspect_no_root_agent_raises(self, tmp_path):
        agent_dir = tmp_path / "bad_agent"
        agent_dir.mkdir()
        (agent_dir / "__init__.py").write_text("x = 1\n")
        with pytest.raises(ValueError, match="Could not find root_agent"):
            AgentInspector.inspect(str(agent_dir))

    def test_inspect_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError, match=r"__init__\.py"):
            AgentInspector.inspect("/nonexistent/path/agent")
