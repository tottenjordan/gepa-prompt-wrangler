"""The MDE arithmetic behind the held-out-partition decision gate.

`wrangler.reporting.inference` is the statistics `scripts/partition_mde.py`
used to own privately. That script's NO-GO on the 12-case `test` partition has
been published and acted on, so these tests exist to stop the arithmetic
drifting underneath a decision nobody will re-derive by hand.

The failures they prevent, concretely:

- the MDE formula quietly drifting from Miller (arXiv 2024-11-01) -- checked
  against a hand computation, not against itself;
- the sigma^2 decomposition ceasing to reproduce the variance it was derived
  from. It does today: at ``K = K_OBSERVED`` the bracket is IDENTICALLY the
  directly measured per-case variance for *any* sigma^2 estimate, which is what
  makes a verdict read there a measurement rather than a model.
  `TestTheVerdictIsSigma2Invariant` pins that, including against absurd inputs;
- `score_repeats`/`num_runs` appearing to buy resolution that the
  question-sampling term makes impossible, which would recommend budget that
  cannot work;
- an estimator degenerating to a constant, so it stops depending on the data it
  claims to measure.

The estimators are fed campaign 09's real per-case artifacts in
`tests/fixtures/c09/`, the same way `tests/test_noise_floor.py` pins the floor
to campaign 06's. Tests of the gate's CLI, exit codes and printed report live
in `tests/test_partition_mde.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wrangler.core.partitions import load_partitions
from wrangler.reporting.inference import (
    K_OBSERVED,
    EvalSide,
    _variance,
    common_cases,
    cross_window_measurement,
    minimum_detectable_effect,
    per_case_contrast,
    per_case_delta,
    required_cases,
    variance_components,
    z_multiplier,
)

FIXTURE = Path(__file__).parent / "fixtures" / "c09"

CONTROL = "c09-control"
ON = "c09-rationale-on"
OFF = "c09-rationale-off"

#: The two effects this project needs to detect, from
#: docs/analysis/2026-09-22-campaign-09-reanalysis.md: the rationale on/off
#: contrast and the optimized-arm-vs-control DiD on `safety_v1`. The gate's own
#: copies live in `scripts/partition_mde.py`, which owns the verdict; these are
#: here so the arithmetic tests target realistic effect sizes without importing
#: the script back into the layer that was extracted from it.
EFFECT_CONTRAST = 0.075
EFFECT_DID = 0.0952


def _load_c09_sides() -> dict[tuple[str, str], EvalSide]:
    """Campaign 09's six committed eval sides, as `EvalSide` objects.

    Deliberately not `scripts.partition_mde.load_sides`: this file tests the
    arithmetic layer, and reaching back into the script would rebuild the
    dependency the layer was just extracted from.
    """
    sides: dict[tuple[str, str], EvalSide] = {}
    for phase in ("eval_before", "eval_after"):
        for arm in (CONTROL, ON, OFF):
            payload = json.loads((FIXTURE / phase / f"{arm}.json").read_text())
            sides[(phase, arm)] = EvalSide(
                arm=arm,
                phase=phase,
                per_case={int(row["case_index"]): row for row in payload["per_case"]},
                scores_std=payload.get("scores_std") or {},
                num_runs=int(payload.get("num_runs") or 1),
                score_repeats=int(payload.get("score_repeats") or 1),
            )
    return sides


@pytest.fixture
def sides() -> dict[tuple[str, str], EvalSide]:
    return _load_c09_sides()


def _side(per_case: list[dict], std: float = 0.0) -> EvalSide:
    return EvalSide(
        arm="a",
        phase="eval_before",
        per_case={row["case_index"]: row for row in per_case},
        scores_std={"m": std},
        num_runs=2,
        score_repeats=2,
    )


class TestTheFormula:
    """Miller's MDE, checked against arithmetic done by hand."""

    def test_matches_a_hand_computed_value(self):
        """Guards the formula itself against a silent algebra change.

        omega2=0.01, sigma2_a=sigma2_b=0.02, n=25, K_A=K_B=2:
            bracket = 0.01 + 0.02/2 + 0.02/2 = 0.03
            0.03 / 25 = 0.0012 ; sqrt = 0.0346410161514
            x (1.959963985 + 0.841621234 = 2.801585219) = 0.0970497588
        """
        got = minimum_detectable_effect(
            omega2=0.01, sigma2_a=0.02, sigma2_b=0.02, n=25, k_a=2, k_b=2
        )
        assert got == pytest.approx(0.0970497588, abs=1e-9)

    def test_z_multiplier_is_the_two_sided_95_percent_80_power_constant(self):
        """A one-sided z, or 90% power, would shrink the MDE by ~15-30%.

        That is the difference between a go and a no-go at the margin, so the
        constant is pinned rather than left to a default somewhere.
        """
        assert z_multiplier() == pytest.approx(1.959963985 + 0.841621234, abs=1e-6)

    def test_mde_shrinks_as_cases_are_added(self):
        """The whole question the gate asks is 'would more cases help?'."""
        args = {"omega2": 0.05, "sigma2_a": 0.01, "sigma2_b": 0.01, "k_a": 2, "k_b": 2}
        curve = [minimum_detectable_effect(n=n, **args) for n in (12, 16, 20, 24)]
        assert curve == sorted(curve, reverse=True)

    def test_mde_shrinks_as_repeats_are_added(self):
        """If K had no effect the per-question term would be mis-wired."""
        args = {"omega2": 0.05, "sigma2_a": 0.02, "sigma2_b": 0.02, "n": 12}
        assert minimum_detectable_effect(k_a=2, k_b=2, **args) < minimum_detectable_effect(
            k_a=1, k_b=1, **args
        )

    def test_repeats_can_never_cross_the_question_sampling_floor(self):
        """The finding the gate turns on: budget cannot buy past omega2.

        DOE 03 measured `score_repeats` buying essentially nothing for
        `safety_v1` (exponent 0.02). Miller's formula is why -- K divides
        sigma2 and leaves omega2 alone. A formula that let a large K drive the
        MDE toward zero would recommend spending money that cannot work.
        """
        floor = minimum_detectable_effect(omega2=0.05, sigma2_a=0.0, sigma2_b=0.0, n=12)
        huge = minimum_detectable_effect(
            omega2=0.05, sigma2_a=0.02, sigma2_b=0.02, n=12, k_a=10_000, k_b=10_000
        )
        assert huge > floor
        assert huge == pytest.approx(floor, abs=1e-4)

    def test_required_cases_inverts_the_formula(self):
        """`n` reported as 'what it would take' must actually get there."""
        args = {"omega2": 0.05, "sigma2_a": 0.01, "sigma2_b": 0.01, "k_a": 2, "k_b": 2}
        n = required_cases(target=EFFECT_CONTRAST, **args)
        assert minimum_detectable_effect(n=n, **args) <= EFFECT_CONTRAST
        assert minimum_detectable_effect(n=n - 1, **args) > EFFECT_CONTRAST

    def test_zero_cases_is_rejected_rather_than_dividing_by_zero(self):
        """A ZeroDivisionError from inside a gate reads as a crash, not a verdict."""
        with pytest.raises(ValueError, match="n"):
            minimum_detectable_effect(omega2=0.05, sigma2_a=0.0, sigma2_b=0.0, n=0)


