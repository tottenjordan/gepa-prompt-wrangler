"""Tests for wrangler.reporter — report generation, and wrangler.analysis — chart generation."""

import subprocess
from typing import ClassVar
from unittest.mock import MagicMock, patch


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
            "pair_alpha": {
                "model": "m",
                "before": {"final_response_quality_v1": 0.5},
                "after": {"final_response_quality_v1": 0.8},
            },
            "pair_beta": {
                "model": "m",
                "before": {"final_response_quality_v1": 0.6},
                "after": {"final_response_quality_v1": 0.9},
            },
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

        results = {
            "test": {
                "model": "m",
                "before": {"final_response_quality_v1": 0.50},
                "after": {"final_response_quality_v1": 0.70},
            }
        }
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

        results = {
            "test": {
                "model": "m",
                "before": {"tool_use_quality_v1": 0.40, "safety_v1": 0.97},
                "after": {"tool_use_quality_v1": 0.45, "safety_v1": 0.97},
                "thresholds": {"tool_use_quality_v1": 0.5, "safety_v1": 0.95},
            }
        }
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "GEPA Threshold Alignment" in content
        assert "BELOW" in content  # tool_use after 0.45 < 0.50
        assert "PASS" in content  # safety 0.97 >= 0.95

    @patch("wrangler.reporting.analysis.plt")
    @patch("wrangler.reporting.reporter.CHARTS_DIR")
    @patch("wrangler.reporting.reporter.REPORTS_DIR")
    def test_threshold_section_absent_without_thresholds(
        self, mock_reports, mock_charts, mock_plt, tmp_path
    ):
        mock_reports.__truediv__ = lambda s, x: tmp_path / x
        mock_reports.mkdir = MagicMock()
        mock_charts.__truediv__ = lambda s, x: tmp_path / "charts" / x
        mock_charts.mkdir = MagicMock()
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        mock_plt.cm.Set2 = MagicMock(return_value=[(0, 0, 0, 1)] * 6)

        from wrangler.reporting.reporter import generate_report

        results = {
            "test": {"model": "m", "before": {"safety_v1": 0.9}, "after": {"safety_v1": 0.9}}
        }
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

        results = {
            "test": {
                "model": "m",
                "optimized_prompt": "Be very helpful.",
                "before": {},
                "after": {},
            }
        }
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

        results = {
            "test": {
                "model": "m",
                "before": {"final_response_quality_v1": 0},
                "after": {"final_response_quality_v1": 0.5},
            }
        }
        generate_report(results, "test", use_paperbanana=False)
        content = (tmp_path / "experiment_report.md").read_text()
        assert "0.50" in content
        assert "+0.50" in content


class TestPaperBananaFallback:
    RESULTS: ClassVar[dict] = {
        "lite": {
            "model": "gemini-3.1-flash-lite",
            "before": {"final_response_quality_v1": 0.7},
        }
    }

    @patch(
        "wrangler.reporting.charts.subprocess.run", side_effect=FileNotFoundError("uv not found")
    )
    @patch("wrangler.reporting.charts.generate_comparison_chart")
    def test_falls_back_on_subprocess_error(self, mock_mpl, mock_run, tmp_path):
        from wrangler.reporting.charts import generate_comparison_chart_pb

        generate_comparison_chart_pb(self.RESULTS, tmp_path)
        mock_mpl.assert_called_once()

    @patch(
        "wrangler.reporting.charts.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pb", timeout=180),
    )
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


