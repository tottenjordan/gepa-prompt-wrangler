"""Prompt registry — stores all prompt versions for each agent.

Each agent has:
- GENERIC: intentionally weak starting prompt (same for all agents)
- OPTIMIZED: dict of GEPA-optimized prompts keyed by version name

Prompt source of truth:
- Pipeline (wrangler): manifest.yaml `system_prompt` field per pair
- Standalone agent runs: agents import GENERIC directly
"""
