"""Tests for wrangler.reporting.analyzer — experiment analysis and report generation."""

import pytest

from wrangler.reporting.analyzer import (
    PairAnalysis,
    ExperimentAnalysis,
    _prompt_diff_summary,
    _find_removed_content,
    _analyze_tool_keywords,
    format_analysis_report,
    GepaRunStats,
    _format_tool_audit,
    METRIC_LABELS,
)


def _make_pair(
    pair_id="flash",
    model="gemini-3.5-flash",
    before=None,
    after=None,
    original="Be helpful.",
    optimized="Be very helpful and thorough.",
):
    return PairAnalysis(
        pair_id=pair_id,
        model=model,
        before=before or {"quality": 0.7, "safety": 0.9},
        after=after or {"quality": 0.85, "safety": 0.88},
        original_prompt=original,
        optimized_prompt=optimized,
    )


class TestPairAnalysis:
    def test_deltas(self):
        p = _make_pair(before={"q": 0.7, "s": 0.9}, after={"q": 0.85, "s": 0.88})
        assert abs(p.deltas["q"] - 0.15) < 0.001
        assert abs(p.deltas["s"] - (-0.02)) < 0.001

    def test_avg_before(self):
        p = _make_pair(before={"a": 0.6, "b": 0.8})
        assert abs(p.avg_before - 0.7) < 0.001

    def test_avg_after(self):
        p = _make_pair(after={"a": 0.9, "b": 1.0})
        assert abs(p.avg_after - 0.95) < 0.001

    def test_avg_before_empty(self):
        p = PairAnalysis(pair_id="x", model="m", before={}, after={})
        assert p.avg_before == 0.0

    def test_improved_metrics(self):
        p = _make_pair(before={"q": 0.7, "s": 0.9}, after={"q": 0.85, "s": 0.9})
        assert "q" in p.improved_metrics
        assert "s" not in p.improved_metrics

    def test_degraded_metrics(self):
        p = _make_pair(before={"q": 0.7, "s": 0.9}, after={"q": 0.85, "s": 0.8})
        assert "s" in p.degraded_metrics
        assert "q" not in p.degraded_metrics

    def test_prompt_char_delta(self):
        p = _make_pair(original="short", optimized="much longer text here")
        assert p.prompt_char_delta == len("much longer text here") - len("short")

    def test_prompt_char_pct(self):
        p = _make_pair(original="1234567890", optimized="12345678901234567890")
        assert abs(p.prompt_char_pct - 100.0) < 0.1

    def test_prompt_char_pct_empty_original(self):
        p = _make_pair(original="", optimized="something")
        assert p.prompt_char_pct == 0.0


class TestExperimentAnalysis:
    def test_overall_improved_true(self):
        p1 = _make_pair(before={"q": 0.5}, after={"q": 0.9})
        p2 = _make_pair(before={"q": 0.7}, after={"q": 0.75})
        ea = ExperimentAnalysis(experiment_name="test", pairs=[p1, p2])
        assert ea.overall_improved is True

    def test_overall_improved_false(self):
        p1 = _make_pair(before={"q": 0.9}, after={"q": 0.5})
        ea = ExperimentAnalysis(experiment_name="test", pairs=[p1])
        assert ea.overall_improved is False


class TestPromptDiffSummary:
    def test_identical_prompts(self):
        result = _prompt_diff_summary("hello world", "hello world")
        assert "identical" in result

    def test_empty_original(self):
        result = _prompt_diff_summary("", "something")
        assert "no prompt comparison" in result

    def test_empty_optimized(self):
        result = _prompt_diff_summary("something", "")
        assert "no prompt comparison" in result

    def test_diff_shows_line_counts(self):
        orig = "line1\nline2\nline3"
        opt = "line1\nmodified\nline3\nnew line"
        result = _prompt_diff_summary(orig, opt)
        assert "+" in result and "-" in result


class TestFindRemovedContent:
    def test_finds_policy_keyword(self):
        orig = "The maximum budget is $5000.\nBe helpful."
        opt = "Be helpful and thorough."
        removed = _find_removed_content(orig, opt)
        assert any("maximum" in r.lower() or "$" in r for r in removed)

    def test_empty_inputs(self):
        assert _find_removed_content("", "something") == []
        assert _find_removed_content("something", "") == []

    def test_nothing_removed(self):
        text = "Use the tool to search flights."
        assert _find_removed_content(text, text) == []

    def test_short_lines_ignored(self):
        orig = "Hi\nThe policy requires approval for amounts over $10000."
        opt = "Be helpful."
        removed = _find_removed_content(orig, opt)
        assert not any(r == "Hi" for r in removed)


