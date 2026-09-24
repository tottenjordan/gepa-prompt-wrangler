"""Can the 12-case held-out `test` partition detect the effects we care about?

**This is a decision gate, not a feature.** The plan it sits in front of builds
reporting on `wrangler.core.partitions.load_partitions()["test"]`. If twelve
cases cannot resolve the effect sizes this project actually measures, that
reporting would produce confident-looking nulls forever, and the right time to
find out is before it is built rather than after campaign 10 comes back
"unresolved" for the third time.

## The formula

Miller, *Adding Error Bars to Evals* (arXiv, 2024-11-01):

    delta = (z_{alpha/2} + z_beta) * sqrt( (omega^2 + sigma_A^2/K_A + sigma_B^2/K_B) / n )

- ``omega^2`` -- **question-sampling variance**: how much the per-case effect
  varies across *different cases*. Reducible only by more cases. ``K`` does not
  touch it, which is the structural reason DOE 03 measured `score_repeats`
  buying essentially nothing for `safety_v1` (exponent 0.02).
- ``sigma^2/K`` -- **per-question measurement variance**, averaged down by K
  repeats. Here ``K`` is `num_runs`: campaign 09 ran ``num_runs: 2``, so
  ``K_OBSERVED = 2``.
- ``n`` -- number of cases. alpha = 0.05 two-sided, power = 0.80.

The arithmetic itself lives in `wrangler.reporting.inference` so the reporting
this gate guards can reuse it; this file is the CLI, the artifact IO and the
report. What follows is how the estimators are wired, not how they compute.

## Estimators, and why these ones

Every number below is measured off campaign 09's six eval sides
(``eval_{before,after}`` x ``{control, rationale-on, rationale-off}``), paired
on ``case_index`` and restricted to the 63 cases present on all six. Nothing is
assumed.

**THE VERDICT IS A DIRECT MEASUREMENT, AND IT IS INVARIANT TO THE sigma^2
ESTIMATOR.** This is the strongest property the gate has, so it is stated as an
identity rather than left implicit. For a contrast ``c_i`` (per case: a DiD
against the control, or the on-minus-off contrast), ``Var_i(c_i)`` *is*
``omega^2 + sum sigma^2/K`` at the K campaign 09 ran -- no model, no split. The
decomposition is then *defined* from that total and a measurement estimate
``m``::

    omega^2          = max(0, total - m)
    sigma^2_combined = min(m, total) * K_OBSERVED

which gives, for every ``m >= 0``::

    bracket_at(K_OBSERVED) = max(0, total - m) + min(m, total) = total

(``m <= total``: ``(total - m) + m``; ``m > total``: ``0 + total``.) The cap is
what makes this hold: without it a large sigma^2 estimate would let the
``K = K_OBSERVED`` column disagree with the measurement it was derived from.

The held-out verdict is read at ``K = K_OBSERVED``, so **no sigma^2 estimate
can move it** -- not a different one, not a mis-scaled one, not an inverted
one. Measured across all 15 test-partition cells, the two estimators' MDEs
differ by at most 5.6e-17. ``tests/test_inference.py`` pins the identity and
``tests/test_partition_mde.py`` pins the verdict it produces here.

The decomposition therefore answers only the follow-up question, "could a
bigger `num_runs` budget rescue it?", which is where the ``K != K_OBSERVED``
columns live and where a bad sigma^2 would do damage.

**Two sigma^2 estimators, reported as a bracket, because they disagree by ~2x
and the disagreement is itself the finding.**

- ``within-window``, from ``scores_std``. That field is the spread of the ARM
  MEAN across the `num_runs` passes of one eval side, so the per-question
  variance behind it is ``n_side * scores_std^2``. Both passes run minutes
  apart against one engine, so this captures only the noise `num_runs`
  demonstrably averages.
- ``cross-window``, from the CONTROL arm's per-case delta. The control's prompt
  is byte-identical on both sides, so under Miller's model its true
  per-question effect is exactly zero and **all** of ``Var_i(ctrl_after -
  ctrl_before)`` is measurement -- at the real 12-20 h gap a campaign's two
  eval sides are separated by. This is the larger estimate, and CLAUDE.md
  already records why: campaign 09's control drifted +0.0732 on `safety_v1`
  against DOE 03's 0.0082 floor, a 9x disagreement whose only established
  distinguishing feature is that DOE 03's captures were minutes apart.

Neither is obviously right, so both are printed. Using ``cross-window`` drives
``omega^2`` negative for `safety_v1` and `instruction_following_v1` -- the
control drifted *more* than the contrasts vary -- which is reported as
``clamped`` rather than smoothed away. **A decomposition that cannot resolve
its own sign is not evidence, and the honest reading is that the split is
underdetermined at this sample size while the total is not.**

Conservatism, stated: ``within-window`` credits `num_runs` with the least, so
it gives the *worst* MDE and the *best* case for "more cases, not more runs";
``cross-window`` credits it with the most and is the upper bound on what budget
could buy. The verdict is reported under both and does not depend on the
choice.

## Usage

    uv run python scripts/partition_mde.py                        # committed c09 fixtures
    uv run python scripts/partition_mde.py --stages-dir /tmp/c09
    uv run python scripts/partition_mde.py --fetch --run-id run-86239e1924 \
        --stages-dir /tmp/c09

The default reads ``tests/fixtures/c09`` -- campaign 09's real stage artifacts,
committed for exactly this reason, the same way ``tests/test_noise_floor.py``
carries campaign 06's. ``--fetch`` reads the staging bucket from
``GCP_STAGING_BUCKET``; it is never written into this file.

Exit codes, so the gate can be read by something other than a human:

===  ==========================================================================
0    GO -- the partition resolves both target effects.
1    NO-GO -- measured, and underpowered.
2    the gate could not be measured (artifacts or configuration missing).
===  ==========================================================================

1 and 2 are distinct deliberately, the same split ``wrangler evaluators
trace-health`` draws: a caller that cannot tell them apart reads a broken fetch
as a verdict, and acts on a measurement nobody took.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from wrangler.core.partitions import load_partitions
from wrangler.reporting.inference import (
    K_OBSERVED,
    Components,
    Contrast,
    EvalSide,
    _variance,
    common_cases,
    cross_window_measurement,
    minimum_detectable_effect,
    per_case_contrast,
    required_cases,
    variance_components,
    within_window_measurement,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- Design constants -------------------------------------------------------

#: Exit codes. A NO-GO is a measurement; a missing artifact is not. Collapsing
#: them onto 1 lets a caller act on a verdict that was never computed.
EXIT_GO = 0
EXIT_NO_GO = 1
EXIT_UNMEASURABLE = 2

#: Campaign 09's real stage artifacts, committed so this gate reproduces without
#: network or credentials (the pattern `tests/test_noise_floor.py` already uses).
DEFAULT_STAGES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "c09"


class Unmeasurable(SystemExit):
    """The gate could not be evaluated: artifacts or configuration are missing.

    A ``SystemExit`` subclass so an uncaught one still exits
    ``EXIT_UNMEASURABLE`` rather than 1, and so the message survives ``str()``
    for callers that catch it. ``main()`` catches it and reports on stderr.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = EXIT_UNMEASURABLE


