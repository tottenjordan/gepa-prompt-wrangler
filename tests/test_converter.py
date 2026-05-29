"""Tests for eval data format conversion."""

import json
import pytest
from pathlib import Path

import yaml


class TestConverter:
    def test_load_simplified_yaml(self, tmp_path):
        from wrangler.converter import load_eval_file

        data = {
            "eval_cases": [
                {
                    "prompt": "Find flights from SFO to JFK",
                    "expected_response": "Flights found.",
                    "expected_tools": [{"name": "search_flights", "args": {"origin": "SFO"}}],
                },
            ]
        }
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(data))

        cases = load_eval_file(str(path))
        assert len(cases) == 1
        assert cases[0]["prompt"] == "Find flights from SFO to JFK"

    def test_load_adk_json(self, tmp_path):
        from wrangler.converter import load_eval_file

        data = [
            {
                "query": "Hello",
                "reference": "Hi there",
                "expected_tool_use": [],
            }
        ]
        path = tmp_path / "test.json"
        path.write_text(json.dumps(data))

        cases = load_eval_file(str(path))
        assert len(cases) == 1
        assert cases[0].get("prompt") == "Hello" or cases[0].get("query") == "Hello"

    def test_auto_detect_yaml(self, tmp_path):
        from wrangler.converter import load_eval_file

        data = {"eval_cases": [{"prompt": "test", "expected_response": "ok"}]}
        path = tmp_path / "test.yml"
        path.write_text(yaml.dump(data))

        cases = load_eval_file(str(path))
        assert len(cases) == 1

    def test_missing_file_raises(self, tmp_path):
        from wrangler.converter import load_eval_file
        with pytest.raises((FileNotFoundError, OSError)):
            load_eval_file(str(tmp_path / "nonexistent.yaml"))

    def test_cases_have_prompt(self, tmp_path):
        from wrangler.converter import load_eval_file

        data = {"eval_cases": [{"prompt": "test q", "expected_response": "test a"}]}
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(data))

        cases = load_eval_file(str(path))
        assert "prompt" in cases[0]

    def test_category_and_tier_loaded_from_yaml(self, tmp_path):
        from wrangler.converter import load_eval_file

        data = {
            "eval_cases": [
                {
                    "prompt": "Find flights",
                    "expected_response": "Flights found.",
                    "tier": "low",
                    "category": "search",
                },
            ]
        }
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(data))

        cases = load_eval_file(str(path))
        assert cases[0]["tier"] == "low"
        assert cases[0]["category"] == "search"

    def test_to_adk_evalset_preserves_category_and_tier(self):
        from wrangler.converter import to_adk_evalset

        cases = [
            {
                "query": "Find flights",
                "expected_response": "Flights found.",
                "expected_tools": [],
                "category": "search",
                "tier": "low",
            },
        ]
        result = to_adk_evalset(cases)
        assert result[0]["category"] == "search"
        assert result[0]["tier"] == "low"

    def test_to_adk_evalset_omits_empty_category(self):
        from wrangler.converter import to_adk_evalset

        cases = [{"query": "test", "expected_response": "ok", "expected_tools": []}]
        result = to_adk_evalset(cases)
        assert "category" not in result[0]
        assert "tier" not in result[0]


class TestGepaEvalsetGeneration:
    def test_category_in_session_input(self, tmp_path):
        from wrangler.converter import generate_gepa_evalset

        cases = [
            {
                "prompt": "Find flights",
                "expected_response": "Found.",
                "tier": "low",
                "category": "search",
                "expected_tools": [],
            },
        ]
        evalset_path = generate_gepa_evalset(
            cases, str(tmp_path), eval_set_id="test", app_name="test_opt", count=1,
        )
        with open(evalset_path) as f:
            data = json.load(f)

        case = data["eval_cases"][0]
        assert case["session_input"]["category"] == "search"
        assert case["session_input"]["tier"] == "low"

    def test_category_in_eval_id(self, tmp_path):
        from wrangler.converter import generate_gepa_evalset

        cases = [
            {
                "prompt": "Find flights",
                "expected_response": "Found.",
                "tier": "low",
                "category": "search",
                "expected_tools": [],
            },
        ]
        evalset_path = generate_gepa_evalset(
            cases, str(tmp_path), eval_set_id="test", app_name="test_opt", count=1,
        )
        with open(evalset_path) as f:
            data = json.load(f)

        assert data["eval_cases"][0]["eval_id"] == "case_1_low_search"

    def test_eval_id_without_category(self, tmp_path):
        from wrangler.converter import generate_gepa_evalset

        cases = [
            {
                "prompt": "Find flights",
                "expected_response": "Found.",
                "tier": "low",
                "expected_tools": [],
            },
        ]
        evalset_path = generate_gepa_evalset(
            cases, str(tmp_path), eval_set_id="test", app_name="test_opt", count=1,
        )
        with open(evalset_path) as f:
            data = json.load(f)

        assert data["eval_cases"][0]["eval_id"] == "case_1_low"


class TestSamplerConfig:
    def test_default_criteria_include_rubrics(self):
        from wrangler.converter import generate_sampler_config

        config = generate_sampler_config("test_opt")
        criteria = config["eval_config"]["criteria"]

        assert "rubric_based_final_response_quality_v1" in criteria
        assert "rubric_based_tool_use_quality_v1" in criteria

        rubrics = criteria["rubric_based_final_response_quality_v1"]["rubrics"]
        rubric_ids = [r["rubric_id"] for r in rubrics]
        assert "instruction_adherence" in rubric_ids
        assert "completeness" in rubric_ids

    def test_judge_model_propagated(self):
        from wrangler.converter import generate_sampler_config

        config = generate_sampler_config("test_opt", judge_model="gemini-2.5-flash")
        criteria = config["eval_config"]["criteria"]

        assert criteria["final_response_match_v2"]["judge_model_options"]["judge_model"] == "gemini-2.5-flash"
        assert criteria["rubric_based_final_response_quality_v1"]["judge_model_options"]["judge_model"] == "gemini-2.5-flash"

    def test_multi_judge_enabled(self):
        from wrangler.converter import generate_sampler_config

        config = generate_sampler_config("test_opt", multi_judge=True)
        criteria = config["eval_config"]["criteria"]

        assert "multi_judge_quality" in criteria
        assert "custom_metrics" in config["eval_config"]
        assert "multi_judge_quality" in config["eval_config"]["custom_metrics"]

    def test_multi_judge_disabled_by_default(self):
        from wrangler.converter import generate_sampler_config

        config = generate_sampler_config("test_opt")
        assert "custom_metrics" not in config["eval_config"]

    def test_writes_to_disk(self, tmp_path):
        from wrangler.converter import generate_sampler_config

        config = generate_sampler_config("test_opt", output_dir=str(tmp_path))
        config_path = tmp_path / "sampler_config.json"
        assert config_path.exists()

        with open(config_path) as f:
            loaded = json.load(f)
        assert loaded["app_name"] == "test_opt"
        assert "rubric_based_final_response_quality_v1" in loaded["eval_config"]["criteria"]
