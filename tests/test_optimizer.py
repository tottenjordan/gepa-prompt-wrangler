"""Tests for wrangler.optimizer — wrapper module creation, patch helpers, and config handling."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.adk.evaluation.rubric_based_evaluator import _normalize_text

from wrangler.optimize.optimizer import (
    _create_wrapper_module,
    _prewarm_mcp_toolsets,
    _ToolsetFailureCounter,
)


def test_patch_adk_preserves_upstream_rubric_id_matching():
    """Patch 5 must not clobber upstream's rubric_id-based matching.

    ADK 2.7.1 (issue #6072, fixed 2026-07-31) matches rubric verdicts by
    rubric_id first, falling back to normalized text. An override derived
    from ADK 2.2 does text-only matching and silently discards the more
    reliable path, corrupting the scores GEPA optimizes against.
    """
    import inspect

    from google.adk.evaluation import rubric_based_evaluator as rbe

    from wrangler.optimize.optimizer import _patch_adk

    _patch_adk()

    src = inspect.getsource(rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score)
    assert "rubric_by_id" in src, (
        "convert_auto_rater_response_to_score was replaced by an override that "
        "lacks rubric_id matching"
    )


class TestToolsetFailureCounter:
    """A lost toolset is a warning in ADK and a wrong score in GEPA."""

    def _record(self, msg, *args, level=logging.WARNING):
        return logging.LogRecord("google_adk.x", level, __file__, 1, msg, args, None)

    def test_counts_the_adk_give_up_warning(self):
        counter = _ToolsetFailureCounter()
        counter.emit(
            self._record(
                "Failed to get tools from toolset %s: %s", "McpToolset", "BrokenResourceError"
            )
        )
        assert counter.count == 1

    def test_ignores_unrelated_warnings(self):
        counter = _ToolsetFailureCounter()
        counter.emit(self._record("Something else went wrong"))
        assert counter.count == 0

    def test_does_not_count_the_per_attempt_message(self):
        """ADK retries get_tools() once; only the final give-up costs the agent its tools.

        Counting `_execute_with_session`'s per-attempt log instead would have
        reported 12 lost toolsets on a run that actually lost 2.
        """
        counter = _ToolsetFailureCounter()
        counter.emit(
            self._record("Exception during MCP session execution: Failed to get tools: %s", "boom")
        )
        assert counter.count == 0

    def test_attached_to_the_adk_logger_it_sees_a_real_warning(self):
        """Guards the logger name and the fact that WARNING gets through."""
        counter = _ToolsetFailureCounter()
        log = logging.getLogger("google_adk.google.adk.agents.llm_agent")
        log.addHandler(counter)
        try:
            log.warning("Failed to get tools from toolset %s: %s", "McpToolset", "boom")
        finally:
            log.removeHandler(counter)
        assert counter.count == 1


class TestSafetyMetricPin:
    """Patch 6 — ADK asks for a safety metric version us-central1 will not serve."""

    def test_upstream_still_requests_the_unversioned_metric(self):
        """The premise of patch 6. Delete the patch when this stops holding.

        `PrebuiltMetric.SAFETY` carries no version, so the SDK resolves it
        client-side through METRIC_LATEST_SPEC_NAME. If upstream ever pins the
        version itself, this assertion fails and patch 6 becomes dead weight —
        which is the failure mode that made patch 5 harmful.
        """
        import inspect

        from google.adk.evaluation import safety_evaluator as safety_mod

        # The module source, not the bound method: another test in this file
        # calls _patch_adk(), which replaces the method for the whole session.
        src = inspect.getsource(safety_mod)
        assert "PrebuiltMetric.SAFETY," in src, (
            "ADK no longer passes the unversioned safety metric — re-run the "
            "probe in docs/notes/adk-patch-status.md and consider removing patch 6"
        )

    def test_unversioned_safety_resolves_to_a_version_us_central1_rejects(self):
        """The other half of the premise: the SDK's 'latest' is ahead of the region.

        This is what produced `400 INVALID_ARGUMENT: Unsupported predefined
        metric: safety_v3` on every GEPA case.
        """
        from google.adk.dependencies.vertexai import vertexai

        unversioned = vertexai.types.PrebuiltMetric.SAFETY
        assert unversioned._get_api_metric_spec_name() != "safety_v1"

    def test_patch_pins_the_evaluator_to_safety_v1(self, monkeypatch):
        from google.adk.evaluation import safety_evaluator as safety_mod
        from google.adk.evaluation import vertex_ai_eval_facade as facade_mod

        from wrangler.optimize.optimizer import _patch_adk

        _patch_adk()

        captured = {}

        class RecordingFacade:
            def __init__(self, threshold, metric_name):
                captured["threshold"] = threshold
                captured["metric_name"] = metric_name

            def evaluate_invocations(self, *args):
                captured["args"] = args
                return "result"

        monkeypatch.setattr(facade_mod, "_SingleTurnVertexAiEvalFacade", RecordingFacade)

        evaluator = object.__new__(safety_mod.SafetyEvaluatorV1)
        evaluator._threshold = 0.95

        assert evaluator.evaluate_invocations(["actual"]) == "result"
        assert captured["threshold"] == 0.95
        assert captured["metric_name"]._get_api_metric_spec_name() == "safety_v1"
        assert captured["args"] == (["actual"], None, None)


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
    """Guard upstream ADK's rubric-text normalization (issue #6072).

    These cases are why wrangler once shipped its own ``_fuzzy_normalize``
    override. ADK 2.7.1 handles all of them, so the override was deleted --
    these tests are the canary that a future ADK bump has not regressed it.
    """

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
        assert _normalize_text(input_text) == self.RUBRIC

    def test_smart_single_quotes_stripped_at_boundaries(self):
        assert _normalize_text("‘hello’") == "hello"

    def test_smart_single_quotes_normalized_mid_word(self):
        assert _normalize_text("the response’s tools") == "the response's tools"

    def test_ellipsis_normalized(self):
        assert _normalize_text("The response… uses tools") == "the response... uses tools"

    def test_non_string_returns_empty(self):
        assert _normalize_text(None) == ""
        assert _normalize_text(42) == ""

    def test_empty_string(self):
        assert _normalize_text("") == ""

    def test_accented_chars_preserved(self):
        assert _normalize_text("réponse") == "réponse"
