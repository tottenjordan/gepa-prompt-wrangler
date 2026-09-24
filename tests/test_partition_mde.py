"""The held-out test partition's minimum detectable effect, pinned.

`scripts/partition_mde.py` is a DECISION GATE: it says whether the checked-in
12-case `test` partition can resolve the two effect sizes this project needs to
detect (+0.0952 and +-0.075, both from
`docs/analysis/2026-09-22-campaign-09-reanalysis.md`). A gate that answers from
assumed variance is worse than no gate, because it produces a plausible number
with nothing behind it -- so every estimator here is fed the real campaign 09
per-case artifacts in `tests/fixtures/c09/`, the same way
`tests/test_noise_floor.py` pins the floor to campaign 06's.

The failures these tests exist to prevent, concretely:

- the MDE formula quietly drifting from Miller (arXiv 2024-11-01) -- checked
  against a hand computation, not against itself;
- the verdict starting to depend on the sigma^2 decomposition. It does not
  today: it is read at ``K = K_OBSERVED``, where the bracket is IDENTICALLY the
  directly measured per-case variance for any sigma^2 estimate. That identity
  is what makes the no-go a measurement rather than a model, and
  `TestTheVerdictIsSigma2Invariant` pins it;
- `score_repeats`/`num_runs` appearing to buy resolution that the
  question-sampling term makes impossible -- which misdirects the *secondary*
  "spend more budget" answer, and cannot move the verdict itself;
- an estimator degenerating to a constant, so the gate stops depending on the
  data it claims to measure;
- a missing artifact exiting like a NO-GO, so a caller acts on a verdict that
  was never computed;
- the 12-case verdict silently flipping if the partition, the fixtures, or the
  arithmetic move.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.partition_mde import (
    EFFECT_CONTRAST,
    EFFECT_DID,
    EXIT_NO_GO,
    EXIT_UNMEASURABLE,
    K_OBSERVED,
    METRICS,
    SCENARIOS,
    EvalSide,
    Unmeasurable,
    _variance,
    build_report,
    common_cases,
    cross_window_measurement,
    doe03_agreement,
    load_sides,
    minimum_detectable_effect,
    per_case_contrast,
    per_case_delta,
    reducible_share,
    required_cases,
    variance_components,
    z_multiplier,
)
from wrangler.core.partitions import load_partitions

FIXTURE = Path(__file__).parent / "fixtures" / "c09"

CONTROL = "c09-control"
ON = "c09-rationale-on"
OFF = "c09-rationale-off"


@pytest.fixture
def sides() -> dict[tuple[str, str], EvalSide]:
    return load_sides(FIXTURE)


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
        assert cross_window_measurement(twin, "m", cases, n_sides=4) == 0.0

        # ... and it must not be a constant zero: move one case and the
        # estimator must report exactly n_sides/2 pairs' worth of that spread.
        moved = [dict(row) for row in rows]
        moved[0]["m"] += 0.4
        drifted = {("eval_before", CONTROL): _side(rows), ("eval_after", CONTROL): _side(moved)}
        expected = 2 * _variance([0.4, *[0.0] * 7])
        assert cross_window_measurement(drifted, "m", cases, n_sides=4) == pytest.approx(expected)
        assert expected > 0

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


class TestAgainstCampaign09:
    """Pinned to the six real eval sides, and to the published reanalysis."""

    def test_only_cases_present_on_every_side_are_used(self, sides):
        """Two of six sides scored 63/64; unpaired means mix different subsets.

        That is silent-failures #5 -- the 2026-08-22 control arm's apparent
        +0.180 on an unchanged prompt was case-subset mismatch, not signal.
        """
        assert len(sides) == 6
        assert len(common_cases(sides)) == 63

    def test_reproduces_the_published_per_case_contrast_means(self, sides):
        """If these drift, the variance behind the gate is not campaign 09's.

        Values transcribed from docs/analysis/2026-09-22-campaign-09-reanalysis.md.
        """
        cases = common_cases(sides)

        def mean(values: list[float]) -> float:
            return sum(values) / len(values)

        on_off = {
            "safety_v1": 0.0000,
            "instruction_following_v1": 0.0768,
            "final_response_quality_v1": -0.0752,
            "hallucination_v1": -0.0499,
            "tool_use_quality_v1": -0.0450,
        }
        for metric, expected in on_off.items():
            got = mean(per_case_contrast(sides, ON, OFF, metric, cases))
            assert got == pytest.approx(expected, abs=5e-4), metric

        for arm in (ON, OFF):
            did = mean(per_case_contrast(sides, arm, CONTROL, "safety_v1", cases))
            assert did == pytest.approx(EFFECT_DID, abs=5e-4), arm

    def test_the_test_partition_is_the_twelve_checked_in_cases(self, sides):
        """The gate must judge the real partition, not a generic n=12.

        A generic n=12 answers a different question -- these twelve specific
        cases have their own variance, and stratification does not make them
        interchangeable with any other twelve.
        """
        test = load_partitions()["test"]
        assert len(test) == 12
        assert set(test) <= set(common_cases(sides))

    def test_twelve_cases_cannot_resolve_either_target_effect(self, sides):
        """THE DECISION. Pinned so it cannot be softened without a red test.

        Both target effects come from the campaign 09 reanalysis: +0.0952 for
        the DiD-vs-control optimization effect, +-0.075 for the rationale
        on/off contrast. Measured on the twelve held-out cases, the MDE is
        roughly 2-3x either target, and it stays above both even at K=2.
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
    """The strongest property this gate has: its verdict is a DIRECT measurement.

    The two sigma^2 estimators disagree by ~2x and drive omega^2 negative on
    two metrics, so if the headline MDEs depended on that split the no-go would
    be arguable. They do not: the split is defined as omega^2 = max(0, total-m)
    and sigma^2_combined = min(m, total) * K_OBSERVED, so the bracket at
    K = K_OBSERVED collapses back to `total` for every m >= 0.
    """

    def test_the_two_sigma2_estimators_give_the_same_held_out_verdict(self, sides):
        """No sigma^2 estimate -- inverted, mis-scaled or absent -- can flip the gate.

        If this fails, the decomposition has stopped reproducing the measured
        total and the printed verdict has quietly become a modelled number
        whose value depends on which of two ~2x-apart estimators was picked.
        """
        report = build_report(sides)
        n = len(report["test_cases"])
        for contrast in ("DiD vs control (on)", "rationale on - off"):
            for metric in METRICS:
                comps = [
                    report["test_components"][(contrast, metric, scenario)]
                    for scenario in SCENARIOS
                ]
                for comp in comps:
                    assert comp.bracket_at(K_OBSERVED) == pytest.approx(comp.total, abs=1e-15)
                mdes = [comp.mde(n, K_OBSERVED) for comp in comps]
                assert mdes[0] == pytest.approx(mdes[1], abs=1e-12), (contrast, metric)

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


