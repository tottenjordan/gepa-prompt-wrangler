import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent
from generic_prompts import GENERIC_PROMPT

flash_agent.instruction = GENERIC_PROMPT
root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
