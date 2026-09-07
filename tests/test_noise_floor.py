"""The noise floor, computed by a function instead of a throwaway script.

On 2026-09-02 the first uncontaminated floor this project has ever measured was
worked out with an ad-hoc script pasted into a shell. That is how a number ends
up in CLAUDE.md that nobody can reproduce -- and CLAUDE.md currently carries two
such numbers, both measured through dropout that no longer exists.

So the arithmetic lives here, pinned to a committed fixture of that arm's real
`eval_before` / `eval_after` artifacts. Real data rather than synthetic, so the
function cannot drift from what the pipeline actually emits, and the expected
values come from the hand computation rather than from the function itself --
otherwise the test only proves the code agrees with the code.

The arm was a control: prompt byte-identical on both sides, `skip_optimize`
on, so every delta it produced is noise by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wrangler.reporting.analyzer import floor_from_control_arm

FIXTURE = Path(__file__).parent / "fixtures" / "c06_validation"


def _fixture() -> tuple[dict, dict]:
    before = json.loads((FIXTURE / "eval_before.json").read_text())
    after = json.loads((FIXTURE / "eval_after.json").read_text())
    return before, after


class TestAgainstTheHandComputation:
    """Expected values transcribed from the 2026-09-02 manual analysis."""

    def test_coverage_is_complete_on_both_sides(self):
        """64/64 both sides -- the first floor here not confounded by dropout.

        Every earlier measurement differenced eval runs that scored different
        subsets of cases, so the delta was partly dropout. EVAL_MAX_RETRIES=16
        is what closed that.
        """
        floor = floor_from_control_arm(*_fixture())
        assert floor["coverage"]["before"] == pytest.approx(1.0)
        assert floor["coverage"]["after"] == pytest.approx(1.0)
        assert floor["cases"]["before"] == 64
        assert floor["cases"]["after"] == 64

    def test_unpaired_deltas_match(self):
        floor = floor_from_control_arm(*_fixture())
        u = floor["unpaired"]
        assert u["hallucination_v1"] == pytest.approx(-0.0747, abs=5e-4)
        assert u["final_response_quality_v1"] == pytest.approx(-0.0554, abs=5e-4)
        assert u["safety_v1"] == pytest.approx(-0.0104, abs=5e-4)
        assert u["tool_use_quality_v1"] == pytest.approx(-0.0050, abs=5e-4)
        assert u["instruction_following_v1"] == pytest.approx(-0.0028, abs=5e-4)

    def test_paired_deltas_match(self):
        floor = floor_from_control_arm(*_fixture())
        p = floor["paired"]
        assert p["hallucination_v1"] == pytest.approx(-0.0420, abs=5e-4)
        assert p["final_response_quality_v1"] == pytest.approx(-0.0197, abs=5e-4)

    def test_the_scalar_floor_is_the_largest_unpaired_movement(self):
        floor = floor_from_control_arm(*_fixture())
        assert floor["floor"] == pytest.approx(0.0747, abs=5e-4)

    def test_pairing_reduces_the_floor_substantially(self):
        """CLAUDE.md claims pairing helps by ~15%. This arm says 44-64%.

        Not asserted as an exact figure -- one arm cannot establish it, and
        campaign 06's four arms are what decides whether the documented number
        gets corrected. Asserted as a direction, so a regression that silently
        stopped pairing would fail here.
        """
        floor = floor_from_control_arm(*_fixture())
        for metric in ("hallucination_v1", "final_response_quality_v1"):
            assert abs(floor["paired"][metric]) < abs(floor["unpaired"][metric])

    def test_every_delta_is_negative(self):
        """The drift signal, pinned so it cannot vanish unnoticed.

        Five of five negative on an unchanged prompt is what gates campaign 07.
        If a future change to the arithmetic makes this scatter, that is a
        finding about the arithmetic and should surface here rather than in a
        campaign write-up.
        """
        floor = floor_from_control_arm(*_fixture())
        assert all(v < 0 for v in floor["unpaired"].values())


class TestEdgeCases:
    def test_a_metric_missing_from_one_side_is_skipped_not_zeroed(self):
        """Treating an absent metric as 0.0 invents a full-scale delta."""
        before = {"scores": {"a": 0.8, "b": 0.5}, "per_case": [], "cases_total": 0}
        after = {"scores": {"a": 0.7}, "per_case": [], "cases_total": 0}
        floor = floor_from_control_arm(before, after)
        assert "b" not in floor["unpaired"]
        assert floor["unpaired"]["a"] == pytest.approx(-0.1)

    def test_a_case_scored_by_only_one_side_is_excluded_from_pairing(self):
        before = {
            "scores": {"m": 0.5},
            "per_case": [{"case_index": 0, "m": 0.4}, {"case_index": 1, "m": 0.6}],
            "cases_total": 2,
        }
        after = {
            "scores": {"m": 0.7},
            "per_case": [{"case_index": 0, "m": 0.8}],
            "cases_total": 2,
        }
        floor = floor_from_control_arm(before, after)
        assert floor["n_paired"] == 1
        assert floor["paired"]["m"] == pytest.approx(0.4)

    def test_empty_per_case_does_not_divide_by_zero(self):
        before = {"scores": {"m": 0.5}, "per_case": [], "cases_total": 0}
        after = {"scores": {"m": 0.6}, "per_case": [], "cases_total": 0}
        floor = floor_from_control_arm(before, after)
        assert floor["paired"] == {}
        assert floor["n_paired"] == 0
        assert floor["unpaired"]["m"] == pytest.approx(0.1)

    def test_no_scores_at_all_yields_no_floor_rather_than_zero(self):
        """A zero floor asserts there is no noise, which is never observed."""
        floor = floor_from_control_arm(
            {"scores": {}, "per_case": [], "cases_total": 0},
            {"scores": {}, "per_case": [], "cases_total": 0},
        )
        assert floor["floor"] is None

    def test_coverage_is_none_when_the_total_is_unknown(self):
        """Older artifacts predate the cases_total field; do not fake it as 1.0."""
        before = {"scores": {"m": 0.5}, "per_case": [{"case_index": 0, "m": 0.5}]}
        after = {"scores": {"m": 0.5}, "per_case": [{"case_index": 0, "m": 0.5}]}
        floor = floor_from_control_arm(before, after)
        assert floor["coverage"]["before"] is None


class TestPerMetricFloorsAlongsideTheScalar:
    """Campaign 06 measures a floor per metric; the old API returns one number.

    `measure_noise_floor` collapses every control metric to a single
    `max(|delta|)`. That is deliberately conservative and five existing callers
    depend on it, so it keeps working unchanged. But on the 2026-09-02 arm the
    per-metric spread was 0.0028 to 0.0747 -- a 27x range -- so judging
    instruction_following against hallucination's floor throws away most of the
    resolution the campaign is paying for.
    """

    class _Pair:
        def __init__(self, deltas, is_control=True):
            self.deltas = deltas
            self.is_control = is_control

    def test_the_scalar_api_is_unchanged(self):
        from wrangler.reporting.analyzer import measure_noise_floor

        ctrl = self._Pair({"a": 0.039, "b": -0.012})
        assert measure_noise_floor([ctrl]) == pytest.approx(0.039)

    def test_no_control_arm_still_returns_none_not_zero(self):
        from wrangler.reporting.analyzer import measure_noise_floor, measure_noise_floor_per_metric

        real = self._Pair({"a": 0.5}, is_control=False)
        assert measure_noise_floor([real]) is None
        assert measure_noise_floor_per_metric([real]) is None

    def test_per_metric_keeps_each_metric_separate(self):
        from wrangler.reporting.analyzer import measure_noise_floor_per_metric

        ctrl = self._Pair({"a": 0.039, "b": -0.012})
        floors = measure_noise_floor_per_metric([ctrl])
        assert floors == {"a": pytest.approx(0.039), "b": pytest.approx(0.012)}

    def test_several_control_arms_take_the_worst_per_metric(self):
        """Two control arms disagreeing is information, not something to average."""
        from wrangler.reporting.analyzer import measure_noise_floor_per_metric

        floors = measure_noise_floor_per_metric(
            [self._Pair({"a": 0.02, "b": 0.09}), self._Pair({"a": 0.05, "b": 0.01})]
        )
        assert floors == {"a": pytest.approx(0.05), "b": pytest.approx(0.09)}

    def test_a_metric_only_one_arm_measured_still_appears(self):
        from wrangler.reporting.analyzer import measure_noise_floor_per_metric

        floors = measure_noise_floor_per_metric(
            [self._Pair({"a": 0.02}), self._Pair({"a": 0.01, "rare": 0.30})]
        )
        assert floors["rare"] == pytest.approx(0.30)


class TestDriftSignSummary:
    """Pre-registered on 2026-09-07, before Campaign 06's arms reported.

    The validation arm moved all five metrics negative on a byte-identical
    prompt. Pure noise should scatter in sign, so this counts signs per arm and
    reports whether the arms agree.

    The counting rule matters more than the arithmetic. The five metrics are
    NOT independent -- all five score the same 64 responses via the same
    autorater in one call -- so treating 20 metric-arm cells as 20 draws would
    badly overstate significance. The ARMS are the independent units, so this
    summarises per arm first and only then across arms.

    See docs/analysis/2026-09-02-eval-order-drift.md for the decision rule.
    """

    def test_an_all_negative_arm_is_reported_as_negative(self):
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary({"claude-n1": {"a": -0.07, "b": -0.05, "c": -0.01}})
        assert s["per_arm"]["claude-n1"]["negative"] == 3
        assert s["per_arm"]["claude-n1"]["direction"] == "negative"

    def test_a_mixed_arm_takes_its_majority(self):
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary({"x": {"a": -0.07, "b": -0.05, "c": +0.01}})
        assert s["per_arm"]["x"]["direction"] == "negative"
        assert s["per_arm"]["x"]["positive"] == 1

    def test_an_evenly_split_arm_has_no_direction(self):
        """A tie is not weak evidence for either side; it is no evidence."""
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary({"x": {"a": -0.05, "b": +0.05}})
        assert s["per_arm"]["x"]["direction"] is None

    def test_unanimous_arms_are_flagged_as_consistent(self):
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary(
            {
                "a1": {"m": -0.07, "n": -0.02},
                "a2": {"m": -0.03, "n": -0.01},
                "a3": {"m": -0.05, "n": -0.04},
                "a4": {"m": -0.02, "n": -0.06},
            }
        )
        assert s["arms_negative"] == 4
        assert s["arms_total"] == 4
        assert s["consistent"] is True

    def test_scattered_arms_are_not_consistent(self):
        """The pre-registered null: the validation arm was a coincidence."""
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary(
            {
                "a1": {"m": -0.07},
                "a2": {"m": +0.03},
                "a3": {"m": -0.05},
                "a4": {"m": +0.02},
            }
        )
        assert s["consistent"] is False
        assert s["arms_negative"] == 2

    def test_it_reports_arms_not_cells_as_the_unit(self):
        """Guards the statistical point the pre-registration turns on.

        If this ever returned a flat count of metric-arm cells, someone would
        compute a binomial p over it and claim significance the design does not
        support.
        """
        from wrangler.reporting.analyzer import drift_sign_summary

        s = drift_sign_summary({"a1": {"m": -0.1, "n": -0.1, "o": -0.1}})
        assert s["arms_total"] == 1, "the unit of analysis is the arm, not the metric"

    def test_the_validation_arm_from_the_fixture_is_all_negative(self):
        """End-to-end against real data: floor function feeds the sign test."""
        from wrangler.reporting.analyzer import drift_sign_summary

        floor = floor_from_control_arm(*_fixture())
        s = drift_sign_summary({"c06-ctrl-claude-n1": floor["unpaired"]})
        assert s["per_arm"]["c06-ctrl-claude-n1"]["negative"] == 5
        assert s["consistent"] is True
