import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pro_agent import pro_agent
from generic_prompts import GENERIC_PROMPT

pro_agent.instruction = GENERIC_PROMPT
root_agent = pro_agent
agent = types.SimpleNamespace(root_agent=pro_agent)
