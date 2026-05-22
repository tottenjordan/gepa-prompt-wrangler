"""Example travel agent for prompt-wrangler evaluation."""

import types
from agents.example_agent.agent import agent, create_agent, root_agent

agent = types.SimpleNamespace(root_agent=root_agent)
