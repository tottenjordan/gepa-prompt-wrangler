import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent
from prompts.lite_prompts import OPTIMIZED as LITE_OPTIMIZED

# Seed with lite v3 prompt (same Gemini family, scored 0.833) instead of generic
flash_agent.instruction = LITE_OPTIMIZED["wrangler_v3"]["prompt"]
root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
