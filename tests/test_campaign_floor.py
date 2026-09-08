"""Reading a campaign's floor must be a command, not a shell script.

`floor_from_control_arm` and `drift_sign_summary` existed but nothing called
them, so the 2026-09-02 floor was still worked out by pasting Python into a
terminal. That is exactly how CLAUDE.md acquired two floor figures nobody can
re-derive. This closes the loop: one command over the campaign's run ids
produces the table the DOE write-up needs.

The pure functions are tested here. The GCS fetch is thin by design -- it maps
run ids to artifacts and does nothing else -- so the arithmetic stays testable
without a bucket.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wrangler.reporting.campaign_floor import render_markdown, summarize_arms

FIXTURE = Path(__file__).parent / "fixtures" / "c06_validation"


def _real_arm() -> tuple[dict, dict]:
    return (
        json.loads((FIXTURE / "eval_before.json").read_text()),
        json.loads((FIXTURE / "eval_after.json").read_text()),
    )


def _synthetic(deltas: dict[str, float], base: float = 0.8) -> tuple[dict, dict]:
    before = {"scores": dict.fromkeys(deltas, base), "per_case": [], "cases_total": 0}
    after = {"scores": {m: base + d for m, d in deltas.items()}, "per_case": [], "cases_total": 0}
    return before, after


class TestSummarizeArms:
    def test_one_real_arm_reproduces_the_hand_computation(self):
        s = summarize_arms({"claude-n1": _real_arm()})
        arm = s["arms"]["claude-n1"]
        assert arm["floor"] == pytest.approx(0.0747, abs=5e-4)
        assert arm["coverage"]["before"] == pytest.approx(1.0)

    def test_the_pooled_floor_takes_the_worst_per_metric(self):
        """Two arms disagreeing is information about how variable the floor is.

        Averaging would understate the noise, which is the one direction that
        manufactures false wins.
        """
        s = summarize_arms(
            {
                "a": _synthetic({"m": -0.02, "n": 0.09}),
                "b": _synthetic({"m": 0.05, "n": -0.01}),
            }
        )
        assert s["pooled"]["m"] == pytest.approx(0.05)
        assert s["pooled"]["n"] == pytest.approx(0.09)

    def test_the_headline_floor_is_the_worst_pooled_metric(self):
        s = summarize_arms({"a": _synthetic({"m": 0.02, "n": -0.09})})
        assert s["floor"] == pytest.approx(0.09)

    def test_drift_is_summarised_per_arm_not_per_metric_cell(self):
        """The metrics are not independent; the arms are. Guards that."""
        s = summarize_arms(
            {
                "a": _synthetic({"m": -0.02, "n": -0.03, "o": -0.01}),
                "b": _synthetic({"m": 0.02, "n": 0.03, "o": 0.01}),
            }
        )
        assert s["drift"]["arms_total"] == 2
        assert s["drift"]["arms_negative"] == 1
        assert s["drift"]["consistent"] is False

    def test_unanimous_negative_arms_are_flagged_consistent(self):
        s = summarize_arms(
            {
                "a": _synthetic({"m": -0.02, "n": -0.03}),
                "b": _synthetic({"m": -0.05, "n": -0.01}),
            }
        )
        assert s["drift"]["consistent"] is True

    def test_an_arm_with_asymmetric_coverage_is_flagged(self):
        """A delta across a coverage gap is mostly dropout, not noise.

        Historical arms swung 42 points between their two sides; pooling one of
        those into a floor would describe the dropout instead.
        """
        before = {"scores": {"m": 0.8}, "per_case": [], "cases_scored": 64, "cases_total": 64}
        after = {"scores": {"m": 0.7}, "per_case": [], "cases_scored": 40, "cases_total": 64}
        s = summarize_arms({"lopsided": (before, after)})
        assert s["arms"]["lopsided"]["coverage_warning"] is True
        assert "lopsided" in s["warnings"][0]

    def test_a_balanced_arm_is_not_flagged(self):
        s = summarize_arms({"ok": _real_arm()})
        assert s["arms"]["ok"]["coverage_warning"] is False
        assert s["warnings"] == []

    def test_no_arms_yields_no_floor_rather_than_zero(self):
        s = summarize_arms({})
        assert s["floor"] is None
        assert s["pooled"] == {}


class TestRenderMarkdown:
    def test_it_names_every_arm_and_the_headline_floor(self):
        md = render_markdown(summarize_arms({"claude-n1": _real_arm()}))
        assert "claude-n1" in md
        assert "0.07" in md

    def test_it_reports_paired_and_unpaired_side_by_side(self):
        """Both, always. They disagreed 44-64% on the arm this was built from,
        and reporting only one either flatters the pipeline or hides a lever."""
        md = render_markdown(summarize_arms({"claude-n1": _real_arm()}))
        assert "paired" in md.lower()
        assert "unpaired" in md.lower()

    def test_coverage_appears_next_to_every_arm(self):
        md = render_markdown(summarize_arms({"claude-n1": _real_arm()}))
        assert "coverage" in md.lower()

    def test_a_coverage_warning_is_surfaced_not_buried(self):
        before = {"scores": {"m": 0.8}, "per_case": [], "cases_scored": 64, "cases_total": 64}
        after = {"scores": {"m": 0.7}, "per_case": [], "cases_scored": 40, "cases_total": 64}
        md = render_markdown(summarize_arms({"bad": (before, after)}))
        assert "⚠" in md or "warning" in md.lower()

    def test_it_states_the_sqrt_n_caveat_when_levels_are_few(self):
        """Two points cannot distinguish sqrt(n) from any decreasing curve."""
        md = render_markdown(summarize_arms({"a": _real_arm()}))
        assert "two points" in md.lower() or "cannot" in md.lower()
