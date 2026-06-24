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

    if isinstance(data, dict) and "eval_cases" in data:
        data = data["eval_cases"]
    if not isinstance(data, list):
        raise ValueError(f"Expected a YAML list of eval cases, got {type(data).__name__}")

    cases = []
    for item in data:
        prompt = item.get("prompt") or item.get("query", "")
        reference = item.get("expected_response") or item.get("reference", "")
        cases.append({
            "prompt": prompt,
            "reference": reference,
            "expected_tool": item.get("expected_tool", ""),
            "expected_tools": item.get("expected_tools", []),
            "description": item.get("description", ""),
            "tier": item.get("tier", ""),
            "category": item.get("category", ""),
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
        if case.get("category"):
            entry["category"] = case["category"]
        if case.get("tier"):
            entry["tier"] = case["tier"]
        evalset.append(entry)
    return evalset


def save_adk_evalset(cases: list[dict[str, Any]], path: str | Path) -> None:
    """Convert simplified cases to ADK format and write to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    evalset = to_adk_evalset(cases)
    with open(path, "w") as f:
        json.dump(evalset, f, indent=2)


# ---------------------------------------------------------------------------
# GEPA Evalset Generation
# ---------------------------------------------------------------------------


def _sample_balanced(cases: list[dict], count: int, seed: int = 42) -> list[dict]:
    """Sample a balanced subset across complexity levels.

    If cases have a 'complexity' or 'category' field, sample proportionally.
    Otherwise distribute evenly by position (first third = low, etc.).
    """
    import random
    rng = random.Random(seed)

    has_complexity = any(c.get("complexity") for c in cases)

    if has_complexity:
        buckets: dict[str, list[dict]] = {}
        for c in cases:
            level = c.get("complexity", "medium")
            buckets.setdefault(level, []).append(c)
    else:
        third = max(len(cases) // 3, 1)
        buckets = {
            "low": cases[:third],
            "medium": cases[third:2*third],
            "high": cases[2*third:],
        }

    per_bucket = max(count // len(buckets), 1)
    remainder = count - per_bucket * len(buckets)

    sampled = []
    for i, (level, bucket) in enumerate(sorted(buckets.items())):
        n = per_bucket + (1 if i < remainder else 0)
        n = min(n, len(bucket))
        sampled.extend(rng.sample(bucket, n))

    return sampled[:count]


def _case_to_gepa_conversation(case: dict, app_name: str) -> dict:
    """Convert a simplified eval case to GEPA conversation format."""
    prompt = case.get("prompt") or case.get("query", "")
    reference = case.get("reference") or case.get("expected_response", "")

    tool_uses = []
    for tool in case.get("expected_tools", []):
        if isinstance(tool, dict):
            tool_uses.append({
                "name": tool.get("name", ""),
                "args": tool.get("args", {}),
            })
        elif isinstance(tool, str):
            tool_uses.append({"name": tool, "args": {}})

    if not tool_uses and case.get("expected_tool"):
        tool_uses.append({"name": case["expected_tool"], "args": {}})

    session_input: dict[str, Any] = {
        "app_name": app_name,
        "user_id": "eval_user",
    }
    if case.get("category"):
        session_input["category"] = case["category"]
    if case.get("tier"):
        session_input["tier"] = case["tier"]

    return {
        "user_content": {
            "parts": [{"text": prompt}],
            "role": "user",
        },
        "final_response": {
            "parts": [{"text": reference}],
            "role": "model",
        },
        "intermediate_data": {
            "tool_uses": tool_uses,
        },
        "session_input": session_input,
    }


def generate_gepa_evalset(
    cases: list[dict],
    output_dir: str | Path,
    eval_set_id: str = "eval_set",
    app_name: str = "agent_opt",
    count: int = 15,
    balanced: bool = True,
) -> str:
    """Generate a GEPA-compatible evalset JSON from simplified cases.

    Args:
        cases: Simplified eval cases (from load_eval_file).
        output_dir: Directory to write the evalset JSON.
        eval_set_id: Identifier for the evalset (used in filename).
        app_name: App name for session_input (must match optimizer directory name).
        count: Number of cases to include.
        balanced: If True, sample across complexity levels.

    Returns:
        Path to the generated evalset JSON file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if balanced and len(cases) > count:
        selected = _sample_balanced(cases, count)
    else:
        selected = cases[:count]

    eval_cases = []
    for i, case in enumerate(selected):
        tier = case.get("tier", "") or case.get("complexity", "")
        category = case.get("category", "")
        if tier and category:
            eval_id = f"case_{i+1}_{tier}_{category}"
        elif tier:
            eval_id = f"case_{i+1}_{tier}"
        else:
            eval_id = f"case_{i+1}"
        conversation = _case_to_gepa_conversation(case, app_name)
        session_input = conversation.pop("session_input", {
            "app_name": app_name,
            "user_id": "eval_user",
        })
        eval_cases.append({
            "eval_id": eval_id,
            "conversation": [conversation],
            "session_input": session_input,
        })

    evalset = {
        "eval_set_id": eval_set_id,
        "eval_cases": eval_cases,
    }

    evalset_path = output_dir / f"{eval_set_id}.evalset.json"
    with open(evalset_path, "w") as f:
        json.dump(evalset, f, indent=2)

    return str(evalset_path)


# Default GEPA thresholds — keep in sync with the committed
# agents/*_opt/sampler_config.json files (the single source of truth).
_DEFAULT_THRESHOLDS = {
    "tool_use_quality_v1": 0.5,
    "final_response_quality_v1": 0.85,
    "hallucinations_v1": 0.95,
    "safety_v1": 0.95,
}


def build_gepa_criteria(
    thresholds: dict[str, float] | None = None,
    judge_model: str = "gemini-3.5-flash",
) -> dict:
    """Build the GEPA ``eval_config.criteria`` dict with calibrated thresholds.

    Emits ONLY metrics registered in the ADK metric evaluator registry:
    ``safety_v1``, ``hallucinations_v1``, ``rubric_based_final_response_quality_v1``,
    ``rubric_based_tool_use_quality_v1``. Reference-based / unregistered metrics
    (``response_match_score``, ``final_response_match_v2``, ``instruction_following_v1``)
    are intentionally excluded — they cause NotFoundError or score against
    references that don't match real MCP tool outputs.
    """
    t = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
    return {
        "safety_v1": t["safety_v1"],
        "hallucinations_v1": t["hallucinations_v1"],
        "rubric_based_final_response_quality_v1": {
            "judge_model_options": {"judge_model": judge_model},
            "threshold": t["final_response_quality_v1"],
            "rubrics": [
                {
                    "rubric_id": "instruction_adherence",
                    "rubric_content": {
                        "text_property": "Response follows system prompt instructions."
                    },
                    "type": "INSTRUCTION_ADHERENCE",
                },
                {
                    "rubric_id": "completeness",
                    "rubric_content": {
                        "text_property": "Response fully addresses the user request."
                    },
                    "type": "FINAL_RESPONSE_QUALITY",
                },
            ],
        },
        "rubric_based_tool_use_quality_v1": {
            "judge_model_options": {"judge_model": judge_model},
            "threshold": t["tool_use_quality_v1"],
            "rubrics": [
                {
                    "rubric_id": "correct_tool_selection",
                    "rubric_content": {
                        "text_property": "Correct tools selected."
                    },
                    "type": "TOOL_USE_QUALITY",
                },
                {
                    "rubric_id": "correct_parameters",
                    "rubric_content": {
                        "text_property": "Accurate tool parameters provided."
                    },
                    "type": "TOOL_USE_QUALITY",
                },
            ],
        },
    }


def generate_sampler_config(
    app_name: str,
    eval_set_name: str = "eval_set",
    judge_model: str = "gemini-3.5-flash",
    output_dir: str | Path | None = None,
    train_eval_case_ids: list[str] | None = None,
    validation_eval_case_ids: list[str] | None = None,
    multi_judge: bool = False,
) -> dict:
    """Generate a GEPA sampler config.

    Args:
        app_name: Must match the optimizer directory name.
        eval_set_name: Must match the evalset JSON filename stem (without .evalset.json).
        judge_model: Model used for response match scoring.
        output_dir: If provided, writes the config to sampler_config.json.
        train_eval_case_ids: Subset of case IDs for training. Uses all if None.
        validation_eval_case_ids: Subset of case IDs for validation. Uses train set if None.

    Returns:
        The sampler config dict.
    """
    config = {
        "eval_config": {
            "criteria": build_gepa_criteria(judge_model=judge_model),
        },
        "app_name": app_name,
        "train_eval_set": eval_set_name,
    }

    if multi_judge:
        config["eval_config"]["criteria"]["multi_judge_quality"] = 0.5
        config["eval_config"]["custom_metrics"] = {
            "multi_judge_quality": {
                "code_config": {"name": "wrangler.multi_judge.evaluate"},
                "description": "Multi-model ensemble quality score",
            }
        }

    if train_eval_case_ids is not None:
        config["train_eval_case_ids"] = train_eval_case_ids
    if validation_eval_case_ids is not None:
        config["validation_eval_case_ids"] = validation_eval_case_ids

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "sampler_config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

    return config
