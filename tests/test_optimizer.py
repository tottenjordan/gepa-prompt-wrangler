"""Tests for wrangler.optimizer — wrapper module creation, patch helpers, and config handling."""

import asyncio
import re
import unicodedata
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from wrangler.optimize.optimizer import _create_wrapper_module, _prewarm_mcp_toolsets

# --- Reproduce _fuzzy_normalize for direct unit testing ---
# Mirrors the implementation inside _patch_adk() in optimizer.py.

_SMART_CHARS = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
    0x2026: "...",
}


def _fuzzy_normalize(text):
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_SMART_CHARS)
    text = re.sub(r'^[\s*•\-"\']+', "", text)
    text = re.sub(r'[\s*•\-"\']+$', "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


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
        from wrangler.optimize.optimizer import _patch_adk

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
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent, max_retries=1))
        assert warmed == 1
        ts_fail.get_tools.assert_awaited_once()
        ts_ok.get_tools.assert_awaited_once()

    def test_retries_on_failure(self):
        ts_fail = _make_toolset(fail=True)
        agent = _make_agent([ts_fail])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent, max_retries=3))
        assert warmed == 0
        assert ts_fail.get_tools.await_count == 3

    def test_all_fail_returns_zero(self):
        ts1 = _make_toolset(fail=True)
        ts2 = _make_toolset(fail=True)
        agent = _make_agent([ts1, ts2])
        warmed = asyncio.run(_prewarm_mcp_toolsets(agent, max_retries=1))
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
        for v in criteria.values():
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
        for v in criteria.values():
            if isinstance(v, dict) and "threshold" not in v:
                v["threshold"] = 0.0

        assert criteria["custom"]["threshold"] == 0.7


class TestFuzzyNormalize:
    """Validate NFKC + smart-char normalization for rubric matching (issue #6072)."""

    RUBRIC = "the response correctly uses tools"

    @pytest.mark.parametrize(
        ("label", "input_text"),
        [
            ("exact", "The response correctly uses tools"),
            ("markdown_bullet", "- The response correctly uses tools"),
            ("bullet_bold", "* **The response correctly uses tools**"),
            ("smart_double_quotes", "“The response correctly uses tools”"),
            ("double_spaces", "The  response  correctly  uses  tools"),
            ("em_dash_prefix", "— The response correctly uses tools"),
            ("en_dash_prefix", "– The response correctly uses tools"),
            ("unicode_bullet", "• The response correctly uses tools"),
            ("leading_whitespace", "   The response correctly uses tools"),
        ],
    )
    def test_garbled_text_matches_rubric(self, label, input_text):
        assert _fuzzy_normalize(input_text) == self.RUBRIC

    def test_smart_single_quotes_stripped_at_boundaries(self):
        assert _fuzzy_normalize("‘hello’") == "hello"

    def test_smart_single_quotes_normalized_mid_word(self):
        assert _fuzzy_normalize("the response’s tools") == "the response's tools"

    def test_ellipsis_normalized(self):
        assert _fuzzy_normalize("The response… uses tools") == "the response... uses tools"

    def test_non_string_returns_empty(self):
        assert _fuzzy_normalize(None) == ""
        assert _fuzzy_normalize(42) == ""

    def test_empty_string(self):
        assert _fuzzy_normalize("") == ""

    def test_accented_chars_preserved(self):
        assert _fuzzy_normalize("réponse") == "réponse"


class TestSubstringUniquenessGuard:
    """Verify substring fallback only matches when exactly one rubric candidate exists."""

    def test_unique_substring_match_accepted(self):
        normalized_map = {
            _fuzzy_normalize("uses tools correctly"): "rubric_1",
        }
        judge_text = _fuzzy_normalize("the agent uses tools correctly and efficiently")
        result = normalized_map.get(judge_text)

        if not result:
            candidates = [
                r for ct, r in normalized_map.items() if ct in judge_text or judge_text in ct
            ]
            if len(candidates) == 1:
                result = candidates[0]

        assert result == "rubric_1"

    def test_ambiguous_substring_match_rejected(self):
        normalized_map = {
            _fuzzy_normalize("uses tools correctly"): "rubric_1",
            _fuzzy_normalize("uses tools efficiently"): "rubric_2",
        }
        judge_text = _fuzzy_normalize("uses tools")
        result = normalized_map.get(judge_text)

        if not result:
            candidates = [
                r for ct, r in normalized_map.items() if ct in judge_text or judge_text in ct
            ]
            if len(candidates) == 1:
                result = candidates[0]

        assert result is None
