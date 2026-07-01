"""Tests for wrangler.reporter — report generation, and wrangler.analysis — chart generation."""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call


class TestGenerateComparisonChart:
    @patch("wrangler.reporting.analysis.plt")
    def test_chart_saved_to_correct_path(self, mock_plt, tmp_path):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.analysis import generate_comparison_chart
        results = {"lite": {"before": {"final_response_quality_v1": 0.9}}}
        generate_comparison_chart(results, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporting.analysis.plt")
    def test_handles_single_pair(self, mock_plt, tmp_path):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.analysis import generate_comparison_chart
        results = {"lite": {"before": {"final_response_quality_v1": 0.5}}}
        generate_comparison_chart(results, charts_dir=tmp_path)

    @patch("wrangler.reporting.analysis.plt")
    def test_handles_missing_metrics(self, mock_plt, tmp_path):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.analysis import generate_comparison_chart
        results = {"lite": {"before": {}}}
        generate_comparison_chart(results, charts_dir=tmp_path)


class TestGenerateImprovementChart:
    @patch("wrangler.reporting.analysis.plt")
    def test_chart_saved_to_correct_path(self, mock_plt, tmp_path):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.analysis import generate_improvement_chart
        results = {
            "lite": {
                "before": {"final_response_quality_v1": 0.5},
                "after": {"final_response_quality_v1": 0.8},
            }
        }
        generate_improvement_chart(results, charts_dir=tmp_path)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporting.analysis.plt")
    def test_handles_empty_results(self, mock_plt, tmp_path):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.analysis import generate_improvement_chart
        results = {"lite": {"before": {}, "after": {}}}
        generate_improvement_chart(results, charts_dir=tmp_path)


class TestGenerateReport:
    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_report_file_created(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {
            "lite": {
                "model": "gemini-3.1-flash-lite",
                "before": {"final_response_quality_v1": 0.7},
                "after": {"final_response_quality_v1": 0.9},
            }
        }
        generate_report(results, "test_experiment", use_paperbanana=False)
        report = tmp_path / "experiment_report.md"
        assert report.exists()

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_report_contains_pair_ids(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {
            "pair_alpha": {"model": "m", "before": {"final_response_quality_v1": 0.5}, "after": {"final_response_quality_v1": 0.8}},
            "pair_beta": {"model": "m", "before": {"final_response_quality_v1": 0.6}, "after": {"final_response_quality_v1": 0.9}},
        }
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "pair_alpha" in content or "Pair_Alpha" in content
        assert "pair_beta" in content or "Pair_Beta" in content

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_report_score_table(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {"test": {"model": "m", "before": {"final_response_quality_v1": 0.50}, "after": {"final_response_quality_v1": 0.70}}}
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "0.50" in content
        assert "0.70" in content
        assert "+0.20" in content

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_threshold_section_rendered(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {"test": {
            "model": "m",
            "before": {"tool_use_quality_v1": 0.40, "safety_v1": 0.97},
            "after": {"tool_use_quality_v1": 0.45, "safety_v1": 0.97},
            "thresholds": {"tool_use_quality_v1": 0.5, "safety_v1": 0.95},
        }}
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "GEPA Threshold Alignment" in content
        assert "BELOW" in content  # tool_use after 0.45 < 0.50
        assert "PASS" in content   # safety 0.97 >= 0.95

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_threshold_section_absent_without_thresholds(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {"test": {"model": "m", "before": {"safety_v1": 0.9}, "after": {"safety_v1": 0.9}}}
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "GEPA Threshold Alignment" not in content

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_includes_optimized_prompts(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {"test": {"model": "m", "optimized_prompt": "Be very helpful.", "before": {}, "after": {}}}
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "Be very helpful." in content

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_zero_before_score_handled(self, mock_reports, mock_charts, mock_plt, tmp_path):
        """When before score is 0, the report should still render without errors."""
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report
        results = {"test": {"model": "m", "before": {"final_response_quality_v1": 0}, "after": {"final_response_quality_v1": 0.5}}}
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "0.50" in content
        assert "+0.50" in content


class TestPaperBananaFallback:
    RESULTS = {
        "lite": {
            "model": "gemini-3.1-flash-lite",
            "before": {"final_response_quality_v1": 0.7},
        }
    }

    @patch("wrangler.reporting.charts.subprocess.run", side_effect=FileNotFoundError("uv not found"))
    @patch("wrangler.reporting.charts.generate_comparison_chart")
    def test_falls_back_on_subprocess_error(self, mock_mpl, mock_run, tmp_path):
        from wrangler.reporting.charts import generate_comparison_chart_pb
        generate_comparison_chart_pb(self.RESULTS, tmp_path)
        mock_mpl.assert_called_once()

    @patch("wrangler.reporting.charts.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pb", timeout=180))
    @patch("wrangler.reporting.charts.generate_comparison_chart")
    def test_falls_back_on_timeout(self, mock_mpl, mock_run, tmp_path):
        from wrangler.reporting.charts import generate_comparison_chart_pb
        generate_comparison_chart_pb(self.RESULTS, tmp_path)
        mock_mpl.assert_called_once()

    @patch("wrangler.reporting.charts.subprocess.run")
    @patch("wrangler.reporting.charts.generate_comparison_chart")
    def test_no_paperbanana_flag_skips(self, mock_mpl, mock_run, tmp_path):
        from wrangler.reporting.charts import generate_comparison_chart_pb
        generate_comparison_chart_pb(self.RESULTS, tmp_path, use_paperbanana=False)
        mock_run.assert_not_called()
        mock_mpl.assert_called_once()


class TestComposite:
    """The headline composite excludes the instruction_following artifact metric."""

    def test_excludes_instruction_following_v1(self):
        from wrangler.reporting.reporter import _composite
        metrics = {
            "final_response_quality_v1": 0.9,
            "hallucination_v1": 0.9,
            "safety_v1": 0.9,
            "tool_use_quality_v1": 0.9,
            "instruction_following_v1": 0.1,
        }
        # mean of the four non-IF metrics = 0.9, NOT (0.9*4+0.1)/5 = 0.74
        assert _composite(metrics) == pytest.approx(0.9)

    def test_excludes_unsuffixed_instruction_following(self):
        from wrangler.reporting.reporter import _composite
        metrics = {"final_response_quality_v1": 0.8, "instruction_following": 0.0}
        assert _composite(metrics) == pytest.approx(0.8)

    def test_averages_remaining(self):
        from wrangler.reporting.reporter import _composite
        metrics = {"final_response_quality_v1": 0.6, "safety_v1": 0.8}
        assert _composite(metrics) == pytest.approx(0.7)

    def test_empty_returns_zero(self):
        from wrangler.reporting.reporter import _composite
        assert _composite({}) == 0
        assert _composite({"instruction_following_v1": 0.5}) == 0