#: The two effects the held-out reporting would have to detect. Both from
#: docs/analysis/2026-09-22-campaign-09-reanalysis.md.
EFFECT_DID = 0.0952  # optimized arm vs unoptimized control, `safety_v1`
EFFECT_CONTRAST = 0.075  # rationale on/off, the secondary metrics

CONTROL_ARM = "c09-control"
TREATMENT_ARMS = ("c09-rationale-on", "c09-rationale-off")
PHASES = ("eval_before", "eval_after")

METRICS = (
    "safety_v1",
    "instruction_following_v1",
    "hallucination_v1",
    "final_response_quality_v1",
    "tool_use_quality_v1",
)

CASE_COUNTS = (12, 16, 20, 24)
REPEAT_LEVELS = (1, 2)


# --- Reading the artifacts --------------------------------------------------


def load_sides(stages_dir: str | Path) -> dict[tuple[str, str], EvalSide]:
    """Load the six ``stages/eval_{before,after}/<arm>.json`` artifacts."""
    root = Path(stages_dir)
    sides: dict[tuple[str, str], EvalSide] = {}
    for phase in PHASES:
        for arm in (CONTROL_ARM, *TREATMENT_ARMS):
            path = root / phase / f"{arm}.json"
            if not path.is_file():
                raise Unmeasurable(
                    f"missing {path}\nFetch the campaign 09 stage artifacts first:\n"
                    f"  uv run python {Path(__file__).name} --fetch --run-id <run-id>\n"
                    f"or point --stages-dir at a directory holding "
                    f"{'/'.join(PHASES)}/<arm>.json"
                )
            payload = json.loads(path.read_text())
            rows = payload.get("per_case") or []
            if not rows:
                raise Unmeasurable(f"{path} has no per_case rows; the MDE needs per-case scores")
            sides[(phase, arm)] = EvalSide(
                arm=arm,
                phase=phase,
                per_case={int(row["case_index"]): row for row in rows},
                scores_std=payload.get("scores_std") or {},
                num_runs=int(payload.get("num_runs") or 1),
                score_repeats=int(payload.get("score_repeats") or 1),
            )
    return sides


