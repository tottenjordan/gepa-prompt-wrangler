import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sonnet_agent import sonnet_agent
from generic_prompts import GENERIC_PROMPT

sonnet_agent.instruction = GENERIC_PROMPT
root_agent = sonnet_agent
agent = types.SimpleNamespace(root_agent=sonnet_agent)
