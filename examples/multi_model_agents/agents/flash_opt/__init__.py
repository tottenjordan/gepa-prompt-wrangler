import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent

root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
