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
        for tool in (agent.tools or []):
            try:
                if hasattr(tool, "func"):
                    tools.append(_inspect_function_tool(tool))
                elif hasattr(tool, "agent"):
                    tools.append(ToolSpec(
                        name=tool.agent.name,
                        description=getattr(tool.agent, "description", ""),
                        tool_type="agent_tool",
                    ))
                else:
                    tools.append(ToolSpec(
                        name=str(type(tool).__name__),
                        description="Auto-discovered tool",
                        tool_type="unknown",
                    ))
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
                        **({"parameters": t.parameters} if t.parameters else {}),
                    }
                    for t in spec.tools
                ],
            }
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False, width=100)