class TestAgainstDOE03:
    """A weak directional cross-check on the BUDGET columns, pinned as a regression."""

    def test_the_k_reducible_share_ranks_metrics_the_way_doe_03_did(self, sides):
        """A REGRESSION PIN against the frozen fixtures, NOT evidence.

        What it catches: a sigma^2 estimator that has changed shape against
        known inputs and started reading question sampling as measurement,
        which would misdirect the "would more `num_runs` help?" columns.

        What it does NOT do, despite how it reads:

        - It cannot guard the verdict. That is taken at K = K_OBSERVED, where
          the bracket equals the measured total for any sigma^2 whatsoever --
          see `TestTheVerdictIsSigma2Invariant`. No inversion of this estimator
          can turn the no-go into a go.
        - It is not statistically meaningful on its own. r = +0.81 across five
          metrics is t = 2.41 on 3 df, two-sided p ~ 0.095. A
          leave-one-eval-side-out jackknife holds near +0.8 on five of six
          replicates and collapses to +0.17 on the sixth
          (`eval_after/c09-rationale-on`, which alone carries 75% of
          `safety_v1`'s within-window sigma^2 pool through one 1-df standard
          deviation).

        The 0.7 threshold is therefore a tripwire on fixed data, not a claim
        that the decomposition has been independently confirmed.
        """
        report = build_report(sides)
        assert doe03_agreement(report) > 0.7

        shares = {m: reducible_share(report, m) for m in METRICS}
        assert max(shares, key=shares.__getitem__) == "safety_v1"
        assert shares["instruction_following_v1"] == min(shares.values())

    def test_instruction_following_is_dominated_by_question_sampling(self, sides):
        """The holdout cannot be bought out of, and the report must show that.

        DOE 03: `instruction_following_v1` has the weakest response to both
        knobs (`num_runs` 0.20, `score_repeats` -0.06). Under Miller that means
        omega^2 carries most of its variance, so its MDE at K -> infinity must
        still be far above the +-0.075 target at n=12. A decomposition that
        made this metric look K-solvable would be wrong.
        """
        report = build_report(sides)
        comp = report["components"][
            ("rationale on - off", "instruction_following_v1", "within-window")
        ]
        assert comp.omega2 / comp.total > 0.8
        irreducible = minimum_detectable_effect(
            omega2=comp.omega2, sigma2_a=0.0, sigma2_b=0.0, n=12
        )
        assert irreducible > EFFECT_CONTRAST