class TestAControlArmIsNotReportedAsAnImprovement:
    """A control arm's report claimed GEPA improved a prompt it never touched.

    Campaign 06's validation arm ran with `skip_optimize: true`: no optimize
    stage, prompt byte-identical on both sides, so every delta is noise by
    construction. The generated report opened with

        **1/1 models improved** after GEPA optimization.
        Best performer: **sonnet** (+0.030 avg).
        **Strongest metric gain:** Instruction Following (+0.067 avg)

    +0.030 sits well inside that same arm's measured floor of 0.067. This is
    precisely the failure CLAUDE.md's control-arm rule exists to prevent -- on
    2026-08-22 a byte-identical arm drifted +0.039 and three-arm agreement
    nearly promoted it as a clean win.

    The cause is a hardcoded 0.005 threshold, thirteen times smaller than the
    floor actually measured on this pipeline.
    """

    @staticmethod
    def _arm(before, after, optimized_prompt="", original_prompt="p"):
        return {
            "model": "claude-sonnet-4-6",
            "before": before,
            "after": after,
            "original_prompt": original_prompt,
            "optimized_prompt": optimized_prompt,
        }

    def test_an_unchanged_prompt_is_recognised_as_a_control(self):
        from wrangler.reporting.reporter import _is_control_arm

        assert _is_control_arm(self._arm({"m": 0.8}, {"m": 0.83}))
        assert _is_control_arm(self._arm({"m": 0.8}, {"m": 0.83}, optimized_prompt="p"))
        assert not _is_control_arm(
            self._arm({"m": 0.8}, {"m": 0.83}, optimized_prompt="a different prompt")
        )

    def test_a_control_only_run_does_not_claim_optimization_happened(self):
        from wrangler.reporting.reporter import _executive_summary

        results = {"sonnet": self._arm({"m": 0.8408}, {"m": 0.8711})}
        text = "\n".join(_executive_summary(results, ["sonnet"]))
        assert "after GEPA optimization" not in text
        assert "models improved" not in text
        assert "Best performer" not in text

    def test_a_control_only_run_reports_its_delta_as_the_floor(self):
        """The number is still worth printing -- as noise, not as a gain."""
        from wrangler.reporting.reporter import _executive_summary

        results = {"sonnet": self._arm({"m": 0.8408}, {"m": 0.8711})}
        text = "\n".join(_executive_summary(results, ["sonnet"])).lower()
        assert "control" in text
        assert "noise" in text or "floor" in text

    def test_a_real_arm_is_judged_against_the_control_not_against_0_005(self):
        """A sub-floor movement must not be called an improvement."""
        from wrangler.reporting.reporter import _executive_summary

        results = {
            "sonnet": self._arm({"m": 0.80}, {"m": 0.867}),  # control: +0.067 = the floor
            "pro": self._arm({"m": 0.80}, {"m": 0.83}, optimized_prompt="new"),  # +0.030
        }
        text = "\n".join(_executive_summary(results, ["sonnet", "pro"]))
        assert "1/1 models improved" not in text
        assert "0/1 models improved" in text or "within the noise floor" in text.lower()

    def test_a_real_arm_beating_the_floor_still_reports_as_improved(self):
        from wrangler.reporting.reporter import _executive_summary

        results = {
            "sonnet": self._arm({"m": 0.80}, {"m": 0.81}),  # control: floor 0.010
            "pro": self._arm({"m": 0.80}, {"m": 0.95}, optimized_prompt="new"),  # +0.150
        }
        text = "\n".join(_executive_summary(results, ["sonnet", "pro"]))
        assert "improved" in text.lower()
        assert "pro" in text

    def test_with_no_control_arm_the_wording_is_uncalibrated(self):
        """No control means no basis for the claim -- say so rather than guess."""
        from wrangler.reporting.reporter import _executive_summary

        results = {"pro": self._arm({"m": 0.80}, {"m": 0.83}, optimized_prompt="new")}
        text = "\n".join(_executive_summary(results, ["pro"])).lower()
        assert "no control arm" in text or "uncalibrated" in text

    def test_metric_callouts_exclude_control_arms(self):
        """A control's per-metric noise must not become a "strongest gain".

        The metric loop averaged over every arm, controls included, so a
        control drifting +0.067 on instruction_following was announced as the
        sweep's strongest metric gain.
        """
        from wrangler.reporting.reporter import _executive_summary

        results = {
            # control drifts hard on one metric; real arm is flat
            "sonnet": self._arm(
                {"instruction_following_v1": 0.70}, {"instruction_following_v1": 0.90}
            ),
            "pro": self._arm(
                {"instruction_following_v1": 0.80},
                {"instruction_following_v1": 0.80},
                optimized_prompt="new",
            ),
        }
        text = "\n".join(_executive_summary(results, ["sonnet", "pro"]))
        assert "Strongest metric gain" not in text, (
            "the +0.20 belongs to the control arm and is noise, not a gain"
        )

    def test_a_metric_gain_is_not_inflated_by_a_control_arms_drift(self):
        """Averaging controls in shifts the reported metric toward their noise.

        Constructed so the arm-level floor is 0.0 and cannot mask the effect:
        the control's two metrics drift -0.15 and +0.15, so its average delta
        is zero, while one metric drifts +0.15. The real arm is flat.

        Averaged over both arms, safety_v1 reads +0.075 and clears the 0.0
        floor, producing a "Strongest metric gain" that belongs entirely to a
        prompt nobody changed. Over the real arm alone it is 0.0.
        """
        from wrangler.reporting.reporter import _executive_summary

        results = {
            "sonnet": self._arm(
                {"instruction_following_v1": 0.5, "safety_v1": 0.5},
                {"instruction_following_v1": 0.35, "safety_v1": 0.65},
            ),
            "pro": self._arm(
                {"instruction_following_v1": 0.5, "safety_v1": 0.5},
                {"instruction_following_v1": 0.5, "safety_v1": 0.5},
                optimized_prompt="new",
            ),
        }
        text = "\n".join(_executive_summary(results, ["sonnet", "pro"]))
        assert "Strongest metric gain" not in text, (
            "safety_v1 averages +0.075 only because the control arm's +0.15 "
            "drift was folded in; the optimized arm moved 0.000"
        )
