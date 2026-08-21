import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from opus_agent import opus_agent

root_agent = opus_agent
agent = types.SimpleNamespace(root_agent=opus_agent)
