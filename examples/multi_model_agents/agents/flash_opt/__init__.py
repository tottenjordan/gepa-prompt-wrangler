import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flash_agent import flash_agent

flash_agent.instruction = "You are a helpful assistant. Use the available tools to answer user questions."
root_agent = flash_agent
agent = types.SimpleNamespace(root_agent=flash_agent)
