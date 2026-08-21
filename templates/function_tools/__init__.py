"""Template agent with function tools — copy and customize for your use case."""

import types

from .agent import create_agent, root_agent

agent = types.SimpleNamespace(root_agent=root_agent)

__all__ = ["agent", "create_agent", "root_agent"]
