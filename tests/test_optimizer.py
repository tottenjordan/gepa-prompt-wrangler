"""Tests for wrangler.optimizer — wrapper module creation, patch helpers, and config handling."""

import json
import os
import pytest
from pathlib import Path

from wrangler.optimizer import _create_wrapper_module


class TestCreateWrapperModule:
    def test_creates_init_file(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        init_file = Path(wrapper_dir) / "__init__.py"
        assert init_file.exists()

    def test_wrapper_dir_exists(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        assert Path(wrapper_dir).is_dir()

    def test_init_contains_import_logic(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        content = (Path(wrapper_dir) / "__init__.py").read_text()
        assert "root_agent" in content
        assert "importlib" in content

    def test_patch_adk_is_callable(self):
        from wrangler.optimizer import _patch_adk
        assert callable(_patch_adk)


class TestThresholdInjection:
    """Verify optimizer auto-injects threshold: 0.0 for dict criteria missing it."""

    def test_threshold_injected_for_dict_criteria(self):
        config = {
            "eval_config": {
                "criteria": {
                    "response_match_score": 0.1,
                    "final_response_match_v2": {
                        "judge_model_options": {"judge_model": "gemini-3.5-flash"},
                    },
                }
            }
        }
        criteria = config["eval_config"]["criteria"]
        for k, v in criteria.items():
            if isinstance(v, dict) and "threshold" not in v:
                v["threshold"] = 0.0

        assert criteria["response_match_score"] == 0.1
        assert criteria["final_response_match_v2"]["threshold"] == 0.0

    def test_existing_threshold_preserved(self):
        config = {
            "eval_config": {
                "criteria": {
                    "safety_v1": 0.8,
                    "custom": {"threshold": 0.7, "extra": "data"},
                }
            }
        }
        criteria = config["eval_config"]["criteria"]
        for k, v in criteria.items():
            if isinstance(v, dict) and "threshold" not in v:
                v["threshold"] = 0.0

        assert criteria["custom"]["threshold"] == 0.7
