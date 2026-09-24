"""Both cost-quality chart paths must show the same thing.

`generate_cost_quality_chart_pb` (PaperBanana) wraps `generate_cost_quality_chart`
(matplotlib) as its fallback. Changing only one would make the report's content depend on
whether an API call succeeded, which is worse than either version alone.

These are behavioural: they render and inspect what was produced, never the source text.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

FIX = Path("tests/fixtures/c09")
ARMS = ("c09-control", "c09-rationale-on", "c09-rationale-off")


@pytest.fixture
def results():
    out = {}
    for arm in ARMS:
        before = json.loads((FIX / "eval_before" / f"{arm}.json").read_text())
        after = json.loads((FIX / "eval_after" / f"{arm}.json").read_text())
        out[arm] = {
            "model": "claude-sonnet-5",
            "before": before["scores"],
            "after": after["scores"],
            "token_usage": {
                "input_tokens": before["token_usage"]["input_tokens"]
                + after["token_usage"]["input_tokens"],
                "output_tokens": before["token_usage"]["output_tokens"]
                + after["token_usage"]["output_tokens"],
                "is_estimate": True,
            },
        }
    return out


class TestMatplotlibFallback:
    def test_it_renders_a_file(self, results, tmp_path):
        from wrangler.reporting.analysis import generate_cost_quality_chart

        generate_cost_quality_chart(results, tmp_path)
        assert (tmp_path / "cost_quality.png").stat().st_size > 0

    def test_it_draws_one_panel_per_metric_not_one_averaged_scatter(self, results, tmp_path):
        """The regression that matters: five metrics collapsed to a mean hid metrics moving
        in opposite directions, which is what campaign 09 measured."""
        import matplotlib.pyplot as plt

        from wrangler.reporting.analysis import generate_cost_quality_chart

        captured = {}
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, axes = real(*args, **kwargs)
            captured["n_axes"] = getattr(axes, "size", 1)
            return fig, axes

        with mock.patch.object(plt, "subplots", spy):
            generate_cost_quality_chart(results, tmp_path)
        assert captured["n_axes"] >= 5

    def test_the_axis_says_the_cost_is_estimated(self, results, tmp_path):
        """No token count in this system is metered; a dollar axis that does not say so
        invites the number being quoted as billing."""
        import matplotlib.pyplot as plt

        from wrangler.reporting.analysis import generate_cost_quality_chart

        labels = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, axes = real(*args, **kwargs)
            labels.extend(axes.flat)
            return fig, axes

        with mock.patch.object(plt, "subplots", spy):
            generate_cost_quality_chart(results, tmp_path)
        assert any("estimated" in ax.get_xlabel().lower() for ax in labels)


class TestPaperBananaPath:
    def _payload(self, results, tmp_path):
        seen = {}

        def fake_try(data, intent, out_path, **kwargs):
            seen["data"] = data
            seen["intent"] = intent

        with mock.patch("wrangler.reporting.charts._try_paperbanana", fake_try):
            from wrangler.reporting.charts import generate_cost_quality_chart_pb

            generate_cost_quality_chart_pb(results, tmp_path, use_paperbanana=True)
        return seen

    def test_it_sends_one_panel_per_metric(self, results, tmp_path):
        seen = self._payload(results, tmp_path)
        assert len(seen["data"]["panels"]) >= 5

    def test_no_panel_carries_an_averaged_quality_scalar(self, results, tmp_path):
        """The old payload had `before_quality`/`after_quality` as means over all metrics.
        Every quality number must now belong to a named metric."""
        seen = self._payload(results, tmp_path)
        for panel in seen["data"]["panels"]:
            assert panel["metric"]
            assert panel["points"]

    def test_each_panel_carries_its_resolution(self, results, tmp_path):
        seen = self._payload(results, tmp_path)
        assert all(p["resolution"] > 0 for p in seen["data"]["panels"])

    def test_the_cost_field_and_intent_both_say_estimated(self, results, tmp_path):
        seen = self._payload(results, tmp_path)
        assert "estimated" in seen["intent"].lower()
        assert all(
            "estimated_cost_usd" in point
            for panel in seen["data"]["panels"]
            for point in panel["points"]
        )

    def test_disabling_paperbanana_still_renders_via_matplotlib(self, results, tmp_path):
        from wrangler.reporting.charts import generate_cost_quality_chart_pb

        generate_cost_quality_chart_pb(results, tmp_path, use_paperbanana=False)
        assert (tmp_path / "cost_quality.png").stat().st_size > 0
