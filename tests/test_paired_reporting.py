"""Reported deltas and floors must both be paired on `case_index`, together.

A delta computed as `after_mean - before_mean` partly measures *which cases were
scored*: on 2026-08-22 a control arm whose prompt was byte-identical on both
sides produced +0.039 and +0.035 that way, and nearly got promoted as a win.
`paired_deltas()` has existed since, and nothing in the reporting path used it.

The second half matters as much as the first. `measure_noise_floor_per_metric`
builds its floors out of `pair.deltas`, so if deltas pair while floors do not,
`classify_deltas` weighs a paired delta against an unpaired bar and every
verdict in every report shifts without anyone choosing it.

The acceptance figures come from `docs/analysis/2026-09-22-campaign-09-reanalysis.md`,
computed by hand from the artifacts committed at `tests/fixtures/c09/`.
"""

import json
from pathlib import Path

import pytest

from wrangler.eval.evaluator import CASE_INDEX_KEY
from wrangler.reporting.analyzer import (
    ExperimentAnalysis,
    PairAnalysis,
    classify_deltas,
    delta_comparison,
    format_analysis_report,
    measure_noise_floor_per_metric,
    noise_floor_comparison,
)

C09 = Path(__file__).parent / "fixtures" / "c09"

# docs/analysis/2026-09-22-campaign-09-reanalysis.md, `safety_v1`, paired per case.
PUBLISHED_PAIRED = {
    "c09-control": 0.0794,
    "c09-rationale-on": 0.1746,
    "c09-rationale-off": 0.1719,
}
# What the same three arms read as a difference of two run means.
PUBLISHED_UNPAIRED = {
    "c09-control": 0.0732,
    "c09-rationale-on": 0.1607,
    "c09-rationale-off": 0.1651,
}
# Two of the six eval sides scored 63 of 64 cases, which is why the two columns
# disagree at all.
PUBLISHED_N_PAIRED = {"c09-control": 63, "c09-rationale-on": 63, "c09-rationale-off": 64}


def _c09_pair(arm: str) -> PairAnalysis:
    before = json.loads((C09 / "eval_before" / f"{arm}.json").read_text())
    after = json.loads((C09 / "eval_after" / f"{arm}.json").read_text())
    return PairAnalysis(
        pair_id=arm,
        model="gemini-3.5-flash",
        before=before["scores"],
        after=after["scores"],
        before_per_case=before["per_case"],
        after_per_case=after["per_case"],
        original_prompt="seed prompt",
        # The control's prompt is unchanged by construction; the other two arms
        # were optimized.
        optimized_prompt="seed prompt" if arm == "c09-control" else "an optimized prompt",
    )


class TestCampaign09Acceptance:
    """Reproduce the hand-computed reanalysis, or the pairing is wrong."""

    @pytest.mark.parametrize("arm", sorted(PUBLISHED_PAIRED))
    def test_safety_delta_matches_the_published_per_case_figure(self, arm):
        """`safety_v1` paired over the cases both sides scored.

        Prevents a pairing that looks plausible but matches the wrong cases --
        by position, or across a dropped case -- from shipping. These three
        numbers were computed by hand from these exact artifacts.
        """
        got = _c09_pair(arm).deltas["safety_v1"]
        assert got == pytest.approx(PUBLISHED_PAIRED[arm], abs=5e-5)

    @pytest.mark.parametrize("arm", sorted(PUBLISHED_UNPAIRED))
    def test_the_unpaired_figure_is_still_reported_beside_it(self, arm):
        """Both numbers are surfaced, so a reader can see them disagree.

        Pairing moved this repo's floor by 44-64% on a real arm where CLAUDE.md
        claimed ~15%. Publishing only the new number would hide a re-baseline
        of every delta the project has reported.
        """
        detail = _c09_pair(arm).delta_detail
        assert detail["unpaired"]["safety_v1"] == pytest.approx(PUBLISHED_UNPAIRED[arm], abs=1e-4)
        assert detail["paired"]["safety_v1"] == pytest.approx(PUBLISHED_PAIRED[arm], abs=5e-5)

    @pytest.mark.parametrize("arm", sorted(PUBLISHED_N_PAIRED))
    def test_n_paired_travels_with_the_delta(self, arm):
        """A paired delta over 12 of 64 cases is a different claim from one over 63.

        Without the count beside it, a badly dropped arm's delta reads exactly
        like a complete one.
        """
        pair = _c09_pair(arm)
        assert pair.n_paired == PUBLISHED_N_PAIRED[arm]
        assert pair.delta_detail["n_paired"] == PUBLISHED_N_PAIRED[arm]

    def test_the_paired_and_unpaired_figures_actually_differ(self):
        """Guards the premise: if they agreed, none of this would matter."""
        detail = _c09_pair("c09-control").delta_detail
        assert detail["paired"]["safety_v1"] != detail["unpaired"]["safety_v1"]

    def test_the_source_of_every_delta_is_stated(self):
        assert _c09_pair("c09-control").delta_detail["source"] == "paired"


