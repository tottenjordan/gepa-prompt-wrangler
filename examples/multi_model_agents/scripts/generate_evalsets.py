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

# Stratified 70/30 train/val split — deterministic, balanced across tier and category.
# Booking (2 cases) is train-only; all other categories appear in both sets.
TRAIN_CASE_IDS = [
    "case_1_low_search", "case_2_low_search", "case_4_low_search",
    "case_5_low_search", "case_6_low_search", "case_30_low_search",
    "case_7_low_policy", "case_8_low_policy",
    "case_10_low_booking",
    "case_11_low_expense",
    "case_12_low_error_handling", "case_13_low_error_handling",
    "case_14_low_error_handling", "case_37_low_error_handling",
    "case_34_low_boundary",
    "case_15_medium_expense", "case_16_medium_expense", "case_23_medium_expense",
    "case_17_medium_planning", "case_18_medium_planning",
    "case_19_medium_policy",
    "case_22_medium_booking",
    "case_32_medium_cancellation",
    "case_36_medium_boundary",
    "case_24_high_planning", "case_25_high_planning", "case_28_high_planning",
    "case_27_high_expense",
]

VAL_CASE_IDS = [
    "case_3_low_search", "case_31_low_search",
    "case_9_low_policy",
    "case_35_low_boundary",
    "case_38_low_error_handling", "case_40_low_error_handling",
    "case_20_medium_expense",
    "case_21_medium_planning",
    "case_33_medium_cancellation",
    "case_39_medium_error_handling",
    "case_26_high_planning",
    "case_29_high_expense",
]


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

    category = case.get("category", "")
    eval_id = f"case_{idx}_{tier}_{category}" if category else f"case_{idx}_{tier}"

    return {
        "eval_id": eval_id,
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
            "category": case.get("category", ""),
            "tier": tier,
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

        # Validate split covers all cases
        all_ids = {c["eval_id"] for c in evalset["eval_cases"]}
        train_set = set(TRAIN_CASE_IDS)
        val_set = set(VAL_CASE_IDS)
        assert train_set & val_set == set(), f"Train/val overlap: {train_set & val_set}"
        assert train_set | val_set == all_ids, f"Split missing cases: {all_ids - (train_set | val_set)}"

        # Write sampler config with train/val split
        eval_set_name = f"{model}_eval_set"
        app_name = f"{model}_opt"
        sampler_config = {
            "eval_config": {
                "criteria": {
                    "response_match_score": 0.1,
                    "final_response_match_v2": {
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-2.5-pro"},
                    },
                    "safety_v1": 0.8,
                    "rubric_based_final_response_quality_v1": {
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-2.5-pro"},
                        "rubrics": [
                            {
                                "rubric_id": "instruction_adherence",
                                "rubric_content": {
                                    "text_property": (
                                        "The agent's response follows all instructions"
                                        " in the system prompt, including formatting,"
                                        " tone, and content requirements."
                                    )
                                },
                                "type": "INSTRUCTION_ADHERENCE",
                            },
                            {
                                "rubric_id": "completeness",
                                "rubric_content": {
                                    "text_property": (
                                        "The agent's response fully addresses all parts"
                                        " of the user's request without omitting"
                                        " relevant information."
                                    )
                                },
                                "type": "FINAL_RESPONSE_QUALITY",
                            },
                        ],
                    },
                    "rubric_based_tool_use_quality_v1": {
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-2.5-pro"},
                    },
                }
            },
            "app_name": app_name,
            "train_eval_set": eval_set_name,
            "train_eval_case_ids": TRAIN_CASE_IDS,
            "validation_eval_case_ids": VAL_CASE_IDS,
        }
        config_path = out_dir / "sampler_config.json"
        with open(config_path, "w") as f:
            json.dump(sampler_config, f, indent=2)
            f.write("\n")

    print(f"\nTrain/val split: {len(TRAIN_CASE_IDS)} train / {len(VAL_CASE_IDS)} val")
    print("Done. All evalset.json and sampler_config.json files regenerated.")


if __name__ == "__main__":
    main()