# --- Fetching ---------------------------------------------------------------


def staging_bucket(env: Mapping[str, str] | None = None) -> str:
    """The staging bucket, from the environment. Never a literal in this file."""
    environ = os.environ if env is None else env
    bucket = environ.get("GCP_STAGING_BUCKET", "").strip()
    if not bucket:
        raise Unmeasurable(
            "GCP_STAGING_BUCKET is not set; it holds the pipeline stage artifacts. "
            "Set it in .env, or pass --stages-dir to read artifacts already on disk."
        )
    return bucket.removeprefix("gs://").strip("/")


def fetch_stage_artifacts(run_id: str, dest: Path, *, bucket: str | None = None) -> None:
    """Copy the six stage artifacts down with `gcloud storage cp`."""
    bucket = bucket or staging_bucket()
    for phase in PHASES:
        (dest / phase).mkdir(parents=True, exist_ok=True)
        for arm in (CONTROL_ARM, *TREATMENT_ARMS):
            uri = f"gs://{bucket}/pipeline-runs/{run_id}/stages/{phase}/{arm}.json"
            print(f"  fetching {uri}")
            subprocess.run(
                ["gcloud", "storage", "cp", uri, str(dest / phase / f"{arm}.json")],
                check=True,
            )


# --- Report -----------------------------------------------------------------


CONTRASTS = (
    Contrast("DiD vs control (on)", TREATMENT_ARMS[0], CONTROL_ARM, EFFECT_DID),
    Contrast("DiD vs control (off)", TREATMENT_ARMS[1], CONTROL_ARM, EFFECT_DID, headline=False),
    Contrast("rationale on - off", TREATMENT_ARMS[0], TREATMENT_ARMS[1], EFFECT_CONTRAST),
)

#: `num_runs` scaling exponents measured by DOE 03 on a different pool
#: (docs/analysis/2026-09-17-doe-03-result.md). A weak, directional cross-check
#: on the SECONDARY question only -- see `doe03_agreement` for what it is and
#: is not evidence of.
DOE03_NUM_RUNS_EXPONENT = {
    "safety_v1": 0.58,
    "final_response_quality_v1": 0.34,
    "hallucination_v1": 0.39,
    "instruction_following_v1": 0.20,
    "tool_use_quality_v1": -0.11,
}

SCENARIOS = ("within-window", "cross-window")


def _components(
    sides: Mapping[tuple[str, str], EvalSide],
    contrast: Contrast,
    metric: str,
    cases: Sequence[int],
    scenario: str,
    *,
    variance_cases: Sequence[int] | None = None,
) -> Components:
    """Components for one contrast/metric/scenario.

    ``variance_cases`` lets the total be measured over a subset (the held-out
    partition) while sigma^2 stays estimated over all paired cases -- a
    12-case control-arm variance is far too noisy to carry the split.
    """
    values = per_case_contrast(
        sides, contrast.treatment, contrast.baseline, metric, variance_cases or cases
    )
    if scenario == "within-window":
        measurement = within_window_measurement(sides, metric, n_sides=contrast.n_sides)
    else:
        measurement = cross_window_measurement(
            sides, metric, cases, n_sides=contrast.n_sides, control_arm=CONTROL_ARM
        )
    return variance_components(total=_variance(values), measurement_at_k_observed=measurement)


