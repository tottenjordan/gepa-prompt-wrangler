import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lite_agent import lite_agent
from prompts.lite_prompts import OPTIMIZED

lite_agent.instruction = OPTIMIZED["wrangler_v4"]["prompt"]
root_agent = lite_agent
agent = types.SimpleNamespace(root_agent=lite_agent)
