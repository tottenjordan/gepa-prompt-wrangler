"""Where a run's money and wall clock actually went.

Both `results` builders load the three stage artifacts and then sum their token usage
before the reporter ever sees them, so the report can say what an arm cost but not which
*stage* cost it. On the real `c07-pro` run those answers differ sharply:

    optimize   $0.328   30,742 s  (87% of wall clock, 36% of dollars)
    eval       $0.575    4,550 s  (13% of wall clock, 64% of dollars)

Dollars and hours point at different stages, and at ~$0.90 a run the binding constraint
is hours and judge RPM rather than budget. None of that is recoverable from the sum.

Two properties these tests exist to protect, both of which the existing cost code already
gets right and which are easy to lose:

- **"unpriced" is not "$0.00".** An unregistered model must not render as free.
- **Estimates are labelled.** Every `token_usage` in this repo carries
  `is_estimate: True`; a dollar figure printed to four places that was never metered
  reads as metering.
"""

from __future__ import annotations

import pytest

from wrangler.reporting.stage_economics import STAGE_ORDER, build_stage_usage


def _stage(inp=0, out=0, elapsed=0.0, in_usd=0.0, out_usd=0.0, estimate=True):
    return {
        "token_usage": {"input_tokens": inp, "output_tokens": out, "is_estimate": estimate},
        "costs": {"input_usd": in_usd, "output_usd": out_usd},
        "elapsed": elapsed,
    }


class TestBuildStageUsage:
    def test_stages_are_kept_separate_not_summed(self):
        """The single most useful thing the cost data can say is 'which stage'."""
        usage = build_stage_usage(
            eval_before=_stage(100, 200, 60.0, 0.01, 0.02),
            optimize=_stage(3900, 35840, 30741.9, 0.00585, 0.32256),
            eval_after=_stage(150, 250, 90.0, 0.015, 0.025),
        )
        assert usage["optimize"]["output_tokens"] == 35840
        assert usage["eval_before"]["output_tokens"] == 200
        assert usage["eval_after"]["output_tokens"] == 250
        # The distinguishing fact: optimize dominates the clock.
        assert usage["optimize"]["elapsed"] > 100 * usage["eval_before"]["elapsed"]

    def test_absent_optimize_stage_yields_an_empty_entry_not_zeros(self):
        """An eval-only run has no optimize stage.

        Zeros would render as "optimize cost nothing", which is a claim. Absent is
        the truth, and the renderer prints it as such.
        """
        usage = build_stage_usage(_stage(1, 2, 3.0), {}, _stage(1, 2, 3.0))
        assert usage["optimize"] == {}

    def test_a_stage_with_no_token_usage_still_records_its_elapsed(self):
        """Wall clock is measured even when tokens are not.

        Time is the constraint that actually binds a campaign, so losing it because
        the token accounting was missing would drop the more important number.
        """
        usage = build_stage_usage({"elapsed": 42.0}, {}, {})
        assert usage["eval_before"]["elapsed"] == 42.0
        assert usage["eval_before"]["input_tokens"] == 0

    def test_is_estimate_propagates(self):
        usage = build_stage_usage(_stage(1, 2, 3.0, estimate=True), {}, {})
        assert usage["eval_before"]["is_estimate"] is True

    def test_stage_order_is_chronological(self):
        """Report order must follow the run, not dict insertion luck."""
        assert STAGE_ORDER == ("eval_before", "optimize", "eval_after")

    def test_totals_match_the_summed_token_usage_it_replaces(self):
        """The split must reconcile with the existing summed figure.

        `token_usage` stays in `results` and `_cost_benefit_section` still reads it;
        if the two disagree the report contradicts itself in adjacent tables.
        """
        usage = build_stage_usage(_stage(100, 200), _stage(3900, 35840), _stage(150, 250))
        assert sum(s.get("input_tokens", 0) for s in usage.values()) == 4150
        assert sum(s.get("output_tokens", 0) for s in usage.values()) == 36290


