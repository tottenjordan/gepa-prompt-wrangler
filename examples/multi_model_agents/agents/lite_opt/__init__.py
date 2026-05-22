import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lite_agent import lite_agent
from generic_prompts import GENERIC_PROMPT

lite_agent.instruction = GENERIC_PROMPT
root_agent = lite_agent
agent = types.SimpleNamespace(root_agent=lite_agent)
