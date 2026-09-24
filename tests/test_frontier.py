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


def _arm_side(scores, *, coverage=1.0, inp=1000, out=1000):
    return {
        "scores": dict(scores),
        "coverage": coverage,
        "token_usage": {"input_tokens": inp, "output_tokens": out, "is_estimate": True},
    }


class TestSummarizeFrontier:
    """Takes `fetch_arms`' exact output shape so the GCS reader is reused, not rewritten."""

    def _arms(self):
        return {
            "cheap": (
                _arm_side({"safety_v1": 0.60}, inp=1000, out=1000),
                _arm_side({"safety_v1": 0.95}, inp=1000, out=1000),
            ),
            "dear": (
                _arm_side({"safety_v1": 0.90}, inp=50_000, out=50_000),
                _arm_side({"safety_v1": 0.92}, inp=50_000, out=50_000),
            ),
        }

    def _models(self):
        return {"cheap": "gemini-3.5-flash", "dear": "claude-sonnet-5"}

    def test_it_builds_a_point_per_arm_with_cost_and_quality(self):
        from wrangler.reporting.frontier import summarize_frontier

        s = summarize_frontier(self._arms(), self._models())
        assert {p.arm for p in s["points_after"]} == {"cheap", "dear"}
        cheap = next(p for p in s["points_after"] if p.arm == "cheap")
        assert cheap.quality["safety_v1"] == 0.95
        assert cheap.cost_usd > 0

    def test_it_reports_both_phases_so_an_arrow_can_be_drawn(self):
        from wrangler.reporting.frontier import summarize_frontier

        s = summarize_frontier(self._arms(), self._models())
        before = next(p for p in s["points_before"] if p.arm == "cheap")
        after = next(p for p in s["points_after"] if p.arm == "cheap")
        assert before.quality["safety_v1"] == 0.60
        assert after.quality["safety_v1"] == 0.95

    def test_every_dollar_figure_is_flagged_estimated(self):
        from wrangler.reporting.frontier import summarize_frontier

        s = summarize_frontier(self._arms(), self._models())
        assert all(p.is_estimate for p in s["points_after"])
        assert "estimate" in s["cost_note"].lower()

    def test_resolutions_come_from_the_design_not_from_zero(self):
        from wrangler.reporting.frontier import summarize_frontier

        s = summarize_frontier(self._arms(), self._models())
        assert s["resolutions"]["safety_v1"] > 0

    def test_the_variance_source_caveats_are_carried_into_the_output(self):
        """A caveat that stays in a docstring does not travel with a copied table."""
        from wrangler.reporting.frontier import summarize_frontier

        s = summarize_frontier(self._arms(), self._models())
        assert s["caveats"]
        assert any("one run per condition" in c.lower() for c in s["caveats"])


class TestIncomparableArmsAreExcludedLoudly:
    def test_a_large_coverage_gap_excludes_the_arm_with_a_reason(self):
        """campaign_floor.COVERAGE_GAP_LIMIT exists because a delta between a 47%- and an
        89%-covered side measures dropout. Plotting it would do the same."""
        from wrangler.reporting.frontier import summarize_frontier

        arms = {
            "lopsided": (
                _arm_side({"safety_v1": 0.5}, coverage=0.47),
                _arm_side({"safety_v1": 0.9}, coverage=0.89),
            ),
            "fine": (_arm_side({"safety_v1": 0.8}), _arm_side({"safety_v1": 0.85})),
        }
        s = summarize_frontier(arms, {"lopsided": "gemini-3.5-flash", "fine": "gemini-3.5-flash"})
        assert "lopsided" not in {p.arm for p in s["points_after"]}
        assert any("lopsided" in e for e in s["excluded"])
        assert any("coverage" in e.lower() for e in s["excluded"])

    def test_a_contaminated_arm_is_flagged_beside_its_tool_use_number(self):
        """Campaign 07 predates the silent-failure-12 fix; its tool-use numbers are
        uninterpretable and the note must travel with the table."""
        from wrangler.reporting.frontier import summarize_frontier

        arms = {
            "c07-pro": (
                _arm_side({"tool_use_quality_v1": 0.9}),
                _arm_side({"tool_use_quality_v1": 0.95}),
            )
        }
        s = summarize_frontier(arms, {"c07-pro": "gemini-3.5-flash"}, pre_fix_arms={"c07-pro"})
        assert any("c07-pro" in w and "tool_use" in w for w in s["warnings"])


def _pc(rows):
    """A stage artifact carrying per-case scores."""
    return {"per_case": [{"case_index": i, **r} for i, r in enumerate(rows)]}


