"""Tests for wrangler.reporting.analyzer — experiment analysis and report generation."""

import pytest

from wrangler.reporting.analyzer import (
    ExperimentAnalysis,
    GepaRunStats,
    PairAnalysis,
    _analyze_tool_keywords,
    _find_removed_content,
    _format_tool_audit,
    _prompt_diff_summary,
    format_analysis_report,
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
    """`overall_improved` now means "beyond the noise floor", not "went up".

    It used to be `sum(after - before) > 0`, which calls any positive drift a
    win. A control arm on 2026-08-22 drifted +0.039 without its prompt
    changing, so that test would have reported pure noise as improvement.
    These cases therefore need a control arm to return True at all.
    """

    def test_overall_improved_true_when_the_gain_clears_the_floor(self):
        control = _make_pair(
            before={"q": 0.70}, after={"q": 0.72}, original="same", optimized="same"
        )
        p1 = _make_pair(before={"q": 0.5}, after={"q": 0.9})
        ea = ExperimentAnalysis(experiment_name="test", pairs=[control, p1])
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
        assert "+" in result
        assert "-" in result


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


class TestControlArmCalibration:
    """A delta is only an improvement if it beats the control arm's noise.

    CLAUDE.md requires every sweep to carry an arm whose prompt does not
    change. On 2026-08-22 such an arm moved response quality +0.039 and safety
    +0.035 while being byte-identical before and after. Reporting a +0.034 gain
    from another arm as an improvement — which the old hardcoded 0.005
    threshold would have done — states as fact something indistinguishable
    from nothing.
    """

    @staticmethod
    def _pair(name, before, after, orig="seed", opt=None):
        from wrangler.reporting.analyzer import PairAnalysis

        return PairAnalysis(
            pair_id=name,
            model="m",
            before=before,
            after=after,
            original_prompt=orig,
            optimized_prompt=orig if opt is None else opt,
        )

    def test_an_unchanged_prompt_is_recognised_as_a_control(self):
        ctrl = self._pair("flash", {"a": 0.9}, {"a": 0.94})
        real = self._pair("sonnet", {"a": 0.8}, {"a": 0.9}, opt="a much longer prompt")
        assert ctrl.is_control is True
        assert real.is_control is False

    def test_a_pair_with_no_prompts_recorded_is_not_a_control(self):
        """Absent prompts must not masquerade as 'unchanged'."""
        blank = self._pair("x", {"a": 0.9}, {"a": 0.9}, orig="", opt="")
        assert blank.is_control is False

    def test_noise_floor_is_the_largest_control_movement(self):
        from wrangler.reporting.analyzer import measure_noise_floor

        ctrl = self._pair("flash", {"a": 0.9, "b": 0.5}, {"a": 0.939, "b": 0.48})
        real = self._pair("sonnet", {"a": 0.8}, {"a": 0.99}, opt="longer")
        assert measure_noise_floor([ctrl, real]) == pytest.approx(0.039)

    def test_no_control_arm_returns_none_not_zero(self):
        """None means 'uncalibrated'. Zero would mean 'no noise', which is a lie."""
        from wrangler.reporting.analyzer import measure_noise_floor

        real = self._pair("sonnet", {"a": 0.8}, {"a": 0.9}, opt="longer")
        assert measure_noise_floor([real]) is None

    def test_deltas_inside_the_floor_are_not_improvements(self):
        from wrangler.reporting.analyzer import classify_deltas

        real = self._pair("pro", {"a": 0.868, "b": 0.885}, {"a": 0.902, "b": 0.967}, opt="longer")
        got = classify_deltas(real, floor=0.039)
        assert got["a"] == "within-noise", "+0.034 is below the 0.039 floor"
        assert got["b"] == "improved", "+0.082 clears it"

    def test_regressions_are_held_to_the_same_floor(self):
        from wrangler.reporting.analyzer import classify_deltas

        p = self._pair("pro", {"a": 0.855, "b": 0.981}, {"a": 0.760, "b": 0.976}, opt="longer")
        got = classify_deltas(p, floor=0.039)
        assert got["a"] == "regressed", "-0.095 clears the floor"
        assert got["b"] == "within-noise", "-0.005 does not"

    def test_without_a_floor_everything_is_uncalibrated(self):
        from wrangler.reporting.analyzer import classify_deltas

        p = self._pair("sonnet", {"a": 0.8}, {"a": 0.99}, opt="longer")
        assert set(classify_deltas(p, floor=None).values()) == {"uncalibrated"}


class TestExperimentCalibration:
    """The sweep-level verdict must respect the noise floor too."""

    @staticmethod
    def _exp(pairs):
        from wrangler.reporting.analyzer import ExperimentAnalysis

        return ExperimentAnalysis(experiment_name="sweep", pairs=pairs)

    def test_overall_improved_is_false_without_a_control(self):
        """No control arm means no basis for the claim — not an optimistic guess."""
        p = TestControlArmCalibration._pair("sonnet", {"a": 0.8}, {"a": 0.99}, opt="longer")
        e = self._exp([p])
        assert e.noise_floor is None
        assert e.overall_improved is False
        assert "UNCALIBRATED" in e.calibration_note

    def test_drift_smaller_than_the_floor_is_not_an_improvement(self):
        ctrl = TestControlArmCalibration._pair("flash", {"a": 0.9}, {"a": 0.94})
        real = TestControlArmCalibration._pair("pro", {"a": 0.868}, {"a": 0.902}, opt="longer")
        e = self._exp([ctrl, real])
        assert e.noise_floor == pytest.approx(0.04)
        assert e.overall_improved is False, "+0.034 must not beat a 0.040 floor"

    def test_a_gain_beyond_the_floor_counts(self):
        ctrl = TestControlArmCalibration._pair("flash", {"a": 0.9}, {"a": 0.94})
        real = TestControlArmCalibration._pair("sonnet", {"a": 0.8}, {"a": 0.99}, opt="longer")
        e = self._exp([ctrl, real])
        assert e.overall_improved is True
        assert "Noise floor" in e.calibration_note
        assert "flash" in e.calibration_note

    def test_the_control_arm_does_not_inflate_the_verdict(self):
        """Its own drift must be excluded from the gain it is calibrating."""
        ctrl = TestControlArmCalibration._pair("flash", {"a": 0.5}, {"a": 0.54})
        flat = TestControlArmCalibration._pair("pro", {"a": 0.8}, {"a": 0.8}, opt="longer")
        e = self._exp([ctrl, flat])
        assert e.overall_improved is False


class TestPairedDeltas:
    """Deltas must come from cases both sides scored, not from two subsets.

    sonnet's 2026-08-22 arm scored 30 cases before and 57 after. Averaging each
    side separately and subtracting compares different samples and calls the
    difference a prompt effect.
    """

    @staticmethod
    def _pair_with(before_pc, after_pc):
        from wrangler.reporting.analyzer import PairAnalysis

        return PairAnalysis(
            pair_id="p",
            model="m",
            before={"m": 0.5},
            after={"m": 0.9},
            before_per_case=before_pc,
            after_per_case=after_pc,
            original_prompt="seed",
            optimized_prompt="longer",
        )

    def test_paired_delta_ignores_unmatched_cases(self):
        from wrangler.eval.evaluator import CASE_INDEX_KEY

        # Case 0 exists only before; case 9 only after. Only case 1 is comparable,
        # and on it the metric got *worse* — the unpaired average hides that.
        p = self._pair_with(
            [{CASE_INDEX_KEY: 0, "m": 0.1}, {CASE_INDEX_KEY: 1, "m": 0.9}],
            [{CASE_INDEX_KEY: 1, "m": 0.6}, {CASE_INDEX_KEY: 9, "m": 1.0}],
        )
        assert p.paired["n_paired"] == 1
        assert p.paired["deltas"]["m"] == pytest.approx(-0.3)
        assert p.paired["dropped_before"] == 1
        assert p.paired["dropped_after"] == 1

    def test_unpaired_average_would_have_disagreed(self):
        """Guards the premise: the naive delta points the other way."""
        from wrangler.eval.evaluator import CASE_INDEX_KEY

        p = self._pair_with(
            [{CASE_INDEX_KEY: 0, "m": 0.1}, {CASE_INDEX_KEY: 1, "m": 0.9}],
            [{CASE_INDEX_KEY: 1, "m": 0.6}, {CASE_INDEX_KEY: 9, "m": 1.0}],
        )
        naive = (0.6 + 1.0) / 2 - (0.1 + 0.9) / 2
        assert naive > 0
        assert p.paired["deltas"]["m"] < 0

    def test_no_indices_yields_no_pairing(self):
        p = self._pair_with([{"m": 0.1}], [{"m": 0.9}])
        assert p.paired["n_paired"] == 0
        assert p.paired["deltas"] == {}
