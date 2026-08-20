"""Example travel agent for prompt-wrangler evaluation."""

import types

from .agent import agent, create_agent, root_agent

agent = types.SimpleNamespace(root_agent=root_agent)
