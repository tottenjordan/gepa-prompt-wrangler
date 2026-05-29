#!/usr/bin/env python3
"""Generate Vertex AI evalset.json files for all model agents from eval_cases.yaml.

Usage:
    python examples/multi_model_agents/scripts/generate_evalsets.py

Reads eval_cases.yaml (which has tier/category metadata on each case) and
produces a balanced evalset for each model agent (lite, flash, pro, sonnet,
opus), writing to their respective *_opt/ dirs.
"""

import json
from pathlib import Path

import yaml

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
EVAL_DATA_DIR = EXAMPLE_ROOT / "eval_data"
AGENTS_DIR = EXAMPLE_ROOT / "agents"

MODELS = ["lite", "flash", "pro", "sonnet", "opus"]


def load_yaml_cases() -> list[dict]:
    yaml_path = EVAL_DATA_DIR / "eval_cases.yaml"
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    return data["eval_cases"]


def select_cases(all_cases: list[dict]) -> list[tuple[str, dict]]:
    selected = []
    for case in all_cases:
        tier = case.get("tier", "low")
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

    tier_counts: dict[str, int] = {}
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
