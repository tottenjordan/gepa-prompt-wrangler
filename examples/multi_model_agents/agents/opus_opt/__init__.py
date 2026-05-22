import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from opus_agent import opus_agent
from generic_prompts import GENERIC_PROMPT

opus_agent.instruction = GENERIC_PROMPT
root_agent = opus_agent
agent = types.SimpleNamespace(root_agent=opus_agent)