class TestStageEconomicsSection:
    """The rendered section. Imported lazily so a failure here is legible."""

    @staticmethod
    def _render(results, ordered):
        from wrangler.reporting.reporter import _stage_economics_section

        return "\n".join(_stage_economics_section(results, ordered))

    def test_reports_the_optimize_vs_eval_split(self):
        results = {
            "c07-pro": {
                "model": "gemini-3.1-pro-preview",
                "before": {"safety_v1": 0.88},
                "after": {"safety_v1": 0.97},
                "stage_usage": build_stage_usage(
                    _stage(100, 200, 2650.0, 0.01, 0.26),
                    _stage(3900, 35840, 30742.0, 0.00585, 0.32256),
                    _stage(150, 250, 1900.0, 0.015, 0.29),
                ),
            }
        }
        out = self._render(results, ["c07-pro"])
        assert "optimize" in out
        assert "eval_before" in out
        assert "%" in out, "shares are the point; absolute seconds alone do not show dominance"

    def test_absent_stage_usage_renders_nothing_rather_than_crashing(self):
        """Older runs and eval-only runs have no stage_usage at all."""
        results = {"a": {"model": "m", "before": {}, "after": {}}}
        assert self._render(results, ["a"]) == ""

    def test_unpriced_model_is_not_reported_as_free(self):
        results = {
            "a": {
                "model": "definitely-not-a-registered-model",
                "before": {"x": 0.5},
                "after": {"x": 0.6},
                "stage_usage": build_stage_usage(_stage(10, 20, 5.0), {}, _stage(10, 20, 5.0)),
            }
        }
        out = self._render(results, ["a"])
        assert "unpriced" in out.lower()

    def test_estimates_are_disclosed(self):
        results = {
            "a": {
                "model": "gemini-3.1-pro-preview",
                "before": {"x": 0.5},
                "after": {"x": 0.6},
                "stage_usage": build_stage_usage(
                    _stage(10, 20, 5.0, 0.1, 0.2, estimate=True), {}, {}
                ),
            }
        }
        assert "estimate" in self._render(results, ["a"]).lower()


class TestCostPerSurvivingPoint:
    """Pricing a delta that has not cleared the noise is how spend gets justified
    against noise. Campaign 07 saw a metric move 12.3x its floor between two runs of
    one manifest, so this is not hypothetical."""

    @staticmethod
    def _render(results, ordered):
        from wrangler.reporting.reporter import _cost_benefit_section

        return "\n".join(_cost_benefit_section(results, ordered))

    def test_without_a_control_arm_it_says_uncalibrated(self):
        """The common case: one-arm runs whose control lives in a different run.

        A plausible-looking number here, with no floor behind it, is the failure
        this column exists to prevent.
        """
        results = {
            "c07-pro": {
                "model": "gemini-3.1-pro-preview",
                "original_prompt": "seed",
                "optimized_prompt": "a much longer evolved prompt",
                "before": {"safety_v1": 0.88},
                "after": {"safety_v1": 0.97},
                "token_usage": {"input_tokens": 3900, "output_tokens": 35840},
            }
        }
        out = self._render(results, ["c07-pro"]).lower()
        assert "uncalibrated" in out

    @pytest.mark.parametrize("metric_delta", [0.09, -0.09])
    def test_it_never_prices_a_regression_as_value(self, metric_delta):
        results = {
            "ctrl": {  # control: prompt unchanged, so it measures the floor
                "model": "gemini-3.1-pro-preview",
                "original_prompt": "seed",
                "optimized_prompt": "seed",
                "before": {"safety_v1": 0.80},
                "after": {"safety_v1": 0.81},
            },
            "arm": {
                "model": "gemini-3.1-pro-preview",
                "original_prompt": "seed",
                "optimized_prompt": "evolved",
                "before": {"safety_v1": 0.80},
                "after": {"safety_v1": 0.80 + metric_delta},
                "token_usage": {"input_tokens": 100, "output_tokens": 1000},
            },
        }
        out = self._render(results, ["ctrl", "arm"])
        assert "$-" not in out, "a regression must never render as negative dollars-per-point"
