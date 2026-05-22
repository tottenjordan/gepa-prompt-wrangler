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
