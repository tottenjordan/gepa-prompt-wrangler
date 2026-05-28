#!/usr/bin/env python3
"""Generate Vertex AI evalset.json files for all model agents from eval_cases.yaml.

Usage:
    python examples/multi_model_agents/scripts/generate_evalsets.py

Reads eval_cases.yaml and produces a balanced 20-case evalset for each model
agent (lite, flash, pro, sonnet, opus), writing to their respective *_opt/ dirs.
"""

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
EVAL_DATA_DIR = EXAMPLE_ROOT / "eval_data"
AGENTS_DIR = EXAMPLE_ROOT / "agents"

MODELS = ["lite", "flash", "pro", "sonnet", "opus"]

TIER_KEYWORDS = {
    "low": [
        "Find flights from SFO to JFK",
        "Search for hotels in New York",
        "What's the lodging policy limit?",
        "Is a $50 transport expense within policy?",
        "Can you help me write a Python script",
        "Is a $75 meal expense within policy?",
        "Is a $75.01 meal expense within policy?",
        "Cancel booking BK-DOESNOTEXIST",
        "Show me the details for booking BK-INVALID123",
        "Find flights from SFO to Denver",
        "Search for hotels in Boston",
    ],
    "medium": [
        "Search hotels in New York, then check if the nightly rate fits our lodging policy",
        "Submit a $45 meals expense for lunch meeting",
        "Submit a $90 supplies expense for office materials",
        "Check if a $100 meal and a $250 entertainment expense",
        "Book flight FL003 for Carol Davis, then immediately cancel",
        "Book hotel HT001 for Dave Wilson June 15-17",
        "Check if these expenses are within policy",
        "Find flights from Denver to Tokyo",
    ],
    "high": [
        "Book flight FL001 for Alice, check if Grand Hyatt",
        "Find the cheapest SFO-JFK flight, book it for Bob Smith",
        "Review EMP002's expense history, check all policy categories",
        "I have a $2000 budget for a London trip",
        "Compare flights from SFO to JFK vs LAX to ORD",
        "Pull expense histories for EMP001 and EMP002",
        "Look up booking BK-UNKNOWN1",
    ],
}


def load_yaml_cases() -> list[dict]:
    yaml_path = EVAL_DATA_DIR / "eval_cases.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    return data["eval_cases"]


def classify_case(case: dict) -> str | None:
    prompt = case["prompt"]
    for tier, keywords in TIER_KEYWORDS.items():
        for kw in keywords:
            if prompt.startswith(kw):
                return tier
    return None


def select_cases(all_cases: list[dict]) -> list[tuple[str, dict]]:
    buckets: dict[str, list[dict]] = {"low": [], "medium": [], "high": []}

    for case in all_cases:
        tier = classify_case(case)
        if tier:
            buckets[tier].append(case)

    selected = []
    for tier, cases in buckets.items():
        for case in cases:
            selected.append((tier, case))

    return selected


def build_eval_case(idx: int, tier: str, case: dict, app_name: str) -> dict:
    tool_uses = []
    for tool in case.get("expected_tools", []):
        tool_entry = {"name": tool["name"]}
        if tool.get("args"):
            tool_entry["args"] = tool["args"]
        tool_uses.append(tool_entry)

    return {
        "eval_id": f"case_{idx}_{tier}",
        "conversation": [
            {
                "user_content": {
                    "parts": [{"text": case["prompt"]}],
                    "role": "user",
                },
                "final_response": {
                    "parts": [{"text": case["expected_response"]}],
                    "role": "model",
                },
                "intermediate_data": {
                    "tool_uses": tool_uses,
                },
            }
        ],
        "session_input": {
            "app_name": app_name,
            "user_id": "eval_user",
        },
    }


def generate_evalset(selected: list[tuple[str, dict]], model: str) -> dict:
    app_name = f"{model}_opt"
    eval_cases = []
    for idx, (tier, case) in enumerate(selected, start=1):
        eval_cases.append(build_eval_case(idx, tier, case, app_name))

    return {
        "eval_set_id": f"{model}_eval_set",
        "eval_cases": eval_cases,
    }


def main():
    all_cases = load_yaml_cases()
    selected = select_cases(all_cases)

    tier_counts = {}
    for tier, _ in selected:
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    print(f"Loaded {len(all_cases)} eval cases from eval_cases.yaml")
    print(f"Selected {len(selected)} cases: {tier_counts}")

    for model in MODELS:
        evalset = generate_evalset(selected, model)
        out_dir = AGENTS_DIR / f"{model}_opt"
        out_path = out_dir / f"{model}_eval_set.evalset.json"

        with open(out_path, "w") as f:
            json.dump(evalset, f, indent=2)
            f.write("\n")

        print(f"  {out_path.relative_to(EXAMPLE_ROOT)} ({len(evalset['eval_cases'])} cases)")

    print("\nDone. All evalset.json files regenerated.")


if __name__ == "__main__":
    main()