class TestTheEstimatorsReadTheData:
    """Every component must move when the measurements move."""

    def test_within_side_variance_tracks_the_recorded_spread(self):
        """Pins sigma2 to `scores_std`, not to a constant.

        `scores_std` is the across-`num_runs` spread of the ARM MEAN, so the
        per-question variance behind it is n * std**2. Dropping the n turns a
        per-question variance into a standard error of the mean and understates
        sigma2 by ~63x here.
        """
        rows = [{"case_index": i, "m": 0.5} for i in range(9)]
        assert _side(rows, std=0.1).within_side_variance("m") == pytest.approx(9 * 0.01)
        assert _side(rows, std=0.2).within_side_variance("m") == pytest.approx(9 * 0.04)

    def test_contrast_variance_moves_when_one_case_score_moves(self, sides):
        """Catches an estimator that has degenerated to a constant."""
        cases = common_cases(sides)
        base = per_case_contrast(sides, ON, OFF, "safety_v1", cases)

        nudged = dict(sides)
        arm_side = sides[("eval_after", ON)]
        per_case = {i: dict(row) for i, row in arm_side.per_case.items()}
        per_case[cases[0]]["safety_v1"] += 0.25
        nudged[("eval_after", ON)] = EvalSide(
            arm=arm_side.arm,
            phase=arm_side.phase,
            per_case=per_case,
            scores_std=arm_side.scores_std,
            num_runs=arm_side.num_runs,
            score_repeats=arm_side.score_repeats,
        )
        moved = per_case_contrast(nudged, ON, OFF, "safety_v1", cases)

        assert moved[0] == pytest.approx(base[0] + 0.25)
        assert moved[1:] == pytest.approx(base[1:])

    def test_a_control_whose_sides_are_identical_has_no_measurement_variance(self):
        """The load-bearing property of the control-arm sigma2 estimator.

        The control's prompt is byte-identical across both sides, so its true
        per-question effect is zero for every case and ALL of its per-case
        delta variance is measurement. If an identical-sided control produced a
        non-zero estimate, the estimator would be reading question sampling
        into sigma2 and crediting `num_runs` with reducing it.

        The assertions go through `cross_window_measurement` itself, and both
        directions are checked: an earlier version of this test only
        differenced two identical dicts, which tests subtraction and can never
        fail.
        """
        cases = list(range(8))
        rows = [{"case_index": i, "m": i / 10} for i in cases]
        twin = {("eval_before", CONTROL): _side(rows), ("eval_after", CONTROL): _side(rows)}

        assert per_case_delta(twin, CONTROL, "m", cases) == pytest.approx([0.0] * 8)
        assert cross_window_measurement(twin, "m", cases, n_sides=4, control_arm=CONTROL) == 0.0

        # ... and it must not be a constant zero: move one case and the
        # estimator must report exactly n_sides/2 pairs' worth of that spread.
        moved = [dict(row) for row in rows]
        moved[0]["m"] += 0.4
        drifted = {("eval_before", CONTROL): _side(rows), ("eval_after", CONTROL): _side(moved)}
        expected = 2 * _variance([0.4, *[0.0] * 7])
        got = cross_window_measurement(drifted, "m", cases, n_sides=4, control_arm=CONTROL)
        assert got == pytest.approx(expected)
        assert expected > 0

    def test_the_control_arm_is_named_by_the_caller_not_assumed(self):
        """Pointing the estimator at a treatment arm must read *that* arm.

        The arithmetic layer serves any campaign, so the control's name is an
        argument rather than a constant. If it silently ignored the name and
        read some default arm, a caller measuring a differently-named campaign
        would get another arm's drift reported as its measurement variance --
        a wrong sigma^2 with no error anywhere.
        """
        flat = [{"case_index": i, "m": 0.5} for i in range(8)]
        spread = [{"case_index": i, "m": 0.5 + (i % 2) * 0.4} for i in range(8)]
        sides = {
            ("eval_before", CONTROL): _side(flat),
            ("eval_after", CONTROL): _side(flat),
            ("eval_before", ON): _side(flat),
            ("eval_after", ON): _side(spread),
        }
        cases = list(range(8))

        assert cross_window_measurement(sides, "m", cases, n_sides=2, control_arm=CONTROL) == 0.0
        assert cross_window_measurement(sides, "m", cases, n_sides=2, control_arm=ON) > 0.0

    def test_omega2_is_clamped_at_zero_and_says_so(self):
        """Measurement can exceed the observed spread; a negative omega2 is not a result.

        An unclamped negative omega2 makes sqrt() raise or, worse, makes the
        bracket shrink and the MDE look better than measured. Clamping silently
        would hide that the decomposition failed, so the flag is part of the
        return value and the printed table.
        """
        comp = variance_components(total=0.01, measurement_at_k_observed=0.05, k_observed=2)
        assert comp.omega2 == 0.0
        assert comp.clamped is True
        assert comp.bracket_at(K_OBSERVED) == pytest.approx(0.01)


