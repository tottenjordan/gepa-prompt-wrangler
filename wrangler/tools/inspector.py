"""Agent introspection — auto-discover tools and generate YAML scaffold."""

import importlib.util
import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    tool_type: str = "function"
    eval_name: str = ""

    def __post_init__(self):
        if not self.eval_name:
            self.eval_name = self.name


@dataclass
class AgentSpec:
    name: str
    model: str
    instruction: str
    tools: list[ToolSpec] = field(default_factory=list)


def _inspect_function_tool(tool) -> ToolSpec:
    """Extract tool info from a function-based ADK tool."""
    func = getattr(tool, "func", tool)
    sig = inspect.signature(func)
    params = {}
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        param_type = "string"
        if param.annotation != inspect.Parameter.empty:
            type_name = getattr(param.annotation, "__name__", str(param.annotation))
            if "int" in type_name:
                param_type = "integer"
            elif "float" in type_name:
                param_type = "number"
            elif "bool" in type_name:
                param_type = "boolean"
        params[param_name] = {
            "type": param_type,
            "required": param.default == inspect.Parameter.empty,
        }

    return ToolSpec(
        name=getattr(tool, "name", func.__name__),
        description=(func.__doc__ or "").strip().split("\n")[0],
        parameters=params,
        tool_type="function",
    )


def _detect_mcp_eval_name(tool) -> str | None:
    """Try to compute the prefixed eval name for an MCP toolset.

    MCP tools registered via Agent Registry get prefixed as:
    {server_name}_{tool_name} (hyphens replaced with underscores).
    """
    server_name = None

    if hasattr(tool, "connection_params"):
        cp = tool.connection_params
        if hasattr(cp, "resource_name"):
            parts = cp.resource_name.rsplit("/", 1)
            if len(parts) == 2:
                server_name = parts[-1]
        elif hasattr(cp, "url"):
            url = str(cp.url)
            for segment in url.rstrip("/").rsplit("/", 3):
                if segment and not segment.startswith("http"):
                    server_name = segment
                    break

    if hasattr(tool, "name"):
        if server_name is None:
            server_name = getattr(tool, "name", "")
        if server_name:
            return server_name.replace("-", "_")

    return None


class AgentInspector:
    @staticmethod
    def inspect(agent_module_path: str) -> AgentSpec:
        """Import agent module and discover its tools."""
        path = Path(agent_module_path).resolve()
        init_file = path / "__init__.py"
        if not init_file.exists():
            for py_file in path.glob("*.py"):
                if py_file.name != "__init__.py":
                    init_file = py_file
                    break

        spec = importlib.util.spec_from_file_location("_inspect_agent", str(init_file))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        agent = None
        if hasattr(module, "agent") and hasattr(module.agent, "root_agent"):
            agent = module.agent.root_agent
        elif hasattr(module, "root_agent"):
            agent = module.root_agent

        if agent is None:
            raise ValueError(f"Could not find root_agent in {agent_module_path}")

        tools = []
        for tool in agent.tools or []:
            try:
                if callable(tool) and not hasattr(tool, "func") and not hasattr(tool, "agent"):
                    tools.append(_inspect_function_tool(tool))
                elif hasattr(tool, "func"):
                    tools.append(_inspect_function_tool(tool))
                elif hasattr(tool, "agent"):
                    tools.append(
                        ToolSpec(
                            name=tool.agent.name,
                            description=getattr(tool.agent, "description", ""),
                            tool_type="agent_tool",
                        )
                    )
                elif hasattr(tool, "connection_params"):
                    mcp_prefix = _detect_mcp_eval_name(tool)
                    tool_name = getattr(tool, "name", type(tool).__name__)
                    tools.append(
                        ToolSpec(
                            name=tool_name,
                            description=f"MCP toolset ({tool_name})",
                            tool_type="mcp_toolset",
                            eval_name=mcp_prefix or tool_name,
                        )
                    )
                else:
                    tools.append(
                        ToolSpec(
                            name=str(type(tool).__name__),
                            description="Auto-discovered tool",
                            tool_type="unknown",
                        )
                    )
            except Exception:
                pass

        model_str = str(agent.model) if agent.model else "unknown"
        if hasattr(agent.model, "model"):
            model_str = agent.model.model

        return AgentSpec(
            name=agent.name,
            model=model_str,
            instruction=agent.instruction or "",
            tools=tools,
        )

    @staticmethod
    def to_yaml(spec: AgentSpec) -> str:
        """Serialize AgentSpec to YAML scaffold."""
        data = {
            "agent": {
                "name": spec.name,
                "model": spec.model,
                "instruction": spec.instruction,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "type": t.tool_type,
                        **({"eval_name": t.eval_name} if t.eval_name != t.name else {}),
                        **({"parameters": t.parameters} if t.parameters else {}),
                    }
                    for t in spec.tools
                ],
            }
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False, width=100)

    @staticmethod
    def generate_manifest_stub(spec: AgentSpec, agent_module_path: str) -> dict:
        """Generate a pre-populated manifest dict from an inspected agent."""
        return {
            "name": f"{spec.name}-optimization",
            "description": f"Prompt optimization for {spec.name}",
            "agent_module": agent_module_path,
            "eval_data": "eval_cases.yaml",
            "pairs": [
                {
                    "id": spec.name,
                    "model": spec.model,
                    "system_prompt": spec.instruction or "You are a helpful assistant.",
                },
            ],
            "eval_config": {
                "judge_model": "gemini-2.5-pro",
                "response_match_threshold": 0.5,
                "safety_threshold": 0.8,
            },
        }

    @staticmethod
    def generate_eval_skeleton(spec: AgentSpec, count: int = 5) -> list[dict]:
        """Generate skeleton eval cases using discovered tool names."""
        cases = []
        function_tools = [t for t in spec.tools if t.tool_type == "function"]
        mcp_tools = [t for t in spec.tools if t.tool_type == "mcp_toolset"]

        for i, tool in enumerate(function_tools[:count]):
            param_names = list(tool.parameters.keys())
            param_example = ", ".join(f"{p}=..." for p in param_names[:2])
            cases.append(
                {
                    "prompt": f"TODO: Write a query that triggers {tool.name}({param_example})",
                    "expected_response": "TODO: Expected agent response",
                    "expected_tools": [
                        {"name": tool.eval_name, "args": {p: "TODO" for p in param_names[:2]}},
                    ],
                }
            )

        for tool in mcp_tools[: max(count - len(cases), 1)]:
            cases.append(
                {
                    "prompt": f"TODO: Write a query that uses the {tool.name} MCP toolset",
                    "expected_response": "TODO: Expected agent response",
                    "expected_tools": [
                        {"name": f"{tool.eval_name}_TOOL_NAME", "args": {}},
                    ],
                    "_note": f"MCP tools are prefixed: {tool.eval_name}_<tool_function_name>",
                }
            )

        if not cases:
            cases.append(
                {
                    "prompt": "TODO: Write a test query for your agent",
                    "expected_response": "TODO: Expected agent response",
                    "expected_tools": [],
                }
            )

        return cases