class TestTheReportIsHonest:
    """The gate's output has to carry its own caveats."""

    def test_running_the_script_reports_no_go_and_warns_about_small_n(self, capsys):
        """Bowyer et al. (ICML 2025): CLT intervals under-cover below a few hundred.

        At N=100 a nominal-95% interval achieved 92.5%, so the n=12 MDE is
        itself optimistic and must not be printed as if it were reliable. A
        report that omitted that would be read as "12 is nearly enough".
        """
        from scripts.partition_mde import main

        assert main(["--stages-dir", str(FIXTURE)]) == EXIT_NO_GO
        out = capsys.readouterr().out
        assert "NO-GO" in out
        assert "Bowyer" in out

    def test_it_runs_with_no_arguments_at_all(self, capsys):
        """The first documented invocation has to work, or the docs are decoration.

        It defaulted to /tmp/c09, so `uv run python scripts/partition_mde.py`
        exited with the missing-artifact message on a clean checkout while the
        real artifacts sat committed in tests/fixtures/c09.
        """
        from scripts.partition_mde import main

        assert main([]) == EXIT_NO_GO
        assert "NO-GO" in capsys.readouterr().out

    def test_unmeasurable_and_no_go_have_different_exit_codes(self, tmp_path, capsys):
        """A caller must be able to tell "underpowered" from "never measured".

        Both were exit 1, so a gate wired to this script read a failed fetch or
        a wrong --stages-dir as a NO-GO verdict -- acting on a measurement
        nobody took. Fails closed either way, but the two mean opposite things
        about what to do next.
        """
        from scripts.partition_mde import main

        assert main(["--stages-dir", str(FIXTURE)]) == EXIT_NO_GO
        capsys.readouterr()

        assert main(["--stages-dir", str(tmp_path)]) == EXIT_UNMEASURABLE
        captured = capsys.readouterr()
        assert "missing" in captured.err
        assert "NO-GO" not in captured.out

    def test_the_bucket_is_not_hardcoded(self):
        """Fetching must read the staging bucket from the environment.

        A bucket baked into committed source is both a leak and a lie the next
        project inherits. An unset bucket is unmeasurable, not a verdict, so it
        carries the distinct exit code.
        """
        from scripts.partition_mde import staging_bucket

        with pytest.raises(Unmeasurable, match="GCP_STAGING_BUCKET") as caught:
            staging_bucket(env={})
        assert caught.value.code == EXIT_UNMEASURABLE
        assert staging_bucket(env={"GCP_STAGING_BUCKET": "some-bucket"}) == "some-bucket"