def build_report(sides: Mapping[tuple[str, str], EvalSide]) -> dict:
    """Everything the printed report needs, as data, so it can be tested."""
    cases = common_cases(sides)
    test_cases = [i for i in load_partitions()["test"] if i in set(cases)]
    return {
        "paired_cases": cases,
        "test_cases": test_cases,
        "components": {
            (c.label, metric, scenario): _components(sides, c, metric, cases, scenario)
            for c in CONTRASTS
            for metric in METRICS
            for scenario in SCENARIOS
        },
        "test_components": {
            (c.label, metric, scenario): _components(
                sides, c, metric, cases, scenario, variance_cases=test_cases
            )
            for c in CONTRASTS
            for metric in METRICS
            for scenario in SCENARIOS
        },
    }


def _print_variance_table(sides: Mapping[tuple[str, str], EvalSide], report: dict) -> None:
    cases = report["paired_cases"]
    print(f"\nMEASURED VARIANCE COMPONENTS  (per case, {len(cases)} paired cases, K={K_OBSERVED})")
    print(
        f"  {'metric':27}{'sig2 within':>12}{'sig2 cross':>12}"
        f"{'V(on-off)':>11}{'w2 within':>11}{'w2 cross':>11}"
    )
    for metric in METRICS:
        within = within_window_measurement(sides, metric, n_sides=2) * K_OBSERVED
        cross = (
            cross_window_measurement(sides, metric, cases, n_sides=2, control_arm=CONTROL_ARM)
            * K_OBSERVED
        )
        key = ("rationale on - off", metric, "within-window")
        comp_w = report["components"][key]
        comp_c = report["components"][("rationale on - off", metric, "cross-window")]
        flag = " *" if comp_c.clamped else ""
        print(
            f"  {metric:27}{within:12.5f}{cross:12.5f}"
            f"{comp_w.total:11.5f}{comp_w.omega2:11.5f}{comp_c.omega2:11.5f}{flag}"
        )
    print(
        "  sig2 = sigma_A^2 + sigma_B^2 for ONE before/after pair, at K=1.\n"
        "  * omega^2 clamped at 0: the control drifted more than the contrast varies,\n"
        "    so the split is underdetermined. The measured total is unaffected."
    )


def reducible_share(report: dict, metric: str, scenario: str = "within-window") -> float:
    """Fraction of the on-minus-off contrast's variance that `num_runs` can average."""
    comp = report["components"][("rationale on - off", metric, scenario)]
    if comp.total <= 0:
        return 0.0
    return (comp.sigma2_combined / comp.k_observed) / comp.total


def doe03_agreement(report: dict) -> float:
    """Correlation between the K-reducible share and DOE 03's `num_runs` exponents.

    **This bears on the secondary question only.** DOE 03 measured, on a
    different pool, how fast each metric's floor falls with `num_runs`; that is
    the same physical quantity as "how much of this metric's variance is
    measurement". If the two disagreed in rank order, the ``K != K_OBSERVED``
    columns would be recommending budget that cannot work. It **cannot** turn
    the no-go into a go: the verdict is read at ``K = K_OBSERVED``, where
    ``bracket_at`` reproduces the directly measured total for *any* sigma^2
    estimate, inverted included (module docstring, and pinned by
    ``TestTheVerdictIsSigma2Invariant``).

    **It is also weak.** r = +0.81 on FIVE points is t = 2.41 on 3 df,
    two-sided p ~ 0.095 -- not significant at any conventional level. A
    leave-one-eval-side-out jackknife holds at r ~ +0.80 to +0.85 on five of
    six replicates, but dropping ``eval_after/c09-rationale-on`` gives
    **r = +0.17**, with `safety_v1`'s reducible share falling 0.710 -> 0.214:
    that one side carries 75% of `safety_v1`'s within-window sigma^2 pool
    through a single 1-degree-of-freedom standard deviation. Read it as
    directionally consistent, not as confirmation.
    """
    mine = [reducible_share(report, m) for m in METRICS]
    theirs = [DOE03_NUM_RUNS_EXPONENT[m] for m in METRICS]
    return st.correlation(mine, theirs)