class TestAggregateFallback:
    """Older artifacts carry no per-case rows, and must keep working."""

    def test_a_pair_with_no_per_case_data_uses_the_aggregate(self):
        """Artifacts predating per-case capture would otherwise report nothing.

        Returning an empty delta set for them would erase every historical
        experiment's numbers from its own report.
        """
        pair = PairAnalysis(pair_id="old", model="m", before={"a": 0.5}, after={"a": 0.9})
        assert pair.deltas["a"] == pytest.approx(0.4)
        assert pair.delta_detail["source"] == "aggregate"
        assert pair.n_paired == 0

    def test_rows_without_a_case_index_fall_back_rather_than_match_by_position(self):
        """Positional matching would pair case 0 of one subset with case 0 of a
        *different* subset and present it as like-for-like."""
        pair = PairAnalysis(
            pair_id="old",
            model="m",
            before={"a": 0.5},
            after={"a": 0.9},
            before_per_case=[{"a": 0.1}, {"a": 0.9}],
            after_per_case=[{"a": 0.9}],
        )
        assert pair.n_paired == 0
        assert pair.delta_detail["source"] == "aggregate"
        assert pair.deltas["a"] == pytest.approx(0.4)

    def test_no_cases_in_common_falls_back_to_the_aggregate(self):
        pair = PairAnalysis(
            pair_id="disjoint",
            model="m",
            before={"a": 0.5},
            after={"a": 0.9},
            before_per_case=[{CASE_INDEX_KEY: 0, "a": 0.5}],
            after_per_case=[{CASE_INDEX_KEY: 7, "a": 0.9}],
        )
        assert pair.n_paired == 0
        assert pair.deltas["a"] == pytest.approx(0.4)

    def test_a_metric_absent_from_the_paired_set_falls_back_on_its_own(self):
        """A metric the per-case rows never carried must not vanish from the report.

        Dropping it would silently shorten the metric table; pairing the rest
        is still right, so the fallback is per metric and says so.
        """
        pair = PairAnalysis(
            pair_id="mixed",
            model="m",
            before={"a": 0.5, "rare": 0.2},
            after={"a": 0.9, "rare": 0.6},
            before_per_case=[{CASE_INDEX_KEY: 0, "a": 0.5}],
            after_per_case=[{CASE_INDEX_KEY: 0, "a": 0.6}],
        )
        assert pair.deltas["a"] == pytest.approx(0.1), "paired"
        assert pair.deltas["rare"] == pytest.approx(0.4), "aggregate"
        assert pair.delta_detail["source"] == "mixed"
        assert pair.delta_detail["aggregate_metrics"] == ["rare"]

    def test_a_metric_scored_on_only_one_side_is_not_a_delta(self):
        """Treating an absent metric as 0.0 invents a full-scale movement.

        A phantom 0.9 from a one-sided metric on a *control* arm becomes the
        floor every other metric is judged against, marking real movement as
        noise. `floor_from_control_arm` already refuses this.
        """
        pair = PairAnalysis(
            pair_id="lopsided", model="m", before={"a": 0.5}, after={"a": 0.9, "ghost": 0.9}
        )
        assert "ghost" not in pair.deltas
        assert "ghost" not in pair.delta_detail["unpaired"]

    def test_delta_comparison_is_usable_without_a_pairanalysis(self):
        """The reporter holds plain dicts, not PairAnalysis objects."""
        got = delta_comparison({"a": 0.5}, {"a": 0.9}, [], [])
        assert got["deltas"]["a"] == pytest.approx(0.4)
        assert got["n_paired"] == 0


def _control_with_dropout() -> PairAnalysis:
    """A control whose unpaired floor is dominated by one case only scored after."""
    return PairAnalysis(
        pair_id="ctrl",
        model="m",
        before={"m": 0.50},
        after={"m": (0.51 + 0.51 + 0.98) / 3},
        before_per_case=[{CASE_INDEX_KEY: 0, "m": 0.50}, {CASE_INDEX_KEY: 1, "m": 0.50}],
        after_per_case=[
            {CASE_INDEX_KEY: 0, "m": 0.51},
            {CASE_INDEX_KEY: 1, "m": 0.51},
            {CASE_INDEX_KEY: 9, "m": 0.98},
        ],
        original_prompt="seed",
        optimized_prompt="seed",
    )


def _real_arm(delta: float) -> PairAnalysis:
    return PairAnalysis(
        pair_id="real",
        model="m",
        before={"m": 0.50},
        after={"m": 0.50 + delta},
        before_per_case=[{CASE_INDEX_KEY: 0, "m": 0.50}, {CASE_INDEX_KEY: 1, "m": 0.50}],
        after_per_case=[
            {CASE_INDEX_KEY: 0, "m": 0.50 + delta},
            {CASE_INDEX_KEY: 1, "m": 0.50 + delta},
        ],
        original_prompt="seed",
        optimized_prompt="a longer optimized prompt",
    )