class TestCrossesTier:
    """The demo-worthy question: does optimizing a cheap tier move it past an expensive one?"""

    def test_a_cheap_tier_that_overtakes_reports_crossed(self):
        from wrangler.reporting.frontier import crosses_tier

        arms = {
            "cheap": (_arm_side({M: 0.50}), _arm_side({M: 0.90})),
            "dear": (_arm_side({M: 0.70}), _arm_side({M: 0.72})),
        }
        got = crosses_tier(arms, "cheap", "dear", M, resolution=0.05)
        assert got["crossed"] is True
        assert got["gap"] == pytest.approx(0.20)

    def test_a_gap_inside_the_resolution_is_not_a_crossing(self):
        """Same discipline as domination: an unresolvable gap is not a result."""
        from wrangler.reporting.frontier import crosses_tier

        arms = {
            "cheap": (_arm_side({M: 0.50}), _arm_side({M: 0.72})),
            "dear": (_arm_side({M: 0.70}), _arm_side({M: 0.71})),
        }
        got = crosses_tier(arms, "cheap", "dear", M, resolution=0.05)
        assert got["crossed"] is False
        assert "resolution" in got["verdict"].lower()

    def test_it_compares_cheap_after_against_expensive_before(self):
        """The expensive tier's UNoptimized point is the bar: the question is whether a cheap
        optimized prompt buys what an expensive model gives you off the shelf."""
        from wrangler.reporting.frontier import crosses_tier

        arms = {
            "cheap": (_arm_side({M: 0.10}), _arm_side({M: 0.80})),
            "dear": (_arm_side({M: 0.70}), _arm_side({M: 0.99})),
        }
        got = crosses_tier(arms, "cheap", "dear", M, resolution=0.05)
        assert got["baseline"] == pytest.approx(0.70)
        assert got["crossed"] is True

    def test_a_missing_arm_is_reported_not_raised(self):
        from wrangler.reporting.frontier import crosses_tier

        got = crosses_tier({}, "cheap", "dear", M, resolution=0.05)
        assert got["crossed"] is None


class TestComplementarity:
    """FrugalGPT measured 13% of COQA items that GPT-4 got wrong and GPT-3 got right. A
    non-trivial number is what justifies a tier choice over a leaderboard ranking."""

    def test_it_counts_cases_each_arm_wins(self):
        from wrangler.reporting.frontier import complementarity

        a = _pc([{M: 1.0}, {M: 0.0}, {M: 1.0}, {M: 0.0}])
        b = _pc([{M: 0.0}, {M: 1.0}, {M: 1.0}, {M: 0.0}])
        got = complementarity({"a": (a, a), "b": (b, b)}, "a", "b", M)
        assert got["a_only"] == 1
        assert got["b_only"] == 1
        assert got["n_common"] == 4

    def test_it_reports_a_fraction_not_just_a_count(self):
        from wrangler.reporting.frontier import complementarity

        a = _pc([{M: 1.0}, {M: 0.0}])
        b = _pc([{M: 0.0}, {M: 0.0}])
        got = complementarity({"a": (a, a), "b": (b, b)}, "a", "b", M)
        assert got["a_only_frac"] == pytest.approx(0.5)

    def test_only_cases_both_arms_scored_are_compared(self):
        """Unmatched cases are the dropout that silent-failures #5 showed reads as an effect."""
        from wrangler.reporting.frontier import complementarity

        a = {"per_case": [{"case_index": 0, M: 1.0}, {"case_index": 1, M: 1.0}]}
        b = {"per_case": [{"case_index": 0, M: 0.0}]}
        got = complementarity({"a": (a, a), "b": (b, b)}, "a", "b", M)
        assert got["n_common"] == 1

    def test_no_common_cases_reports_zero_rather_than_dividing(self):
        from wrangler.reporting.frontier import complementarity

        a = {"per_case": [{"case_index": 0, M: 1.0}]}
        b = {"per_case": [{"case_index": 9, M: 1.0}]}
        got = complementarity({"a": (a, a), "b": (b, b)}, "a", "b", M)
        assert got["n_common"] == 0
        assert got["a_only_frac"] == 0.0

    def test_a_high_judge_noise_metric_shows_near_total_disagreement(self):
        """Regression pin on the caveat: on campaign 09's real artifacts,
        instruction_following_v1 (64/64 judge self-disagreement per DOE 02) has a winner on
        nearly every case, while safety_v1 (0/64) has one on about a fifth. If this inverts,
        the caveat in the docstring is wrong."""
        import json

        from wrangler.reporting.frontier import complementarity

        arms = {
            a: (
                json.loads(Path(f"tests/fixtures/c09/eval_before/{a}.json").read_text()),
                json.loads(Path(f"tests/fixtures/c09/eval_after/{a}.json").read_text()),
            )
            for a in ("c09-rationale-on", "c09-rationale-off")
        }
        noisy = complementarity(
            arms, "c09-rationale-on", "c09-rationale-off", "instruction_following_v1"
        )
        quiet = complementarity(arms, "c09-rationale-on", "c09-rationale-off", "safety_v1")
        noisy_rate = noisy["a_only_frac"] + noisy["b_only_frac"]
        quiet_rate = quiet["a_only_frac"] + quiet["b_only_frac"]
        assert noisy_rate > 0.9
        assert quiet_rate < 0.4
