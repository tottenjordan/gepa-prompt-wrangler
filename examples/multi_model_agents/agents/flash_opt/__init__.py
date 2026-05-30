import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent
from prompts.flash_prompts import OPTIMIZED

flash_agent.instruction = OPTIMIZED["wrangler_v4"]["prompt"]
root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
