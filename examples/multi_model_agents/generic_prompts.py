"""Generic (intentionally weak) prompts for the e2e optimization demo.

These give GEPA maximum room to improve by providing only minimal instruction.
"""

GENERIC_PROMPT = "You are a helpful assistant. Use the available tools to answer user questions."

AGENT_GENERIC_PROMPTS = {
    "lite": GENERIC_PROMPT,
    "flash": GENERIC_PROMPT,
    "pro": GENERIC_PROMPT,
    "sonnet": GENERIC_PROMPT,
    "opus": GENERIC_PROMPT,
}