class TestAgainstCampaign09:
    """The estimators, run over the six real eval sides."""

    def test_common_cases_intersects_rather_than_unions(self):
        """A case missing from ONE side must drop out of every contrast.

        This is silent-failures #5: the 2026-08-22 control arm's apparent
        +0.180 on an unchanged prompt was case-subset mismatch, not signal. A
        union here -- or an intersection over only the first two sides -- would
        hand `per_case_delta` a case index one side never scored, which either
        raises a bare KeyError mid-report or, if some caller ever tolerated it,
        differences means over different case sets again. The real fixtures'
        63-of-64 pairing is asserted in `tests/test_partition_mde.py`.
        """
        full = [{"case_index": i, "m": 0.5} for i in range(6)]
        sides = {
            ("eval_before", CONTROL): _side(full),
            ("eval_after", CONTROL): _side([r for r in full if r["case_index"] != 4]),
            ("eval_before", ON): _side(full),
            ("eval_after", ON): _side([r for r in full if r["case_index"] != 1]),
        }
        assert common_cases(sides) == [0, 2, 3, 5]

    def test_twelve_cases_cannot_resolve_either_target_effect(self, sides):
        """THE DECISION, in arithmetic. Pinned so it cannot soften unnoticed.

        Both target effects come from the campaign 09 reanalysis: +0.0952 for
        the DiD-vs-control optimization effect, +-0.075 for the rationale
        on/off contrast. Measured on the twelve held-out cases, the MDE is
        roughly 2-3x either target, and it stays above both even at K=2. The
        printed verdict that rests on this is pinned separately in
        `tests/test_partition_mde.py`.
        """
        test = load_partitions()["test"]
        for metric in ("safety_v1", "instruction_following_v1"):
            values = per_case_contrast(sides, ON, OFF, metric, test)
            total = _variance(values)
            comp = variance_components(
                total=total,
                measurement_at_k_observed=0.0,
                k_observed=K_OBSERVED,
            )
            mde = minimum_detectable_effect(
                omega2=comp.omega2,
                sigma2_a=comp.sigma2_combined / 2,
                sigma2_b=comp.sigma2_combined / 2,
                n=len(test),
                k_a=K_OBSERVED,
                k_b=K_OBSERVED,
            )
            assert mde > EFFECT_CONTRAST, metric
            assert mde > EFFECT_DID, metric

    def test_more_cases_are_needed_than_the_whole_eval_set_holds(self, sides):
        """Says how far short 12 is, and pins that 64 is also short.

        The actionable half of a no-go. If this ever passes at n<=64 the gate's
        recommendation ("a bigger test partition cannot be carved out of these
        64 cases") is wrong and must be rewritten.
        """
        cases = common_cases(sides)
        values = per_case_contrast(sides, ON, OFF, "instruction_following_v1", cases)
        comp = variance_components(
            total=_variance(values), measurement_at_k_observed=0.0, k_observed=K_OBSERVED
        )
        n = required_cases(
            omega2=comp.omega2,
            sigma2_a=comp.sigma2_combined / 2,
            sigma2_b=comp.sigma2_combined / 2,
            target=EFFECT_CONTRAST,
            k_a=K_OBSERVED,
            k_b=K_OBSERVED,
        )
        assert n > 64


