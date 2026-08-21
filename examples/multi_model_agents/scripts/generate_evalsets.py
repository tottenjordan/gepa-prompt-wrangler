#!/usr/bin/env python3
"""Generate Vertex AI evalset.json files for all model agents from eval_cases.yaml.

Usage:
    python examples/multi_model_agents/scripts/generate_evalsets.py

Reads eval_cases.yaml (which has tier/category metadata on each case) and
produces a balanced evalset for each model agent (lite, flash, pro, sonnet,
opus, plus the opus48 variant), writing to their respective *_opt/ dirs.

Sampler configs are HAND-TUNED and are the project's single source of truth
(per CLAUDE.md). This script will NOT overwrite an existing sampler_config.json;
it only bootstraps one when absent. The train/val split-coverage assertions run
regardless, validating the generated evalset itself.
"""

import json
from pathlib import Path

import yaml

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
EVAL_DATA_DIR = EXAMPLE_ROOT / "eval_data"
AGENTS_DIR = EXAMPLE_ROOT / "agents"

MODELS = ["lite", "flash", "pro", "sonnet", "opus"]

# Evalset generation targets. The 5 standard model agents derive their
# filename/eval_set_id/app_name from the model name; opus48 is a variant that
# reuses the opus agent + eval_set_id "opus_eval_set" but a distinct app_name.
EVALSET_TARGETS = [
    {
        "dir_name": f"{model}_opt",
        "app_name": f"{model}_opt",
        "eval_set_id": f"{model}_eval_set",
        "filename": f"{model}_eval_set.evalset.json",
    }
    for model in MODELS
] + [
    {
        "dir_name": "opus48_opt",
        "app_name": "opus48_opt",
        "eval_set_id": "opus_eval_set",
        "filename": "opus_eval_set.evalset.json",
    }
]

# Stratified ~77/23 train/val split — deterministic, balanced across all 8 categories.
# Every category has at least 1 case in val for coverage.
TRAIN_CASE_IDS = [
    "case_10_low_booking",
    "case_22_medium_booking",
    "case_40_medium_booking",
    "case_43_low_booking",
    "case_44_high_booking",
    "case_45_low_booking",
    "case_34_low_boundary",
    "case_35_low_boundary",
    "case_36_medium_boundary",
    "case_53_low_boundary",
    "case_54_medium_boundary",
    "case_55_low_boundary",
    "case_32_medium_cancellation",
    "case_33_medium_cancellation",
    "case_46_low_cancellation",
    "case_48_medium_cancellation",
    "case_50_high_cancellation",
    "case_51_low_cancellation",
    "case_12_low_error_handling",
    "case_37_low_error_handling",
    "case_38_low_error_handling",
    "case_39_medium_error_handling",
    "case_61_low_error_handling",
    "case_64_low_error_handling",
    "case_11_low_expense",
    "case_15_medium_expense",
    "case_20_medium_expense",
    "case_23_medium_expense",
    "case_29_high_expense",
    "case_63_medium_expense",
    "case_17_medium_planning",
    "case_18_medium_planning",
    "case_21_medium_planning",
    "case_25_high_planning",
    "case_28_high_planning",
    "case_62_high_planning",
    "case_7_low_policy",
    "case_8_low_policy",
    "case_9_low_policy",
    "case_59_medium_policy",
    "case_60_low_policy",
    "case_1_low_search",
    "case_2_low_search",
    "case_3_low_search",
    "case_4_low_search",
    "case_5_low_search",
    "case_6_low_search",
    "case_30_low_search",
    "case_57_low_search",
]

VAL_CASE_IDS = [
    "case_41_low_booking",
    "case_42_medium_booking",
    "case_52_low_boundary",
    "case_56_medium_boundary",
    "case_47_medium_cancellation",
    "case_49_medium_cancellation",
    "case_13_low_error_handling",
    "case_14_low_error_handling",
    "case_16_medium_expense",
    "case_27_high_expense",
    "case_24_high_planning",
    "case_26_high_planning",
    "case_19_medium_policy",
    "case_31_low_search",
    "case_58_low_search",
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


def generate_evalset(selected: list[tuple[str, dict]], app_name: str, eval_set_id: str) -> dict:
    eval_cases = []
    for idx, (tier, case) in enumerate(selected, start=1):
        eval_cases.append(build_eval_case(idx, tier, case, app_name))

    return {
        "eval_set_id": eval_set_id,
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

    for target in EVALSET_TARGETS:
        out_dir = AGENTS_DIR / target["dir_name"]
        app_name = target["app_name"]
        eval_set_name = target["eval_set_id"]

        evalset = generate_evalset(selected, app_name, eval_set_name)
        out_path = out_dir / target["filename"]

        with open(out_path, "w") as f:
            json.dump(evalset, f, indent=2)
            f.write("\n")

        print(f"  {out_path.relative_to(EXAMPLE_ROOT)} ({len(evalset['eval_cases'])} cases)")

        # Validate split covers all cases (independent of sampler config write)
        all_ids = {c["eval_id"] for c in evalset["eval_cases"]}
        train_set = set(TRAIN_CASE_IDS)
        val_set = set(VAL_CASE_IDS)
        if train_set & val_set:
            raise ValueError(f"Train/val overlap: {train_set & val_set}")
        if train_set | val_set != all_ids:
            raise ValueError(f"Split missing cases: {all_ids - (train_set | val_set)}")

        # Sampler configs are the hand-tuned source of truth — never overwrite.
        # Only bootstrap one when absent.
        config_path = out_dir / "sampler_config.json"
        if config_path.exists():
            print(
                f"    preserved existing sampler_config (source of truth): "
                f"{config_path.relative_to(EXAMPLE_ROOT)}"
            )
            continue

        sampler_config = {
            "eval_config": {
                "criteria": {
                    "response_match_score": 0.1,
                    "final_response_match_v2": {
                        "threshold": 0.3,
                        "judge_model_options": {"judge_model": "gemini-3.5-flash"},
                    },
                    "safety_v1": 0.8,
                    "hallucinations_v1": 0.8,
                    "rubric_based_final_response_quality_v1": {
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-3.5-flash"},
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
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-3.5-flash"},
                        "rubrics": [
                            {
                                "rubric_id": "correct_tool_selection",
                                "rubric_content": {"text_property": "Correct tools selected."},
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
            },
            "app_name": app_name,
            "train_eval_set": eval_set_name,
            "train_eval_case_ids": TRAIN_CASE_IDS,
            "validation_eval_case_ids": VAL_CASE_IDS,
        }
        with open(config_path, "w") as f:
            json.dump(sampler_config, f, indent=2)
            f.write("\n")
        print(f"    bootstrapped sampler_config: {config_path.relative_to(EXAMPLE_ROOT)}")

    print(f"\nTrain/val split: {len(TRAIN_CASE_IDS)} train / {len(VAL_CASE_IDS)} val")
    print(
        "Done. All evalset.json files regenerated; existing sampler_config.json "
        "files preserved (only absent ones bootstrapped)."
    )


if __name__ == "__main__":
    main()
