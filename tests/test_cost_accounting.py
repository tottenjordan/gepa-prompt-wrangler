"""Cost reported two ways: list price, and what was actually spent.

`blended_cost()` is a price-list lookup against an *assumed* 4:1 input:output
ratio. It is arithmetic you can do without running anything, and it cannot see
the effect that matters most in a tier comparison: a cheap-per-token model that
answers verbosely can cost more per run than an expensive terse one.

The eval already records `token_usage`, and the KFP component already turns it
into dollars — the reporter just never read either. So both bases now appear
side by side, because they answer different questions ("what does this model
list at" vs "what did this run cost") and picking one hides the other.
"""

import pytest

from wrangler.core.models import blended_cost, measured_cost


class TestMeasuredCost:
    def test_dollars_from_tokens_actually_spent(self):
        # gemini-3.1-flash-lite: $0.25/M in, $1.50/M out
        c = measured_cost("gemini-3.1-flash-lite", input_tokens=1_000_000, output_tokens=1_000_000)
        assert c["input_usd"] == pytest.approx(0.25)
        assert c["output_usd"] == pytest.approx(1.50)
        assert c["total_usd"] == pytest.approx(1.75)

    def test_zero_tokens_costs_nothing(self):
        c = measured_cost("gemini-3.1-flash-lite", 0, 0)
        assert c["total_usd"] == 0.0

    def test_a_verbose_cheap_model_can_cost_more_than_a_terse_expensive_one(self):
        """The whole reason list price is not enough."""
        cheap_verbose = measured_cost("gemini-3.1-flash-lite", 1_000, 400_000)
        dear_terse = measured_cost("gemini-3.1-pro-preview", 1_000, 5_000)
        assert blended_cost("gemini-3.1-flash-lite") < blended_cost("gemini-3.1-pro-preview")
        assert cheap_verbose["total_usd"] > dear_terse["total_usd"]

    def test_custom_costs_win_over_the_registry(self):
        """A manifest may price a model the registry does not know."""
        c = measured_cost(
            "gemini-3.1-flash-lite", 1_000_000, 0, custom_costs={"input": 9.0, "output": 1.0}
        )
        assert c["input_usd"] == pytest.approx(9.0)

    def test_an_unregistered_model_without_custom_costs_is_flagged_not_zeroed(self):
        """A $0.00 row with no explanation is how an unpriced model reads as free."""
        c = measured_cost("not-a-real-model", 1_000_000, 1_000_000)
        assert c["total_usd"] == 0.0
        assert c["priced"] is False

    def test_a_registered_model_is_marked_priced(self):
        assert measured_cost("gemini-3.5-flash", 10, 10)["priced"] is True

    def test_a_partial_costs_block_does_not_raise(self):
        """A manifest `costs:` block with only `input` set must degrade, not crash.

        `custom_costs["output"]` raised a bare KeyError out of measured_cost --
        taking down a report spanning ten pairs over one ad-hoc `costs:` block,
        which is exactly the failure this function's own docstring says an
        unregistered model must not cause.
        """
        c = measured_cost("gemini-3.5-flash", 1_000_000, 1_000_000, custom_costs={"input": 9.0})
        assert c["total_usd"] >= 0.0

    def test_a_partial_costs_block_is_not_silently_priced(self):
        """A missing side is not a free side.

        Filling the missing key with 0.0 would compute a real number for the
        known side and mark the row `priced=True` -- read as a complete,
        trustworthy price when half of it was never supplied. Treat a partial
        override exactly like no override was given at all: fall back to the
        registry (or to unpriced, if the model isn't registered there either).
        """
        c = measured_cost("not-a-real-model", 1_000_000, 1_000_000, custom_costs={"input": 9.0})
        assert c["priced"] is False
        assert c["total_usd"] == 0.0

    def test_a_partial_costs_block_falls_back_to_the_registry(self):
        """The model is registered; the partial override is discarded, not guessed."""
        registered = measured_cost("gemini-3.5-flash", 1_000_000, 1_000_000)
        partial = measured_cost(
            "gemini-3.5-flash", 1_000_000, 1_000_000, custom_costs={"input": 9.0}
        )
        assert partial == registered


class TestBothBasesAreReported:
    def test_the_cross_model_table_carries_list_and_measured(self):
        from wrangler.reporting.reporter import _cost_benefit_section

        results = {
            "cheap": {
                "model": "gemini-3.1-flash-lite",
                "before": {"q": 0.70},
                "after": {"q": 0.80},
                "token_usage": {"input_tokens": 100_000, "output_tokens": 50_000},
            },
            "dear": {
                "model": "gemini-3.1-pro-preview",
                "before": {"q": 0.75},
                "after": {"q": 0.85},
                "token_usage": {"input_tokens": 100_000, "output_tokens": 50_000},
            },
        }
        text = "\n".join(_cost_benefit_section(results, ["cheap", "dear"]))
        assert "Blended $/M" in text, "list price must stay"
        assert "Spend $" in text, "measured spend must appear"
        assert "$/quality pt" in text

    def test_a_run_with_no_token_data_says_so_rather_than_showing_zero(self):
        """Silently printing $0.00 would read as 'this run was free'."""
        from wrangler.reporting.reporter import _cost_benefit_section

        results = {"a": {"model": "gemini-3.5-flash", "before": {"q": 0.7}, "after": {"q": 0.8}}}
        text = "\n".join(_cost_benefit_section(results, ["a"]))
        assert "n/a" in text.lower()

    def test_it_survives_an_unpriced_model(self):
        from wrangler.reporting.reporter import _cost_benefit_section

        results = {
            "x": {
                "model": "some-unregistered-model",
                "before": {"q": 0.5},
                "after": {"q": 0.6},
                "token_usage": {"input_tokens": 10, "output_tokens": 10},
            }
        }
        text = "\n".join(_cost_benefit_section(results, ["x"]))
        assert "x" in text.lower()