class TestTheFloorPairsWithTheDeltas:
    """If only one of the two pairs, every verdict shifts silently."""

    def test_the_floor_is_measured_the_same_way_the_delta_is(self):
        """The apples-to-oranges guard, stated as a verdict flip.

        The control's unpaired floor is 0.167 (one high-scoring case appears
        only on the after side); paired it is 0.010. A real arm moving +0.050
        is `improved` against a paired floor and `within-noise` against the
        unpaired one, so leaving the floor unpaired while pairing the delta
        would reverse it.
        """
        ctrl, real = _control_with_dropout(), _real_arm(0.05)
        floors = measure_noise_floor_per_metric([ctrl, real])
        assert floors is not None
        assert floors["m"] == pytest.approx(0.01, abs=1e-9), "paired, not 0.167"
        assert classify_deltas(real, floors)["m"] == "improved"

        stale = noise_floor_comparison([ctrl, real])["unpaired"]
        assert stale["m"] == pytest.approx(0.1667, abs=1e-3)
        assert classify_deltas(real, stale)["m"] == "within-noise", (
            "the verdict this test exists to stop being produced by accident"
        )

    def test_both_floors_are_reported_side_by_side_with_n_paired(self):
        """Pairing moved a real arm's floor 44-64%; that disagreement is information."""
        got = noise_floor_comparison([_control_with_dropout(), _real_arm(0.05)])
        assert got["paired"]["m"] == pytest.approx(0.01, abs=1e-9)
        assert got["unpaired"]["m"] == pytest.approx(0.1667, abs=1e-3)
        assert got["floors"] == got["paired"], "the effective floor is the paired one"
        assert got["n_paired"] == {"ctrl": 2}
        assert got["controls"] == ["ctrl"]

    def test_no_control_arm_gives_none_not_an_empty_floor(self):
        """Absent is not zero -- a zero floor calls every movement real."""
        assert noise_floor_comparison([_real_arm(0.05)]) is None
        assert measure_noise_floor_per_metric([_real_arm(0.05)]) is None

    def test_arms_carrying_no_per_case_data_still_produce_a_floor(self):
        """Older runs must keep measuring their floor from the aggregates."""
        ctrl = PairAnalysis(
            pair_id="old-ctrl",
            model="m",
            before={"m": 0.50},
            after={"m": 0.54},
            original_prompt="seed",
            optimized_prompt="seed",
        )
        got = noise_floor_comparison([ctrl])
        assert got["floors"]["m"] == pytest.approx(0.04)
        assert got["paired"] == {}
        assert got["n_paired"] == {"old-ctrl": 0}

    def test_a_duck_typed_arm_with_only_deltas_still_works(self):
        """`_ArmView` in the reporter and the summarize_arm_metrics script pass
        objects that carry `.deltas` and `.is_control` and nothing else."""

        class Arm:
            def __init__(self):
                self.deltas = {"m": 0.04}
                self.is_control = True

        assert measure_noise_floor_per_metric([Arm()]) == {"m": pytest.approx(0.04)}
        assert noise_floor_comparison([Arm()])["unpaired"]["m"] == pytest.approx(0.04)

    def test_the_scalar_floor_pairs_too(self):
        """`measure_noise_floor` feeds the summary verdict in the same report."""
        from wrangler.reporting.analyzer import measure_noise_floor

        assert measure_noise_floor([_control_with_dropout()]) == pytest.approx(0.01, abs=1e-9)


class TestTheReportShowsBothNumbers:
    def _analysis(self) -> ExperimentAnalysis:
        return ExperimentAnalysis(
            experiment_name="campaign-09",
            pairs=[_c09_pair(a) for a in sorted(PUBLISHED_PAIRED)],
        )

    def test_the_per_metric_table_carries_paired_unpaired_and_n(self):
        """A reader must be able to see the two columns disagree.

        Replacing the published number in place, with nothing beside it, is a
        silent re-baseline of every campaign comparison crossing this date.
        """
        report = format_analysis_report(self._analysis())
        assert f"{PUBLISHED_PAIRED['c09-rationale-on']:+.4f}" in report
        assert f"{PUBLISHED_UNPAIRED['c09-rationale-on']:+.4f}" in report

    def test_the_calibration_section_shows_both_floors(self):
        report = format_analysis_report(self._analysis())
        head, _, _ = report.partition("## Summary")
        assert "paired" in head.lower()
        assert "unpaired" in head.lower()
        # The control's own paired safety movement is the floor safety is judged on.
        assert f"{PUBLISHED_PAIRED['c09-control']:.4f}" in head

    def test_a_report_without_per_case_data_still_renders(self):
        analysis = ExperimentAnalysis(
            experiment_name="old",
            pairs=[
                PairAnalysis(
                    pair_id="flash",
                    model="gemini-3.5-flash",
                    before={"safety_v1": 0.8},
                    after={"safety_v1": 0.9},
                    original_prompt="a",
                    optimized_prompt="b",
                )
            ],
        )
        report = format_analysis_report(analysis)
        assert "+0.1000" in report