def _print_doe03_check(report: dict) -> None:
    print("\nCROSS-CHECK AGAINST DOE 03  (weak, and on the BUDGET question only)")
    print(f"  {'metric':27}{'K-reducible share':>19}{'DOE 03 num_runs e':>19}")
    for metric in METRICS:
        print(
            f"  {metric:27}{reducible_share(report, metric):19.3f}"
            f"{DOE03_NUM_RUNS_EXPONENT[metric]:19.2f}"
        )
    print(
        f"  Pearson r = {doe03_agreement(report):+.2f} across five metrics, and the extremes\n"
        "  agree: `safety_v1` most reducible by `num_runs`, `instruction_following_v1`\n"
        "  least -- which is what DOE 03 concluded from resampled captures. The one\n"
        "  disagreement is `tool_use_quality_v1`, whose DOE 03 exponent is negative and\n"
        "  which CLAUDE.md already records as 'already at its floor'.\n"
        "\n"
        "  DO NOT QUOTE THIS AS A CHECK ON THE VERDICT. Two reasons:\n"
        "  - It CANNOT bear on it. The verdict is read at K=2, where the bracket equals\n"
        "    the directly measured variance for any sigma^2 estimate (module docstring).\n"
        "    It speaks only to the 'would more `num_runs` help?' columns.\n"
        "  - On five points, r = +0.81 is t = 2.41 on 3 df, two-sided p ~ 0.095 -- not\n"
        "    significant. Leaving one eval side out at a time, five of six replicates\n"
        "    hold near +0.8, but dropping eval_after/c09-rationale-on gives r = +0.17\n"
        "    (`safety_v1`'s share 0.710 -> 0.214): that side alone is 75% of the\n"
        "    within-window sigma^2 pool, from one 1-df standard deviation."
    )


def _print_mde_grid(report: dict) -> None:
    print("\nMDE BY CASE COUNT AND num_runs  (rationale on - off; alpha=0.05, power=0.80)")
    header = "".join(f"n={n},K={k}".rjust(11) for n in CASE_COUNTS for k in REPEAT_LEVELS)
    for scenario in SCENARIOS:
        print(f"\n  sigma^2 estimator: {scenario}")
        print(f"  {'metric':27}{header}{'K->inf':>11}")
        for metric in METRICS:
            comp = report["components"][("rationale on - off", metric, scenario)]
            cells = "".join(f"{comp.mde(n, k):11.4f}" for n in CASE_COUNTS for k in REPEAT_LEVELS)
            irreducible = minimum_detectable_effect(
                omega2=comp.omega2, sigma2_a=0.0, sigma2_b=0.0, n=CASE_COUNTS[0]
            )
            flag = " *" if comp.clamped else ""
            print(f"  {metric:27}{cells}{irreducible:11.4f}{flag}")
    print(
        f"\n  K->inf is the n={CASE_COUNTS[0]} MDE with the measurement term driven to zero:\n"
        "  the best any `num_runs` budget could ever do. Where it already exceeds the\n"
        "  target effect, no amount of re-running resolves that metric at that n.\n"
        "  * omega^2 clamped at 0, so this row's K->inf of 0.0000 is an artefact of the\n"
        "    clamp, NOT a claim that enough runs would resolve it. Read the\n"
        "    within-window scenario for those metrics."
    )