class TestAnalyzeToolKeywords:
    def test_detects_added_keywords(self):
        result = _analyze_tool_keywords("Be helpful.", "Use the search tool to book flights.")
        assert "search" in result["added"]
        assert "book" in result["added"]

    def test_detects_dropped_keywords(self):
        result = _analyze_tool_keywords("Use the search tool.", "Be helpful.")
        assert "search" in result["dropped"]
        assert "tool" in result["dropped"]

    def test_no_changes(self):
        text = "Use the search tool."
        result = _analyze_tool_keywords(text, text)
        assert result["added"] == []
        assert result["dropped"] == []


class TestFormatAnalysisReport:
    def _make_analysis(self):
        p1 = _make_pair(
            pair_id="flash",
            model="gemini-3.5-flash",
            before={"final_response_quality_v1": 0.7, "safety_v1": 0.9},
            after={"final_response_quality_v1": 0.85, "safety_v1": 0.88},
        )
        p2 = _make_pair(
            pair_id="sonnet",
            model="claude-sonnet-4-6",
            before={"final_response_quality_v1": 0.8, "safety_v1": 0.95},
            after={"final_response_quality_v1": 0.82, "safety_v1": 0.93},
        )
        return ExperimentAnalysis(
            experiment_name="test-experiment",
            pairs=[p1, p2],
            thresholds={"final_response_quality_v1": 0.7, "safety_v1": 0.8},
        )

    def test_contains_summary_section(self):
        report = format_analysis_report(self._make_analysis())
        assert "## Summary" in report

    def test_contains_per_metric_breakdown(self):
        report = format_analysis_report(self._make_analysis())
        assert "## Per-Metric Breakdown" in report

    def test_contains_prompt_changes(self):
        report = format_analysis_report(self._make_analysis())
        assert "## Prompt Changes" in report

    def test_contains_threshold_alignment(self):
        report = format_analysis_report(self._make_analysis())
        assert "## Threshold Alignment Check" in report

    def test_contains_recommendations(self):
        report = format_analysis_report(self._make_analysis())
        assert "## Recommendations" in report

    def test_degraded_pair_shows_diagnosis(self):
        p = _make_pair(
            before={"safety_v1": 0.95},
            after={"safety_v1": 0.80},
        )
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        report = format_analysis_report(analysis)
        assert "## Degradation Diagnosis" in report

    def test_no_degradation_section_when_all_improved(self):
        p = _make_pair(
            before={"quality": 0.5},
            after={"quality": 0.9},
        )
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        report = format_analysis_report(analysis)
        assert "## Degradation Diagnosis" not in report

    def test_cost_efficiency_section(self):
        p = _make_pair(model="gemini-3.5-flash")
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        report = format_analysis_report(analysis)
        assert "## Cost Efficiency" in report


class TestFormatToolAudit:
    def test_with_log_stats(self):
        p = _make_pair()
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        stats = {
            "flash": GepaRunStats(
                pair_id="flash",
                app_name="flash_opt",
                log_exists=True,
                total_lines=100,
                error_count=5,
                warning_count=10,
                timeout_count=2,
                tool_failure_count=1,
            )
        }
        lines = _format_tool_audit(analysis, stats)
        text = "\n".join(lines)
        assert "GEPA Run Log Summary" in text
        assert "2 total MCP timeouts" in text

    def test_without_logs(self):
        p = _make_pair()
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        stats = {"flash": GepaRunStats(pair_id="flash", app_name="flash_opt", log_exists=False)}
        lines = _format_tool_audit(analysis, stats)
        text = "\n".join(lines)
        assert "No GEPA run logs found" in text

    def test_tool_keyword_preservation_table(self):
        p = _make_pair(
            original="Use the search tool.", optimized="Search for flights and book them."
        )
        analysis = ExperimentAnalysis(experiment_name="test", pairs=[p])
        stats = {"flash": GepaRunStats(pair_id="flash", app_name="flash_opt")}
        lines = _format_tool_audit(analysis, stats)
        text = "\n".join(lines)
        assert "Tool Keyword Preservation" in text
