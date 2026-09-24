"""Tests for the per-metric cost-quality Pareto frontier.

The frontier replaces a scatter that averaged five metrics into one scalar and priced models
at list rate. Two things here are load-bearing:

* **Domination must respect resolution.** Without that the frontier is decided by noise, which
  is the same error the averaged scalar made in different clothing.
* **The arithmetic must be testable without a bucket.** Same split as `campaign_floor`: a thin
  GCS fetch, and pure computation over plain dicts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wrangler.reporting.frontier import (
    ArmPoint,
    dominates,
    frontier_for_metric,
    frontier_membership,
)

M = "safety_v1"


def _pt(arm, cost, **quality):
    return ArmPoint(
        arm=arm, model=f"model-{arm}", cost_usd=cost, quality=dict(quality), coverage=1.0
    )


class TestDominance:
    def test_cheaper_and_clearly_better_dominates(self):
        a, b = _pt("a", 1.0, **{M: 0.90}), _pt("b", 2.0, **{M: 0.50})
        assert dominates(a, b, M, resolution=0.05)

    def test_cheaper_but_worse_does_not_dominate(self):
        a, b = _pt("a", 1.0, **{M: 0.50}), _pt("b", 2.0, **{M: 0.90})
        assert not dominates(a, b, M, resolution=0.05)

    def test_better_but_more_expensive_does_not_dominate(self):
        a, b = _pt("a", 5.0, **{M: 0.90}), _pt("b", 2.0, **{M: 0.50})
        assert not dominates(a, b, M, resolution=0.05)

    def test_a_gap_inside_the_resolution_is_not_domination(self):
        """THE load-bearing case. 0.90 vs 0.88 is not a quality difference this design can
        resolve, so the cheaper arm has not earned the frontier to itself."""
        a, b = _pt("a", 1.0, **{M: 0.90}), _pt("b", 2.0, **{M: 0.88})
        assert not dominates(a, b, M, resolution=0.05)
        assert dominates(a, b, M, resolution=0.01)

    def test_equal_cost_is_not_cheaper(self):
        """Domination needs a strict cost advantage, or two identical arms dominate each other."""
        a, b = _pt("a", 2.0, **{M: 0.90}), _pt("b", 2.0, **{M: 0.50})
        assert not dominates(a, b, M, resolution=0.05)

    def test_a_missing_metric_never_dominates(self):
        """An arm that did not score a metric must not win it by absence."""
        a, b = _pt("a", 1.0), _pt("b", 2.0, **{M: 0.50})
        assert not dominates(a, b, M, resolution=0.05)
        assert not dominates(b, a, M, resolution=0.05)


class TestFrontierForMetric:
    def test_a_dominated_arm_is_excluded(self):
        pts = [
            _pt("cheap_good", 1.0, **{M: 0.90}),
            _pt("dear_bad", 5.0, **{M: 0.40}),
        ]
        assert frontier_for_metric(pts, M, resolution=0.05) == ["cheap_good"]

    def test_a_genuine_tradeoff_keeps_both(self):
        """Cheap-and-worse against dear-and-better is the whole point of a frontier."""
        pts = [_pt("cheap", 1.0, **{M: 0.60}), _pt("dear", 5.0, **{M: 0.95})]
        assert set(frontier_for_metric(pts, M, resolution=0.05)) == {"cheap", "dear"}

    def test_arms_within_resolution_are_both_members(self):
        pts = [_pt("cheap", 1.0, **{M: 0.90}), _pt("dear", 5.0, **{M: 0.92})]
        assert set(frontier_for_metric(pts, M, resolution=0.10)) == {"cheap", "dear"}

    def test_result_is_sorted_for_stable_rendering(self):
        pts = [_pt("z", 1.0, **{M: 0.9}), _pt("a", 2.0, **{M: 0.95})]
        assert frontier_for_metric(pts, M, resolution=0.01) == sorted(
            frontier_for_metric(pts, M, resolution=0.01)
        )

    @pytest.mark.parametrize("pts", [[], [_pt("solo", 1.0, **{M: 0.5})]])
    def test_degenerate_inputs_do_not_raise(self, pts):
        got = frontier_for_metric(pts, M, resolution=0.05)
        assert got == [p.arm for p in pts]

    def test_arms_missing_the_metric_are_absent_from_its_frontier(self):
        pts = [_pt("scored", 1.0, **{M: 0.5}), _pt("unscored", 0.5)]
        assert frontier_for_metric(pts, M, resolution=0.05) == ["scored"]

    def test_a_metric_nobody_scored_yields_an_empty_frontier(self):
        """Not an error, and not everybody: a metric no arm scored has no frontier, and a
        ten-tier report must not be lost to one unscored metric."""
        assert frontier_for_metric([_pt("a", 1.0), _pt("b", 2.0)], M, resolution=0.05) == []


class TestFrontierMembership:
    def test_an_arm_winning_one_metric_and_losing_another_counts_once(self):
        """The reason to report membership per metric rather than a pooled score: campaign 09
        measured metrics moving in opposite directions."""
        pts = [
            _pt("cheap", 1.0, safety_v1=0.95, hallucination_v1=0.40),
            _pt("dear", 5.0, safety_v1=0.50, hallucination_v1=0.95),
        ]
        res = {"safety_v1": 0.05, "hallucination_v1": 0.05}
        counts = frontier_membership(pts, res)
        assert counts["cheap"] == 2  # cheapest, and better on safety
        assert counts["dear"] == 1  # earns its place on hallucination only

    def test_membership_counts_every_metric_present(self):
        pts = [_pt("a", 1.0, safety_v1=0.9, hallucination_v1=0.9)]
        counts = frontier_membership(pts, {"safety_v1": 0.05, "hallucination_v1": 0.05})
        assert counts == {"a": 2}

    def test_a_metric_with_no_resolution_given_is_skipped(self):
        """Silently assuming a resolution of zero would let noise decide that metric."""
        pts = [_pt("a", 1.0, safety_v1=0.9, unknown_v1=0.9)]
        counts = frontier_membership(pts, {"safety_v1": 0.05})
        assert counts == {"a": 1}


class TestCostForArm:
    """Cost is summed across both eval sides and is never metered. Each assertion here
    guards a distinct way the number can be misread."""

    def _side(self, inp, out, *, estimate=True):
        return {"token_usage": {"input_tokens": inp, "output_tokens": out, "is_estimate": estimate}}

    def test_it_sums_both_eval_sides(self):
        from wrangler.reporting.frontier import cost_for_arm

        got = cost_for_arm(self._side(1000, 1000), self._side(1000, 1000), "gemini-3.5-flash")
        one = cost_for_arm(self._side(1000, 1000), {}, "gemini-3.5-flash")
        assert got["cost_usd"] == pytest.approx(2 * one["cost_usd"])

    def test_estimate_flag_travels_with_the_number(self):
        """There is no metered token count in this system; a dollar figure that does not
        say so gets quoted as though it were billing data."""
        from wrangler.reporting.frontier import cost_for_arm

        assert cost_for_arm(self._side(10, 10), self._side(10, 10), "gemini-3.5-flash")[
            "is_estimate"
        ]

    def test_an_unregistered_model_is_unpriced_not_free(self):
        """A $0.00 row with no explanation is how an unpriced model is read as a free one."""
        from wrangler.reporting.frontier import cost_for_arm

        got = cost_for_arm(self._side(10_000, 10_000), self._side(0, 0), "not-a-real-model")
        assert got["priced"] is False

    def test_a_registered_model_is_priced_and_positive(self):
        from wrangler.reporting.frontier import cost_for_arm

        got = cost_for_arm(self._side(1_000_000, 1_000_000), {}, "gemini-3.5-flash")
        assert got["priced"] is True
        assert got["cost_usd"] > 0

    def test_a_manifest_cost_override_is_honoured(self):
        from wrangler.reporting.frontier import cost_for_arm

        got = cost_for_arm(
            self._side(1_000_000, 0),
            {},
            "not-a-real-model",
            custom_costs={"input": 2.0, "output": 4.0},
        )
        assert got["cost_usd"] == pytest.approx(2.0)
        assert got["priced"] is True

    def test_a_missing_side_is_treated_as_zero_not_an_error(self):
        """An eval-only or failed-stage arm must still price what it did spend."""
        from wrangler.reporting.frontier import cost_for_arm

        assert cost_for_arm({}, {}, "gemini-3.5-flash")["cost_usd"] == 0.0

    def test_a_side_that_never_recorded_an_estimate_flag_is_still_called_an_estimate(self):
        """Defaulting to 'metered' on a missing flag would upgrade an unknown to a promise."""
        from wrangler.reporting.frontier import cost_for_arm

        assert cost_for_arm({"token_usage": {"input_tokens": 5}}, {}, "gemini-3.5-flash")[
            "is_estimate"
        ]


class TestCostAgreesWithTheArtifact:
    """Recomputing from tokens must reproduce what the stage recorded, or a report carries
    two different cost numbers for one run."""

    FIXTURE = "tests/fixtures/c09/eval_before/c09-rationale-on.json"

    def test_recomputed_cost_matches_the_recorded_cost(self):
        import json

        from wrangler.reporting.frontier import cost_for_arm

        side = json.loads(Path(self.FIXTURE).read_text())
        recorded = side["costs"]["input_usd"] + side["costs"]["output_usd"]
        # campaign 09 ran claude-sonnet-5; the stage priced it from the same registry.
        got = cost_for_arm(side, {}, "claude-sonnet-5")
        assert got["cost_usd"] == pytest.approx(recorded, rel=1e-6)

    def test_the_wrong_model_id_changes_the_answer_materially(self):
        """Guards against inferring a tier's model rather than reading it: the same tokens
        priced as sonnet-4-6 cost 50% more than as sonnet-5."""
        import json

        from wrangler.reporting.frontier import cost_for_arm

        side = json.loads(Path(self.FIXTURE).read_text())
        right = cost_for_arm(side, {}, "claude-sonnet-5")["cost_usd"]
        wrong = cost_for_arm(side, {}, "claude-sonnet-4-6")["cost_usd"]
        assert abs(wrong - right) / right > 0.25
