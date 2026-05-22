"""Convert between simplified YAML eval format and ADK evalset JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_eval_file(path: str | Path) -> list[dict[str, Any]]:
    """Load an eval file, auto-detecting format (YAML simplified or ADK JSON).

    Returns a list of eval cases in the internal simplified format::

        {
            "query": "...",
            "expected_response": "...",           # optional
            "expected_tools": ["tool_a", ...],    # optional
            "tags": ["tag1", ...],                # optional
        }
    """
    path = Path(path)
    fmt = detect_format(path)

    if fmt == "adk_json":
        return _load_adk_json(path)
    return _load_simplified_yaml(path)


def detect_format(path: str | Path) -> str:
    """Return 'simplified_yaml' or 'adk_json' based on file content."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        return "adk_json"

    # YAML files: peek at structure
    with open(path) as f:
        data = yaml.safe_load(f)

    if isinstance(data, list) and data and "query" in data[0]:
        return "simplified_yaml"

    # If it has the ADK wrapper structure
    if isinstance(data, list) and data and "reference" in data[0]:
        return "adk_json"

    return "simplified_yaml"


# ---------------------------------------------------------------------------
# Simplified YAML
# ---------------------------------------------------------------------------


def _load_simplified_yaml(path: Path) -> list[dict[str, Any]]:
    """Load the simplified YAML eval format.

    Expected structure::

        - query: "Find flights from SFO to JFK"
          expected_response: "Found 3 flights"
          expected_tools:
            - search_flights
          tags:
            - search
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list of eval cases, got {type(data).__name__}")

    cases = []
    for item in data:
        cases.append({
            "query": item["query"],
            "expected_response": item.get("expected_response", ""),
            "expected_tools": item.get("expected_tools", []),
            "tags": item.get("tags", []),
        })
    return cases


# ---------------------------------------------------------------------------
# ADK JSON
# ---------------------------------------------------------------------------


def _load_adk_json(path: Path) -> list[dict[str, Any]]:
    """Load ADK evalset JSON and convert to simplified internal format.

    ADK evalset format::

        [
            {
                "query": "...",
                "reference": "...",
                "expected_tool_use": [
                    {"tool_name": "search_flights", "tool_input": {...}}
                ]
            }
        ]
    """
    with open(path) as f:
        data = json.load(f)

    cases = []
    for item in data:
        tools = [
            t["tool_name"] for t in item.get("expected_tool_use", [])
        ]
        cases.append({
            "query": item["query"],
            "expected_response": item.get("reference", ""),
            "expected_tools": tools,
            "tags": item.get("tags", []),
        })
    return cases


# ---------------------------------------------------------------------------
# Export to ADK JSON
# ---------------------------------------------------------------------------


def to_adk_evalset(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert simplified eval cases to ADK evalset JSON format."""
    evalset = []
    for case in cases:
        entry: dict[str, Any] = {"query": case["query"]}
        if case.get("expected_response"):
            entry["reference"] = case["expected_response"]
        if case.get("expected_tools"):
            entry["expected_tool_use"] = [
                {"tool_name": t, "tool_input": {}} for t in case["expected_tools"]
            ]
        evalset.append(entry)
    return evalset


def save_adk_evalset(cases: list[dict[str, Any]], path: str | Path) -> None:
    """Convert simplified cases to ADK format and write to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    evalset = to_adk_evalset(cases)
    with open(path, "w") as f:
        json.dump(evalset, f, indent=2)
