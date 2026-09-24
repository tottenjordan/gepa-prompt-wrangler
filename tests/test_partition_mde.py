"""The held-out test partition's NO-GO verdict, pinned end to end.

`scripts/partition_mde.py` is a DECISION GATE: it says whether the checked-in
12-case `test` partition can resolve the two effect sizes this project needs to
detect (+0.0952 and +-0.075, both from
`docs/analysis/2026-09-22-campaign-09-reanalysis.md`). A gate that answers from
assumed variance is worse than no gate, because it produces a plausible number
with nothing behind it -- so every estimator is fed the real campaign 09
per-case artifacts in `tests/fixtures/c09/`, the same way
`tests/test_noise_floor.py` pins the floor to campaign 06's.

The arithmetic those estimators are built from now lives in
`wrangler.reporting.inference`, and `tests/test_inference.py` guards it. This
file guards the layer the script kept: loading the artifacts, assembling the
report, and the printed verdict with its exit code.

The failures these tests exist to prevent, concretely:

- the verdict starting to depend on the sigma^2 decomposition. It does not
  today: it is read at ``K = K_OBSERVED``, where the bracket is IDENTICALLY the
  directly measured per-case variance for any sigma^2 estimate. That identity
  is what makes the no-go a measurement rather than a model, and
  `TestTheVerdictIsSigma2Invariant` pins that the assembled report still
  exhibits it on real data;
- the report being wired to the wrong campaign's numbers, so the variance
  behind the gate is not campaign 09's;
- an estimator degenerating against known inputs and misdirecting the
  *secondary* "would more `num_runs` help?" columns;
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
    METRICS,
    SCENARIOS,
    Unmeasurable,
    build_report,
    doe03_agreement,
    load_sides,
    reducible_share,
)
from wrangler.core.partitions import load_partitions
from wrangler.reporting.inference import (
    K_OBSERVED,
    EvalSide,
    common_cases,
    minimum_detectable_effect,
    per_case_contrast,
)

FIXTURE = Path(__file__).parent / "fixtures" / "c09"

CONTROL = "c09-control"
ON = "c09-rationale-on"
OFF = "c09-rationale-off"


@pytest.fixture
def sides() -> dict[tuple[str, str], EvalSide]:
    return load_sides(FIXTURE)


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
