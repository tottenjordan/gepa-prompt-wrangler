import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sonnet_agent import sonnet_agent
from prompts.sonnet_prompts import OPTIMIZED

sonnet_agent.instruction = OPTIMIZED["wrangler_v4"]["prompt"]
root_agent = sonnet_agent
agent = types.SimpleNamespace(root_agent=sonnet_agent)
