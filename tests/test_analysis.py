"""Tests for wrangler.analysis — report generation and analysis functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from wrangler.reporting.analysis import (
    compute_tier_scores,
    generate_category_heatmap,
    generate_radar_chart,
    generate_tier_breakdown_chart,
    generate_tier_improvement_heatmap,
    normalize_agent_keys,
)
from wrangler.reporting.report_sections import (
    _cost_benefit_section,
    _prompt_evolution_summary,
    generate_agent_report,
    generate_comparison_report,
)


class TestComputeTierScores:
    def test_groups_by_tier(self):
        per_case = [
            {"quality": 0.8, "safety": 1.0},
            {"quality": 0.6, "safety": 0.9},
            {"quality": 0.9, "safety": 1.0},
        ]
        metadata = [
            {"tier": "low"},
            {"tier": "low"},
            {"tier": "high"},
        ]
        result = compute_tier_scores(per_case, metadata, "tier")
        assert "low" in result
        assert "high" in result
        assert abs(result["low"]["quality"] - 0.7) < 0.01
        assert abs(result["high"]["quality"] - 0.9) < 0.01

    def test_groups_by_category(self):
        per_case = [
            {"quality": 0.5},
            {"quality": 0.9},
            {"quality": 0.7},
        ]
        metadata = [
            {"category": "search"},
            {"category": "policy"},
            {"category": "search"},
        ]
        result = compute_tier_scores(per_case, metadata, "category")
        assert abs(result["search"]["quality"] - 0.6) < 0.01
        assert abs(result["policy"]["quality"] - 0.9) < 0.01

    def test_empty_inputs(self):
        assert compute_tier_scores([], [], "tier") == {}

    def test_mismatched_lengths(self):
        per_case = [{"quality": 0.8}]
        metadata = [{"tier": "low"}, {"tier": "high"}]
        result = compute_tier_scores(per_case, metadata, "tier")
        assert "low" in result
        assert "high" not in result

    def test_skips_empty_scores(self):
        per_case = [{"quality": 0.8}, {}]
        metadata = [{"tier": "low"}, {"tier": "low"}]
        result = compute_tier_scores(per_case, metadata, "tier")
        assert abs(result["low"]["quality"] - 0.8) < 0.01


class TestPromptEvolutionSummary:
    def test_expansion_ratio_displayed(self):
        original = "Short prompt."
        optimized = "A " * 250
        lines = _prompt_evolution_summary(original, optimized)
        joined = "\n".join(lines)
        assert "expansion" in joined.lower()

    def test_keywords_detected_when_added(self):
        original = "You are a helpful assistant."
        optimized = (
            "You are a helpful assistant. Use the tool for policy checks. Handle errors gracefully."
        )
        lines = _prompt_evolution_summary(original, optimized)
        joined = "\n".join(lines)
        assert "Tool-specific guidance" in joined
        assert "Domain policy knowledge" in joined
        assert "Error handling guidance" in joined

    def test_no_keywords_section_when_none_added(self):
        original = "Use the tool for policy checks. Be concise."
        optimized = "Use the tool for policy checks. Be concise and clear."
        lines = _prompt_evolution_summary(original, optimized)
        joined = "\n".join(lines)
        assert "Key additions by GEPA" not in joined

    def test_empty_original_no_division_error(self):
        lines = _prompt_evolution_summary("", "Some optimized prompt text")
        assert len(lines) > 0

    def test_case_insensitive_matching(self):
        original = "Use TOOL and POLICY guidance."
        optimized = "Use tool and policy guidance with more detail."
        lines = _prompt_evolution_summary(original, optimized)
        joined = "\n".join(lines)
        assert "Tool-specific guidance" not in joined
        assert "Domain policy knowledge" not in joined

    def test_returns_list_of_strings(self):
        lines = _prompt_evolution_summary("short", "longer optimized prompt")
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)
        assert any("Prompt Evolution Summary" in line for line in lines)


class TestCostBenefitSection:
    def test_known_model_costs_displayed(self):
        before = {"final_response_quality_v1": 0.5}
        after = {"final_response_quality_v1": 0.8}
        lines = _cost_benefit_section("gemini-3.5-flash", before, after)
        joined = "\n".join(lines)
        assert "$1.5" in joined or "$1.50" in joined
        assert "$9.0" in joined

    def test_unknown_model_uses_zero_costs(self):
        before = {"final_response_quality_v1": 0.5}
        after = {"final_response_quality_v1": 0.8}
        lines = _cost_benefit_section("unknown-model", before, after)
        joined = "\n".join(lines)
        assert "$0" in joined

    def test_improvement_positive_message(self, sample_scores_before, sample_scores_after):
        lines = _cost_benefit_section("gemini-3.5-flash", sample_scores_before, sample_scores_after)
        joined = "\n".join(lines)
        assert "improved average quality" in joined.lower() or "improved" in joined.lower()

    def test_improvement_negative_message(self, sample_scores_after, sample_scores_before):
        lines = _cost_benefit_section("gemini-3.5-flash", sample_scores_after, sample_scores_before)
        joined = "\n".join(lines)
        assert "re-running" in joined.lower() or "Consider" in joined

    def test_quality_per_dollar_calculation(self):
        before = {"m1": 0.5}
        after = {"m1": 0.8}
        lines = _cost_benefit_section("gemini-3.5-flash", before, after)
        joined = "\n".join(lines)
        assert "Quality per" in joined

    def test_empty_scores_no_crash(self):
        lines = _cost_benefit_section("gemini-3.5-flash", {}, {})
        assert len(lines) > 0


class TestGenerateAgentReport:
    def test_creates_markdown_file(self, tmp_path, sample_scores_before):
        path = generate_agent_report(
            "lite",
            "gemini-3.1-flash-lite",
            "123",
            "Generic prompt",
            None,
            sample_scores_before,
            None,
            output_dir=str(tmp_path),
        )
        assert Path(path).exists()
        assert path.endswith("_analysis.md")

    def test_report_contains_agent_name(self, tmp_path, sample_scores_before):
        path = generate_agent_report(
            "lite",
            "gemini-3.1-flash-lite",
            "123",
            "Generic prompt",
            None,
            sample_scores_before,
            None,
            output_dir=str(tmp_path),
        )
        content = Path(path).read_text()
        assert "Lite" in content

    def test_report_without_optimized_prompt(self, tmp_path, sample_scores_before):
        path = generate_agent_report(
            "lite",
            "gemini-3.1-flash-lite",
            "123",
            "Generic prompt",
            None,
            sample_scores_before,
            None,
            output_dir=str(tmp_path),
        )
        content = Path(path).read_text()
        assert "Optimized Prompt" not in content
        assert "Before Optimization" in content

    def test_report_with_optimized_prompt_includes_evolution(
        self, tmp_path, sample_scores_before, sample_scores_after
    ):
        path = generate_agent_report(
            "lite",
            "gemini-3.1-flash-lite",
            "123",
            "Generic prompt",
            "Optimized prompt with tool guidance and policy limits.",
            sample_scores_before,
            sample_scores_after,
            output_dir=str(tmp_path),
        )
        content = Path(path).read_text()
        assert "Optimized Prompt" in content
        assert "Prompt Evolution Summary" in content
        assert "After Optimization" in content

    def test_delta_table_correct_values(self, tmp_path):
        before = {"final_response_quality_v1": 0.50, "safety_v1": 0.80}
        after = {"final_response_quality_v1": 0.70, "safety_v1": 0.90}
        path = generate_agent_report(
            "test",
            "gemini-3.5-flash",
            "123",
            "Generic",
            "Optimized",
            before,
            after,
            output_dir=str(tmp_path),
        )
        content = Path(path).read_text()
        assert "+0.20" in content
        assert "+0.10" in content

    def test_key_observations_lists_improved_and_regressed(self, tmp_path):
        before = {"final_response_quality_v1": 0.90, "safety_v1": 0.50}
        after = {"final_response_quality_v1": 0.80, "safety_v1": 0.90}
        path = generate_agent_report(
            "test",
            "gemini-3.5-flash",
            "123",
            "Generic",
            "Optimized",
            before,
            after,
            output_dir=str(tmp_path),
        )
        content = Path(path).read_text()
        assert "Improved:" in content
        assert "Regressed:" in content
        assert "Safety" in content

    def test_report_with_per_case_data(self, tmp_path, sample_scores_before, sample_scores_after):
        case_metadata = [
            {"tier": "low", "category": "search"},
            {"tier": "medium", "category": "policy"},
        ]
        per_case = [
            {"final_response_quality_v1": 0.9, "safety_v1": 1.0},
            {"final_response_quality_v1": 0.7, "safety_v1": 0.8},
        ]
        path = generate_agent_report(
            "lite",
            "gemini-3.1-flash-lite",
            "123",
            "Generic prompt",
            "Optimized prompt with tool guidance.",
            sample_scores_before,
            sample_scores_after,
            output_dir=str(tmp_path),
            before_per_case=per_case,
            after_per_case=per_case,
            case_metadata=case_metadata,
        )
        content = Path(path).read_text()
        assert "Per-Tier Breakdown" in content
        assert "Per-Category Capability" in content
        assert "Total cases:" in content


class TestGenerateComparisonReport:
    def test_creates_comparison_report_file(self, tmp_path, sample_all_results):
        path = generate_comparison_report(sample_all_results, output_dir=str(tmp_path))
        assert Path(path).exists()
        assert "comparison_report.md" in path

    def test_report_includes_all_agents(self, tmp_path, sample_all_results):
        path = generate_comparison_report(sample_all_results, output_dir=str(tmp_path))
        content = Path(path).read_text()
        for name in ["Lite", "Flash", "Pro", "Sonnet", "Opus"]:
            assert name in content

    def test_cost_benefit_rankings(self, tmp_path, sample_all_results):
        path = generate_comparison_report(sample_all_results, output_dir=str(tmp_path))
        content = Path(path).read_text()
        assert "Ranked by Quality/$" in content
        assert "Lite" in content

    def test_report_without_after_scores(self, tmp_path):
        results = {
            "lite": {
                "model": "gemini-3.1-flash-lite",
                "original_prompt": "Generic",
                "optimized_prompt": None,
                "before": {"final_response_quality_v1": 0.7},
                "after": None,
            }
        }
        path = generate_comparison_report(results, output_dir=str(tmp_path))
        content = Path(path).read_text()
        assert "Baseline" in content
        assert "Improvement Delta" not in content


# ── Chart function tests ──────────────────────────────────────────


class TestNormalizeAgentKeys:
    def test_short_keys_unchanged(self):
        results = {"lite": {"model": "gemini-3.1-flash-lite", "before": {}}}
        normalized = normalize_agent_keys(results)
        assert "lite" in normalized

    def test_normalizes_by_model_field(self):
        results = {"long-key-flash": {"model": "gemini-3.5-flash", "before": {}}}
        normalized = normalize_agent_keys(results)
        assert "flash" in normalized

    def test_preserves_underscore_prefixed_keys(self):
        results = {"_eval_metadata": {"version": "v1"}, "flash": {"model": "gemini-3.5-flash"}}
        normalized = normalize_agent_keys(results)
        assert "_eval_metadata" in normalized

    def test_empty_results(self):
        assert normalize_agent_keys({}) == {}


def _mock_plt_setup(mock_plt):
    """Configure mock plt so subplots() returns (fig, ax) tuple."""
    mock_fig = MagicMock()
    mock_ax = MagicMock()
    mock_plt.subplots.return_value = (mock_fig, mock_ax)
    return mock_fig, mock_ax


class TestGenerateTierBreakdownChart:
    @patch("wrangler.reporting.analysis.plt")
    def test_creates_chart_file(self, mock_plt, tmp_path):
        _mock_plt_setup(mock_plt)
        results = {
            "flash": {
                "model": "gemini-3.5-flash",
                "after": {"quality": 0.8},
                "after_per_case": [{"quality": 0.9}, {"quality": 0.7}],
            },
        }
        metadata = [{"tier": "low"}, {"tier": "high"}]
        generate_tier_breakdown_chart(results, metadata, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporting.analysis.plt")
    def test_skips_when_no_metadata(self, mock_plt, tmp_path):
        results = {"flash": {"model": "gemini-3.5-flash", "after": {"q": 0.8}}}
        generate_tier_breakdown_chart(results, None, charts_dir=tmp_path)
        mock_plt.savefig.assert_not_called()


class TestGenerateCategoryHeatmap:
    @patch("wrangler.reporting.analysis.plt")
    def test_creates_heatmap(self, mock_plt, tmp_path):
        _mock_plt_setup(mock_plt)
        results = {
            "flash": {
                "model": "gemini-3.5-flash",
                "after": {"quality": 0.8},
                "after_per_case": [{"quality": 0.9}, {"quality": 0.7}],
            },
        }
        metadata = [{"category": "search"}, {"category": "policy"}]
        generate_category_heatmap(results, metadata, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporting.analysis.plt")
    def test_skips_when_no_metadata(self, mock_plt, tmp_path):
        results = {"flash": {"model": "gemini-3.5-flash", "after": {"q": 0.8}}}
        generate_category_heatmap(results, None, charts_dir=tmp_path)
        mock_plt.savefig.assert_not_called()


class TestGenerateRadarChart:
    @patch("wrangler.reporting.analysis.plt")
    def test_creates_radar_chart(self, mock_plt, tmp_path):
        _mock_plt_setup(mock_plt)
        mock_plt.subplot.return_value = MagicMock()
        results = {
            "flash": {
                "model": "gemini-3.5-flash",
                "after": {"final_response_quality_v1": 0.8, "safety_v1": 0.9},
            },
            "sonnet": {
                "model": "claude-sonnet-4-6",
                "after": {"final_response_quality_v1": 0.85, "safety_v1": 0.95},
            },
        }
        generate_radar_chart(results, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()


class TestGenerateTierImprovementHeatmap:
    @patch("wrangler.reporting.analysis.plt")
    def test_creates_heatmap(self, mock_plt, tmp_path):
        _mock_plt_setup(mock_plt)
        results = {
            "flash": {
                "model": "gemini-3.5-flash",
                "before": {"quality": 0.7},
                "after": {"quality": 0.8},
                "before_per_case": [{"quality": 0.6}, {"quality": 0.8}],
                "after_per_case": [{"quality": 0.7}, {"quality": 0.9}],
            },
        }
        metadata = [{"tier": "low"}, {"tier": "high"}]
        generate_tier_improvement_heatmap(results, metadata, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporting.analysis.plt")
    def test_skips_when_no_after(self, mock_plt, tmp_path):
        results = {"flash": {"model": "gemini-3.5-flash", "before": {"q": 0.7}}}
        metadata = [{"tier": "low"}]
        generate_tier_improvement_heatmap(results, metadata, charts_dir=tmp_path)
        mock_plt.savefig.assert_not_called()
