"""Prompt registry — stores all prompt versions for each agent.

Each agent has:
- GENERIC: intentionally weak starting prompt (same for all agents)
- OPTIMIZED: GEPA-optimized prompt (unique per agent, updated after optimization)
- ACTIVE: which prompt to use for deployment (points to GENERIC or OPTIMIZED)
"""
