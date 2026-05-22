"""Tests for wrangler.reporter — chart generation and markdown report output."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestGenerateComparisonChart:
    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_chart_saved_to_correct_path(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_charts.__truediv__ = lambda s, x: tmp_path / x
        mock_charts.mkdir = MagicMock()
        mock_reports.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_comparison_chart
        results = {"lite": {"after": {"final_response_quality_v1": 0.9}}}
        generate_comparison_chart(results)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_handles_single_pair(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_charts.__truediv__ = lambda s, x: tmp_path / x
        mock_charts.mkdir = MagicMock()
        mock_reports.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_comparison_chart
        results = {"single": {"after": {"final_response_quality_v1": 0.5}}}
        generate_comparison_chart(results)

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_handles_missing_metrics(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_charts.__truediv__ = lambda s, x: tmp_path / x
        mock_charts.mkdir = MagicMock()
        mock_reports.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_comparison_chart
        results = {"test": {"after": {}}}
        generate_comparison_chart(results)


class TestGenerateImprovementChart:
    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_chart_saved_to_correct_path(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_charts.__truediv__ = lambda s, x: tmp_path / x
        mock_charts.mkdir = MagicMock()
        mock_reports.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_improvement_chart
        results = {
            "lite": {
                "before": {"final_response_quality_v1": 0.5},
                "after": {"final_response_quality_v1": 0.8},
            }
        }
        generate_improvement_chart(results)
        mock_plt.savefig.assert_called_once()

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_handles_empty_results(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_charts.__truediv__ = lambda s, x: tmp_path / x
        mock_charts.mkdir = MagicMock()
        mock_reports.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_improvement_chart
        results = {"test": {"before": {}, "after": {}}}
        generate_improvement_chart(results)


class TestGenerateReport:
    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_report_file_created(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_report
        results = {
            "lite": {
                "model": "gemini-3.1-flash-lite",
                "before": {"final_response_quality_v1": 0.7},
                "after": {"final_response_quality_v1": 0.9},
            }
        }
        generate_report(results, "test_experiment")
        report = tmp_path / "experiment_report.md"
        assert report.exists()

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_report_contains_pair_ids(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_report
        results = {
            "pair_alpha": {"model": "m", "before": {"final_response_quality_v1": 0.5}, "after": {"final_response_quality_v1": 0.8}},
            "pair_beta": {"model": "m", "before": {"final_response_quality_v1": 0.6}, "after": {"final_response_quality_v1": 0.9}},
        }
        generate_report(results, "test")
        content = (tmp_path / "experiment_report.md").read_text()
        assert "pair_alpha" in content
        assert "pair_beta" in content

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_report_score_table(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_report
        results = {"test": {"model": "m", "before": {"final_response_quality_v1": 0.50}, "after": {"final_response_quality_v1": 0.70}}}
        generate_report(results, "test")
        content = (tmp_path / "experiment_report.md").read_text()
        assert "0.50" in content
        assert "0.70" in content
        assert "+0.20" in content

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_includes_optimized_prompts(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_report
        results = {"test": {"model": "m", "optimized_prompt": "Be very helpful.", "before": {}, "after": {}}}
        generate_report(results, "test")
        content = (tmp_path / "experiment_report.md").read_text()
        assert "Be very helpful." in content

    @patch("wrangler.reporter.plt")
    @patch("wrangler.reporter.CHARTS_DIR")
    @patch("wrangler.reporter.REPORTS_DIR")
    def test_zero_before_score_shows_na(self, mock_reports, mock_charts, mock_plt, tmp_path):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporter import generate_report
        results = {"test": {"model": "m", "before": {"final_response_quality_v1": 0}, "after": {"final_response_quality_v1": 0.5}}}
        generate_report(results, "test")
        content = (tmp_path / "experiment_report.md").read_text()
        assert "N/A" in content
