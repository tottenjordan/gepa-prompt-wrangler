"""Example travel agent for prompt-wrangler evaluation."""

import types

from .agent import create_agent, root_agent

# Shadows agent.agent deliberately: ADK discovery expects a module-like object
# exposing root_agent at the package level.
agent = types.SimpleNamespace(root_agent=root_agent)

__all__ = ["agent", "create_agent", "root_agent"]
