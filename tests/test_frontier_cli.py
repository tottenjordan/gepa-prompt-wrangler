"""Tests for `wrangler frontier` and the markdown it renders.

`render_markdown` is what a reader copies into a doc, so the things that qualify a number --
that costs are estimated, that domination is thresholded, that an arm is contaminated -- have
to survive being rendered. A caveat held only in a docstring does not travel with a table.

The GCS fetches are mocked; per the module's own split, nothing here needs a bucket.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from wrangler.cli import main
from wrangler.reporting.frontier import render_markdown, summarize_frontier

FIX = Path("tests/fixtures/c09")


def _side(scores, *, coverage=1.0, inp=1000, out=1000):
    return {
        "scores": dict(scores),
        "coverage": coverage,
        "num_runs": 2,
        "token_usage": {"input_tokens": inp, "output_tokens": out, "is_estimate": True},
    }


@pytest.fixture
def arms():
    return {
        "cheap": (_side({"safety_v1": 0.60}), _side({"safety_v1": 0.95})),
        "dear": (
            _side({"safety_v1": 0.90}, inp=50_000, out=50_000),
            _side({"safety_v1": 0.92}, inp=50_000, out=50_000),
        ),
    }


@pytest.fixture
def models():
    return {"cheap": "gemini-3.5-flash", "dear": "claude-sonnet-5"}


class TestRenderMarkdown:
    def test_it_renders_a_row_per_arm(self, arms, models):
        out = render_markdown(summarize_frontier(arms, models))
        assert "| cheap |" in out
        assert "| dear |" in out

    def test_it_says_the_cost_is_estimated(self, arms, models):
        """The single most quotable number in the table is a dollar figure, and none of them
        are metered."""
        out = render_markdown(summarize_frontier(arms, models))
        assert "ESTIMATED" in out or "estimated" in out.lower()

    def test_it_states_the_domination_rule(self, arms, models):
        """A different threshold gives a different table, so the reader must see which one
        produced theirs."""
        out = render_markdown(summarize_frontier(arms, models))
        assert "minimum detectable effect" in out.lower()

    def test_it_carries_the_variance_caveats(self, arms, models):
        out = render_markdown(summarize_frontier(arms, models))
        assert "one run per condition" in out.lower()

    def test_an_unpriced_arm_renders_na_not_zero_dollars(self, arms):
        """A $0.00 row with no explanation is how an unpriced model is read as a free one."""
        out = render_markdown(summarize_frontier(arms, {"cheap": "nope", "dear": "nope"}))
        assert "n/a" in out
        assert "$0.000" not in out

    def test_a_contaminated_arm_warns_in_the_rendered_output(self, arms, models):
        out = render_markdown(summarize_frontier(arms, models, pre_fix_arms={"cheap"}))
        assert "silent-failure-12" in out

    def test_an_excluded_arm_appears_with_its_reason(self, models):
        lopsided = {
            "cheap": (
                _side({"safety_v1": 0.5}, coverage=0.47),
                _side({"safety_v1": 0.9}, coverage=0.89),
            ),
            "dear": (_side({"safety_v1": 0.9}), _side({"safety_v1": 0.9})),
        }
        out = render_markdown(summarize_frontier(lopsided, models))
        assert "Excluded" in out
        assert "coverage" in out.lower()

    def test_frontier_membership_is_shown_per_arm(self, arms, models):
        out = render_markdown(summarize_frontier(arms, models))
        assert "on frontier" in out.lower()

    def test_no_pooled_average_is_rendered(self, arms, models):
        """The whole point: a single quality scalar is the thing being removed."""
        out = render_markdown(summarize_frontier(arms, models))
        assert "average quality" not in out.lower()


class TestFrontierCommand:
    def _invoke(self, argv, arms, models):
        with (
            mock.patch("wrangler.reporting.campaign_floor.fetch_arms", return_value=arms),
            mock.patch("wrangler.reporting.frontier.fetch_models", return_value=models),
        ):
            return CliRunner().invoke(main, argv)

    def test_it_is_registered(self):
        result = CliRunner().invoke(main, ["--help"])
        assert "frontier" in result.output

    def test_it_renders_the_table(self, arms, models):
        result = self._invoke(["frontier", "run-a", "--bucket", "b"], arms, models)
        assert result.exit_code == 0, result.output
        assert "Cost-quality frontier" in result.output

    def test_it_fails_clearly_when_no_arms_are_found(self, models):
        result = self._invoke(["frontier", "run-a", "--bucket", "b"], {}, models)
        assert result.exit_code != 0
        assert "no arms found" in result.output

    def test_it_warns_when_an_arm_has_no_deploy_artifact(self, arms):
        """An unknown model cannot be priced, and an unpriced arm on a cost axis is worse
        than an absent one -- so say so rather than rendering a silent zero."""
        result = self._invoke(
            ["frontier", "run-a", "--bucket", "b"], arms, {"cheap": "gemini-3.5-flash"}
        )
        assert "no deploy artifact" in result.output
        assert "dear" in result.output

    def test_the_crossing_test_runs_only_when_both_tiers_are_named(self, arms, models):
        plain = self._invoke(["frontier", "run-a", "--bucket", "b"], arms, models)
        assert "reach the expensive tier" not in plain.output

        crossed = self._invoke(
            ["frontier", "run-a", "--bucket", "b", "--cheap", "cheap", "--expensive", "dear"],
            arms,
            models,
        )
        assert "reach the expensive tier" in crossed.output
        assert "safety_v1" in crossed.output

    def test_the_chart_is_opt_in(self, arms, models, tmp_path):
        """A cross-run analysis should print a table by default; rendering a PNG is a
        side effect the caller asks for."""
        plain = self._invoke(["frontier", "run-a", "--bucket", "b"], arms, models)
        assert not list(tmp_path.glob("*.png"))
        assert plain.exit_code == 0

        charted = self._invoke(
            ["frontier", "run-a", "--bucket", "b", "--chart", str(tmp_path)], arms, models
        )
        assert charted.exit_code == 0, charted.output
        assert (tmp_path / "cost_quality.png").exists()


class TestOnRealArtifacts:
    def test_it_renders_campaign_09_without_a_bucket(self):
        """End to end over committed fixtures: if this needs GCS, the split between fetch
        and arithmetic is wrong."""
        arms = {
            a: (
                json.loads((FIX / "eval_before" / f"{a}.json").read_text()),
                json.loads((FIX / "eval_after" / f"{a}.json").read_text()),
            )
            for a in ("c09-control", "c09-rationale-on", "c09-rationale-off")
        }
        models = dict.fromkeys(arms, "claude-sonnet-5")
        out = render_markdown(summarize_frontier(arms, models))
        assert "c09-control" in out
        # All three are one model differing by less than the design resolves, so nothing
        # dominates and every arm stays on every frontier.
        assert "5/5" in out
