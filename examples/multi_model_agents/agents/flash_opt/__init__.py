import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent
from prompts.flash_prompts import GENERIC

# Start from the generic 78-char prompt to measure GEPA lift from scratch
flash_agent.instruction = GENERIC
root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
