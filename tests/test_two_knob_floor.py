"""The floor is a function of TWO knobs, and the model must refuse to guess either.

`minimum_detectable_effect()` was written when `num_runs` was the only lever, and its
central design decision was that it **declines to extrapolate** rather than assume sqrt(n) --
because campaign 06 existed to measure that scaling, and a function assuming it would have
presupposed the campaign's answer and then been used to read the campaign's results.

`score_repeats` (2026-09-17) adds a second axis with its own, different scaling: DOE 02
measured the judge disagreeing with itself on 64/64 cases for `instruction_following_v1` and
**0/64** for `safety_v1`, so repeats buy a great deal on one metric and nothing at all on
another. The refusal has to survive onto the new axis, or the function will happily report a
`safety_v1` floor falling with `score_repeats` -- which DOE 02 says it does not.
"""

from __future__ import annotations

import pytest

from wrangler.reporting.analyzer import (
    CAPTURE_MIN,
    SCORING_MIN,
    arm_side_cost_min,
    cheapest_config_for,
    minimum_detectable_effect,
)


class TestTheOriginalContractIsUnchanged:
    """Adding an axis must not move the one-knob behaviour five callers rely on."""

    def test_at_the_measured_level_the_mde_is_the_floor(self):
        assert minimum_detectable_effect(0.0685, measured_at_runs=1) == pytest.approx(0.0685)

    def test_it_still_refuses_to_extrapolate_runs_without_a_scaling(self):
        assert minimum_detectable_effect(0.0685, measured_at_runs=1, target_runs=3) is None

    def test_it_still_extrapolates_when_told_how(self):
        assert minimum_detectable_effect(
            0.06, measured_at_runs=1, target_runs=4, scaling_exponent=0.5
        ) == pytest.approx(0.03)

    def test_positional_calls_still_work(self):
        """`minimum_detectable_effect(0.06, 1, 3, scaling_exponent=0.5)` is in the suite."""
        assert minimum_detectable_effect(0.06, 1, 3, scaling_exponent=0.5) is not None

    def test_no_floor_means_no_mde(self):
        assert minimum_detectable_effect(None, measured_at_runs=1) is None


class TestTheRepeatsAxis:
    def test_staying_at_the_measured_repeats_needs_no_exponent(self):
        assert minimum_detectable_effect(
            0.02, measured_at_runs=3, measured_at_repeats=1, target_repeats=1
        ) == pytest.approx(0.02)

    def test_it_refuses_to_extrapolate_repeats_without_a_scaling(self):
        """The whole point, carried onto the new axis."""
        assert (
            minimum_detectable_effect(
                0.02, measured_at_runs=3, measured_at_repeats=1, target_repeats=5
            )
            is None
        )

    def test_it_extrapolates_repeats_when_told_how(self):
        assert minimum_detectable_effect(
            0.02,
            measured_at_runs=3,
            measured_at_repeats=1,
            target_repeats=4,
            repeats_exponent=0.5,
        ) == pytest.approx(0.01)

    def test_a_zero_exponent_is_a_legitimate_answer(self):
        """safety_v1: judge sd 0.000, so repeats buy nothing. Flat is not 'unknown'."""
        assert minimum_detectable_effect(
            0.02,
            measured_at_runs=3,
            measured_at_repeats=1,
            target_repeats=5,
            repeats_exponent=0.0,
        ) == pytest.approx(0.02)

    def test_both_axes_compose(self):
        mde = minimum_detectable_effect(
            0.08,
            measured_at_runs=1,
            target_runs=4,
            scaling_exponent=0.5,
            measured_at_repeats=1,
            target_repeats=4,
            repeats_exponent=0.5,
        )
        assert mde == pytest.approx(0.02)  # halved twice

    def test_moving_both_axes_needs_both_exponents(self):
        assert (
            minimum_detectable_effect(
                0.08,
                measured_at_runs=1,
                target_runs=4,
                scaling_exponent=0.5,
                measured_at_repeats=1,
                target_repeats=4,
            )
            is None
        )


class TestCost:
    def test_the_iso_cost_cells_cost_the_same(self):
        """DOE 03's headline contrast is meaningless if they do not."""
        assert arm_side_cost_min(2, 1) == pytest.approx(10.8)
        assert arm_side_cost_min(1, 3) == pytest.approx(11.0)
        for r, s in [(3, 1), (2, 2), (1, 5)]:
            assert arm_side_cost_min(r, s) == pytest.approx(16.4, abs=0.3)

    def test_the_constants_are_the_measured_ones(self):
        assert (CAPTURE_MIN, SCORING_MIN) == (2.6, 2.8)


class TestCheapestConfig:
    def test_it_picks_the_cheaper_of_two_configs_that_both_clear_the_bar(self):
        """A judge-dominated metric should be told to buy repeats, not runs."""
        got = cheapest_config_for(
            0.011,
            floor=0.022,
            runs_exponent=0.0,  # runs buy nothing here
            repeats_exponent=0.5,  # repeats halve at 4x
        )
        assert got is not None
        r, s = got
        assert r == 1, f"bought num_runs={r} for a metric where runs do nothing"
        assert s >= 4

    def test_it_buys_runs_when_repeats_do_nothing(self):
        """safety_v1's shape: all agent-side."""
        got = cheapest_config_for(0.011, floor=0.022, runs_exponent=0.5, repeats_exponent=0.0)
        assert got is not None
        r, s = got
        assert s == 1, f"bought score_repeats={s} for a metric where repeats do nothing"
        assert r >= 4

    def test_it_returns_none_rather_than_guessing_when_an_exponent_is_missing(self):
        assert cheapest_config_for(0.011, floor=0.022) is None

    def test_it_returns_none_when_the_target_is_unreachable_in_budget(self):
        assert (
            cheapest_config_for(
                0.0001, floor=0.05, runs_exponent=0.5, repeats_exponent=0.5, max_runs=3
            )
            is None
        )

    def test_an_already_sufficient_floor_costs_the_minimum(self):
        assert cheapest_config_for(0.05, floor=0.02, runs_exponent=0.5, repeats_exponent=0.5) == (
            1,
            1,
        )

    def test_no_floor_means_no_recommendation(self):
        assert cheapest_config_for(0.01, floor=None, runs_exponent=0.5) is None
