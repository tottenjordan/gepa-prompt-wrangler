"""Tests for wrangler.optimizer — wrapper module creation, patch helpers, and config handling."""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from wrangler.optimizer import _create_wrapper_module, _prewarm_mcp_toolsets


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


def _make_toolset(tools=None, fail=False):
    """Create a mock MCP toolset (BaseToolset subclass)."""
    from google.adk.tools.base_toolset import BaseToolset
    ts = MagicMock(spec=BaseToolset)
    if fail:
        ts.get_tools = AsyncMock(side_effect=ConnectionError("timeout"))
    else:
        ts.get_tools = AsyncMock(return_value=tools or [MagicMock(name="tool_a")])
    return ts


def _make_agent(tools):
    agent = MagicMock()
    agent.tools = tools
    return agent


class TestPrewarmMcpToolsets:
    def test_warms_all_toolsets(self):
        ts1, ts2 = _make_toolset(), _make_toolset()
        agent = _make_agent([ts1, ts2])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent))
        assert warmed == 2
        ts1.get_tools.assert_awaited_once()
        ts2.get_tools.assert_awaited_once()

    def test_continues_on_failure(self):
        ts_ok = _make_toolset()
        ts_fail = _make_toolset(fail=True)
        agent = _make_agent([ts_fail, ts_ok])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent))
        assert warmed == 1
        ts_fail.get_tools.assert_awaited_once()
        ts_ok.get_tools.assert_awaited_once()

    def test_all_fail_returns_zero(self):
        ts1 = _make_toolset(fail=True)
        ts2 = _make_toolset(fail=True)
        agent = _make_agent([ts1, ts2])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent))
        assert warmed == 0

    def test_skips_non_toolset_tools(self):
        plain_tool = MagicMock()
        ts = _make_toolset()
        agent = _make_agent([plain_tool, ts])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent))
        assert warmed == 1
        assert not hasattr(plain_tool, "get_tools") or not plain_tool.get_tools.called

    def test_no_toolsets_returns_zero(self):
        agent = _make_agent([MagicMock(), MagicMock()])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent))
        assert warmed == 0


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