class TestTheVerdictIsSigma2Invariant:
    """The strongest property this arithmetic has: a verdict is a DIRECT measurement.

    The two sigma^2 estimators disagree by ~2x and drive omega^2 negative on
    two metrics, so if the headline MDEs depended on that split the no-go would
    be arguable. They do not: the split is defined as omega^2 = max(0, total-m)
    and sigma^2_combined = min(m, total) * K_OBSERVED, so the bracket at
    K = K_OBSERVED collapses back to `total` for every m >= 0.
    """

    def test_the_bracket_reproduces_the_measured_total_at_the_measured_k(self):
        """The decomposition may re-apportion variance but never invent it.

        Campaign 09 ran `num_runs: 2`; at K=2 the bracket must equal the
        directly measured per-case variance, whichever sigma2 estimator was
        used. Otherwise the K=1/K=2 columns and the measured column disagree
        and neither can be trusted.
        """
        comp = variance_components(total=0.08, measurement_at_k_observed=0.03, k_observed=2)
        assert comp.bracket_at(2) == pytest.approx(0.08)
        assert comp.bracket_at(1) == pytest.approx(0.08 + 0.03)
        assert comp.bracket_at(4) == pytest.approx(0.08 - 0.015)

    def test_the_identity_holds_for_any_measurement_estimate(self):
        """Including absurd ones, which is what makes the invariance structural.

        A future estimator that returned zero, or a hundred times the total,
        would still have to reproduce the measured variance at K_OBSERVED. A
        decomposition that only happened to agree on campaign 09's numbers
        would not be an identity, and the guarantee above would be luck.
        """
        for measurement in (0.0, 1e-9, 0.079, 0.08, 0.081, 8.0, 1e6):
            comp = variance_components(
                total=0.08, measurement_at_k_observed=measurement, k_observed=K_OBSERVED
            )
            assert comp.bracket_at(K_OBSERVED) == pytest.approx(0.08, abs=1e-15)
            assert comp.omega2 >= 0.0