def _print_test_partition(report: dict) -> tuple[bool, list[str]]:
    test_cases = report["test_cases"]
    print(
        f"\nTHE ACTUAL HELD-OUT PARTITION  (n={len(test_cases)}, "
        f"case_index {', '.join(str(i) for i in test_cases)})"
    )
    print(
        "  variance measured over these twelve cases specifically, not a generic n=12.\n"
        f"  Read at K={K_OBSERVED}, where omega^2 + sigma^2/K is IDENTICALLY the measured\n"
        "  per-case variance, so every MDE below is a direct measurement and is the same\n"
        "  under either sigma^2 estimator (max difference 5.6e-17). The verdict cannot be\n"
        "  moved by getting the decomposition wrong."
    )
    print(f"\n  {'metric':27}{'contrast':22}{'MDE':>9}{'target':>9}{'ratio':>8}  verdict")

    failures: list[str] = []
    all_go = True
    for contrast in CONTRASTS:
        if not contrast.headline:
            continue
        for metric in METRICS:
            comp = report["test_components"][(contrast.label, metric, "within-window")]
            mde = comp.mde(len(test_cases), K_OBSERVED)
            ratio = mde / contrast.target
            ok = mde <= contrast.target
            all_go = all_go and ok
            if not ok:
                failures.append(f"{metric} / {contrast.label}")
            print(
                f"  {metric:27}{contrast.label:22}{mde:9.4f}{contrast.target:9.4f}"
                f"{ratio:8.1f}x  {'GO' if ok else 'NO-GO'}"
            )
    return all_go, failures


def _print_required_cases(report: dict) -> dict[str, int]:
    print(
        f"\nWHAT IT WOULD TAKE  (cases to reach +-{EFFECT_CONTRAST:.3f} on the "
        f"{len(report['paired_cases'])} paired cases,\n"
        "  under BOTH sigma^2 branches: w = within-window, x = cross-window)"
    )
    print(
        f"  {'metric':27}{'K=2':>8}{'K=4 w':>8}{'K=4 x':>8}"
        f"{'K=8 w':>8}{'K=8 x':>8}{'w2 only w':>11}{'w2 only x':>11}"
    )
    needed: dict[str, int] = {}
    clamped: list[str] = []
    for metric in METRICS:
        within = report["components"][("rationale on - off", metric, "within-window")]
        cross = report["components"][("rationale on - off", metric, "cross-window")]
        # One K=K_OBSERVED column, not two: the identity makes them equal, and
        # printing both would imply the branch choice matters where it cannot.
        n2 = within.cases_for(EFFECT_CONTRAST, K_OBSERVED)
        inf_w = required_cases(
            omega2=within.omega2, sigma2_a=0.0, sigma2_b=0.0, target=EFFECT_CONTRAST
        )
        inf_x = required_cases(
            omega2=cross.omega2, sigma2_a=0.0, sigma2_b=0.0, target=EFFECT_CONTRAST
        )
        needed[metric] = n2
        if cross.clamped:
            clamped.append(metric)
        print(
            f"  {metric:27}{n2:8d}"
            f"{within.cases_for(EFFECT_CONTRAST, 4):8d}{cross.cases_for(EFFECT_CONTRAST, 4):8d}"
            f"{within.cases_for(EFFECT_CONTRAST, 8):8d}{cross.cases_for(EFFECT_CONTRAST, 8):8d}"
            f"{inf_w:11d}{inf_x:11d}{' *' if cross.clamped else ''}"
        )
    safety = report["components"][("rationale on - off", "safety_v1", "cross-window")]
    print(
        f"  The K={K_OBSERVED} column is branch-invariant -- at the K campaign 09 ran the\n"
        "  bracket IS the measured total -- so the two estimators give one column. They\n"
        "  diverge only where they credit `num_runs` differently; cross-window is the\n"
        "  OPTIMISTIC branch, the upper bound on what budget could buy.\n"
        "  'w2 only' is omega^2 alone: infinite `num_runs`, the irreducible case count.\n"
        "  * omega^2 clamped at 0 under cross-window, for:\n"
        f"      {', '.join(clamped)}\n"
        "    so that branch's 'w2 only' column reads 1 as an artefact of the clamp, NOT\n"
        "    as a claim that runs would resolve those metrics. The conclusion survives\n"
        "    either branch anyway: even on the optimistic one, `safety_v1` needs\n"
        f"    {safety.cases_for(EFFECT_CONTRAST, 4)} cases at K=4 and "
        f"{safety.cases_for(EFFECT_CONTRAST, 8)} at K=8.\n"
        f"  The whole eval set holds 64 cases, of which {len(report['test_cases'])} are held out."
    )
    return needed


