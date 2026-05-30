import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pro_agent import pro_agent
from prompts.pro_prompts import OPTIMIZED

pro_agent.instruction = OPTIMIZED["wrangler_v4"]["prompt"]
root_agent = pro_agent
agent = types.SimpleNamespace(root_agent=pro_agent)
