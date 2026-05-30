import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from opus_agent import opus_agent
from prompts.opus_prompts import OPTIMIZED

opus_agent.instruction = OPTIMIZED["wrangler_v4"]["prompt"]
root_agent = opus_agent
agent = types.SimpleNamespace(root_agent=opus_agent)