def _print_caveats() -> None:
    print(
        "\nREAD BEFORE QUOTING ANY NUMBER ABOVE\n"
        "  - The n=12 MDE is itself OPTIMISTIC. Bowyer et al. (ICML 2025 Spotlight)\n"
        "    measure CLT-based intervals as miscalibrated below a few hundred\n"
        "    datapoints: at N=100 a nominal-95% interval achieved 92.5% coverage. The\n"
        "    normal approximation this formula rests on is worse at 12, so treat the\n"
        "    12-case figures as a lower bound on the true MDE, not an estimate of it.\n"
        "    One component of that is quantifiable: swapping the normal multiplier for\n"
        "    Student's t at df = n-1 = 11 takes 2.8016 -> 3.0765, so every n=12 MDE\n"
        "    above is understated by 9.8%. That widens the NO-GO; it cannot narrow it.\n"
        "  - `safety_v1` is quarter-valued (0.25/0.5/0.75/1.0). Over twelve cases the\n"
        "    mean lands on twelfths of a quarter-step and small differences are\n"
        "    discretisation, which no variance formula models.\n"
        "  - These variances bound CASE-SAMPLING noise only. They say nothing about\n"
        "    which prompt GEPA happened to find -- two runs of one manifest have\n"
        "    differed by 12.3x the control floor, and that term is larger than\n"
        "    everything measured here.\n"
        "  - One run per condition in campaign 09, so each variance estimate carries\n"
        "    that run's luck. Re-measure on the day; a floor is not a property of a\n"
        "    metric (CLAUDE.md, campaign 09's +0.0732 control drift)."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Print the gate and return its exit code. See the module docstring for the codes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--stages-dir",
        default=str(DEFAULT_STAGES_DIR),
        help="directory holding eval_before/<arm>.json and eval_after/<arm>.json "
        "(default: the committed campaign 09 fixtures)",
    )
    parser.add_argument("--fetch", action="store_true", help="download the artifacts first")
    parser.add_argument("--run-id", default="run-86239e1924", help="pipeline run id to fetch")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    stages_dir = Path(args.stages_dir)
    try:
        if args.fetch:
            fetch_stage_artifacts(args.run_id, stages_dir)
        sides = load_sides(stages_dir)
    except Unmeasurable as exc:
        # NOT a NO-GO: nothing was measured, and a caller must be able to tell.
        print(exc, file=sys.stderr)
        return EXIT_UNMEASURABLE
    report = build_report(sides)

    print("=" * 78)
    print("MINIMUM DETECTABLE EFFECT ON THE HELD-OUT TEST PARTITION")
    print("=" * 78)
    print(
        f"  source     : {stages_dir}  (campaign 09, six eval sides)\n"
        f"  paired on  : {len(report['paired_cases'])} cases present on all six sides\n"
        f"  measured at: num_runs={K_OBSERVED}, score_repeats=2\n"
        f"  targets    : DiD vs control {EFFECT_DID:+.4f} | "
        f"rationale on-off +-{EFFECT_CONTRAST:.3f}"
    )

    _print_variance_table(sides, report)
    _print_doe03_check(report)
    _print_mde_grid(report)
    go, failures = _print_test_partition(report)
    needed = _print_required_cases(report)
    _print_caveats()

    print("\n" + "=" * 78)
    if go:
        print("VERDICT: GO -- the 12-case test partition resolves both target effects.")
        print("=" * 78)
        return EXIT_GO

    worst = max(needed.values())
    best = min(needed.values())
    print(
        f"VERDICT: NO-GO -- {len(failures)} of {len(METRICS) * 2} metric/contrast pairs are\n"
        f"underpowered on the 12-case test partition, including every one this project\n"
        f"reports on. Detecting +-{EFFECT_CONTRAST:.3f} needs roughly {best}-{worst} cases at\n"
        f"num_runs={K_OBSERVED}; the entire eval set holds 64. A held-out partition carved\n"
        f"out of these 64 cases cannot be made large enough, so the next step is more\n"
        f"eval cases or a within-case design, NOT a bigger test split."
    )
    print("=" * 78)
    return EXIT_NO_GO


if __name__ == "__main__":
    raise SystemExit(main())
