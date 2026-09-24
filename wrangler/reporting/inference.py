"""Minimum-detectable-effect arithmetic for paired per-case eval contrasts.

This is the statistics behind `scripts/partition_mde.py`, which asked whether a
12-case held-out partition could resolve the effect sizes this project measures
and answered NO-GO. That script owned the whole calculation, and `scripts/` is
not importable from `wrangler/` -- so the only power calculation this repo has
ever done could not be reused by the reporting it was built to gate. Everything
here is the arithmetic layer, lifted unchanged; the CLI, the artifact IO and
every printed table stay in the script.

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

## The sigma^2 split, and why it is an identity

For a contrast ``c_i`` (per case: a DiD against a control, or an on-minus-off
contrast), ``Var_i(c_i)`` *is* ``omega^2 + sum sigma^2/K`` at the K the campaign
ran -- no model, no split. `variance_components` *defines* the decomposition
from that measured total and a measurement estimate ``m``::

    omega^2          = max(0, total - m)
    sigma^2_combined = min(m, total) * K_OBSERVED

which gives, for every ``m >= 0``::

    bracket_at(K_OBSERVED) = max(0, total - m) + min(m, total) = total

(``m <= total``: ``(total - m) + m``; ``m > total``: ``0 + total``.) The cap is
what makes this hold: without it a large sigma^2 estimate would let the
``K = K_OBSERVED`` column disagree with the measurement it was derived from.

**A verdict read at ``K = K_OBSERVED`` therefore cannot be moved by any sigma^2
estimate** -- not a different one, not a mis-scaled one, not an inverted one.
The decomposition answers only the follow-up question, "could a bigger
`num_runs` budget rescue it?", which is where the ``K != K_OBSERVED`` columns
live and where a bad sigma^2 would do damage. `tests/test_inference.py` pins the
identity, including against absurd ``m``.

## Two measurement estimators

`within_window_measurement` reads ``scores_std`` -- the spread of the arm mean
across the `num_runs` passes of one eval side, minutes apart against one engine,
so it captures only the noise `num_runs` demonstrably averages.
`cross_window_measurement` reads a control arm's per-case delta: its prompt is
byte-identical on both sides, so under Miller's model its true per-question
effect is exactly zero and all of that variance is measurement -- at the real
12-20 h gap between a campaign's two eval sides.

They disagree by roughly 2x and the disagreement is itself a finding, so callers
are expected to report both rather than pick one.
"""

from __future__ import annotations

import json
import math
import statistics as st
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- Design constants -------------------------------------------------------

ALPHA = 0.05
POWER = 0.80

#: Campaign 09 ran `num_runs: 2`, so every variance read off it is a K = 2
#: measurement. Re-apportioning to other K is only meaningful relative to this.
K_OBSERVED = 2

#: The effect sizes this project has actually measured, from
#: docs/analysis/2026-09-22-campaign-09-reanalysis.md: the optimized-arm-vs-
#: control DiD on `safety_v1`, and the rationale on/off contrast that document
#: tells campaign 10 to design for. They are what a design is compared against
#: when no target is named -- never a convention, always a measurement.
REFERENCE_EFFECT_DID = 0.0952
REFERENCE_EFFECT_CONTRAST = 0.075

#: Campaign 09's committed stage artifacts -- the ONLY measured per-case
#: variance this repo has. They live under `tests/` because that is where they
#: were first committed (`scripts/partition_mde.py` reads the same directory,
#: and `tests/test_noise_floor.py` carries campaign 06's the same way), and
#: moving them would break a published gate for no measurement gain. Every
#: consumer takes the directory as an argument, so nothing here is pinned to a
#: test tree at import time.
C09_STAGES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "c09"

#: The arms and phases campaign 09 ran. The treatment/baseline pair below is
#: the on-minus-off contrast, which is the four-sided shape a two-arm campaign
#: readout has -- not the DiD against the control, whose two deltas the
#: reanalysis found to be the same 11/63 cases and therefore not independent.
C09_PHASES = ("eval_before", "eval_after")
C09_TREATMENT = "c09-rationale-on"
C09_BASELINE = "c09-rationale-off"
C09_CONTROL = "c09-control"

#: Wrap width for the rendered block. `preflight.render` indents detail by
#: eight columns, so this leaves the whole thing inside 80 in a terminal.
_WRAP = 72


# --- The formula ------------------------------------------------------------


def z_multiplier(alpha: float = ALPHA, power: float = POWER) -> float:
    """``z_{alpha/2} + z_beta``, two-sided. ~2.80159 at 0.05 / 0.80."""
    normal = st.NormalDist()
    return normal.inv_cdf(1 - alpha / 2) + normal.inv_cdf(power)


def minimum_detectable_effect(
    *,
    omega2: float,
    sigma2_a: float,
    sigma2_b: float,
    n: int,
    k_a: int = 1,
    k_b: int = 1,
    alpha: float = ALPHA,
    power: float = POWER,
) -> float:
    """Miller's MDE. See the module docstring for what each term means here."""
    if n <= 0:
        raise ValueError(f"n must be a positive case count, got {n}")
    if k_a <= 0 or k_b <= 0:
        raise ValueError(f"K must be a positive repeat count, got ({k_a}, {k_b})")
    bracket = omega2 + sigma2_a / k_a + sigma2_b / k_b
    return z_multiplier(alpha, power) * math.sqrt(bracket / n)


def required_cases(
    *,
    omega2: float,
    sigma2_a: float,
    sigma2_b: float,
    target: float,
    k_a: int = 1,
    k_b: int = 1,
    alpha: float = ALPHA,
    power: float = POWER,
) -> int:
    """Smallest ``n`` whose MDE reaches ``target``. Inverts the formula exactly."""
    if target <= 0:
        raise ValueError(f"target effect must be positive, got {target}")
    bracket = omega2 + sigma2_a / k_a + sigma2_b / k_b
    exact = (z_multiplier(alpha, power) ** 2) * bracket / target**2
    return max(1, math.ceil(exact))


# --- Variance components ----------------------------------------------------


@dataclass(frozen=True)
class Components:
    """One contrast's variance, split into what K can and cannot reduce.

    ``total`` is the directly measured per-case variance at ``k_observed``, and
    ``bracket_at(k_observed)`` reproduces it exactly. ``clamped`` records that
    the measurement estimate exceeded the total, i.e. the split failed and only
    the total should be quoted.
    """

    total: float
    omega2: float
    sigma2_combined: float
    k_observed: int
    clamped: bool

    def bracket_at(self, k: int) -> float:
        """``omega^2 + sum sigma^2 / k``, re-apportioned from the measured total."""
        if k <= 0:
            raise ValueError(f"K must be a positive repeat count, got {k}")
        return self.omega2 + self.sigma2_combined / k

    def mde(self, n: int, k: int) -> float:
        half = self.sigma2_combined / 2
        return minimum_detectable_effect(
            omega2=self.omega2, sigma2_a=half, sigma2_b=half, n=n, k_a=k, k_b=k
        )

    def cases_for(self, target: float, k: int) -> int:
        half = self.sigma2_combined / 2
        return required_cases(
            omega2=self.omega2, sigma2_a=half, sigma2_b=half, target=target, k_a=k, k_b=k
        )


def variance_components(
    *, total: float, measurement_at_k_observed: float, k_observed: int = K_OBSERVED
) -> Components:
    """Split a measured per-case variance into ``omega^2`` and a K-reducible part.

    ``measurement_at_k_observed`` is the estimated measurement contribution *at
    the K the total was measured at*, and is capped at ``total`` so the split
    can never invent variance. A cap that bites means the sigma^2 estimator
    exceeded what was observed, which is reported, not hidden.
    """
    reducible = min(measurement_at_k_observed, total)
    return Components(
        total=total,
        omega2=max(0.0, total - measurement_at_k_observed),
        sigma2_combined=reducible * k_observed,
        k_observed=k_observed,
        clamped=measurement_at_k_observed > total,
    )


# --- Eval sides and per-case contrasts --------------------------------------


@dataclass(frozen=True)
class EvalSide:
    """One arm's ``eval_before`` or ``eval_after`` stage artifact."""

    arm: str
    phase: str
    per_case: Mapping[int, Mapping[str, float]]
    scores_std: Mapping[str, float]
    num_runs: int
    score_repeats: int

    def within_side_variance(self, metric: str) -> float:
        """Per-question variance implied by ``scores_std``.

        ``scores_std`` is the across-`num_runs` spread of the ARM MEAN over
        ``len(per_case)`` cases, so the per-question variance behind it is
        ``n * std**2``. Reading ``scores_std**2`` directly would understate
        sigma^2 by the case count.

        ``len(per_case)`` is the UNION of cases across the side's runs (63 or
        64 here), which slightly overstates what any single run scored. Worth
        under 2%, and it cannot reach a verdict read at ``K = K_OBSERVED``,
        where the bracket equals the measured total whatever sigma^2 says.
        """
        return len(self.per_case) * self.scores_std.get(metric, 0.0) ** 2


@dataclass(frozen=True)
class Contrast:
    """A contrast a report would have to measure.

    ``headline`` marks the ones a verdict is computed over. Campaign 09's two
    DiD arms are **not independent replicates** -- the reanalysis found both
    deltas are exactly 11/63 because they fixed overlapping case sets -- so
    counting both in a verdict would double-weight one measurement.
    """

    label: str
    treatment: str
    baseline: str
    target: float
    n_sides: int = 4
    headline: bool = True


def common_cases(sides: Mapping[tuple[str, str], EvalSide]) -> list[int]:
    """Case indices scored on every side.

    Pairing is not optional: two of campaign 09's six sides scored 63/64, and
    differencing means over different case subsets is silent-failures #5.
    """
    shared: set[int] | None = None
    for side in sides.values():
        keys = set(side.per_case)
        shared = keys if shared is None else shared & keys
    return sorted(shared or set())


def per_case_delta(
    sides: Mapping[tuple[str, str], EvalSide],
    arm: str,
    metric: str,
    cases: Sequence[int],
) -> list[float]:
    """``after - before`` for one arm, per case."""
    before, after = sides[("eval_before", arm)], sides[("eval_after", arm)]
    return [after.per_case[i][metric] - before.per_case[i][metric] for i in cases]


def per_case_contrast(
    sides: Mapping[tuple[str, str], EvalSide],
    treatment: str,
    baseline: str,
    metric: str,
    cases: Sequence[int],
) -> list[float]:
    """Difference in differences, per case: ``delta(treatment) - delta(baseline)``."""
    treat = per_case_delta(sides, treatment, metric, cases)
    base = per_case_delta(sides, baseline, metric, cases)
    return [t - b for t, b in zip(treat, base, strict=True)]


def _variance(values: Sequence[float]) -> float:
    return st.variance(values) if len(values) > 1 else 0.0


def within_window_measurement(
    sides: Mapping[tuple[str, str], EvalSide], metric: str, *, n_sides: int
) -> float:
    """Measurement variance of an ``n_sides``-sided contrast, from ``scores_std``.

    Averaged over every side rather than taken per side: each side gives one
    stdev from two runs, which is a 1-degree-of-freedom estimate and swings by
    an order of magnitude between arms. Pooling six gives six.
    """
    per_side = st.mean(side.within_side_variance(metric) for side in sides.values())
    return n_sides * per_side / K_OBSERVED


def cross_window_measurement(
    sides: Mapping[tuple[str, str], EvalSide],
    metric: str,
    cases: Sequence[int],
    *,
    n_sides: int,
    control_arm: str,
) -> float:
    """Measurement variance of an ``n_sides``-sided contrast, from the control arm.

    ``control_arm`` names the arm whose two sides share a byte-identical prompt.
    It is a required argument rather than a constant here because the arm
    naming belongs to a campaign, not to the arithmetic -- and passing a
    *treatment* arm by mistake would silently read real prompt effect as
    measurement noise, so there is no default to get wrong quietly.

    The control's two sides share a byte-identical prompt, so ``Var_i`` of its
    per-case delta is a pure two-side measurement variance spanning the real
    gap between eval sides. An ``n_sides``-sided contrast stacks ``n_sides / 2``
    such pairs.
    """
    return (n_sides / 2) * _variance(per_case_delta(sides, control_arm, metric, cases))


# --- A design's sensitivity, against a measured variance --------------------


@dataclass(frozen=True)
class VarianceSource:
    """A measured per-case contrast variance, and where it was measured.

    This type exists so an MDE can never be quoted without its provenance.
    CLAUDE.md's standing rule is that **a floor is not a property of a metric**
    -- campaign 09's control drifted +0.0732 on `safety_v1` against the 0.0082
    DOE 03 had measured for the same metric, a 9x disagreement -- so a number
    presented as "the variance of `safety_v1`" is the exact mistake this repo
    keeps paying for. Everything here is carried into the printed output.
    """

    label: str
    contrast: str
    n_cases: int
    k_observed: int
    components: Mapping[str, Components]
    caveats: tuple[str, ...] = ()
    origin: str = ""

    @property
    def metrics(self) -> list[str]:
        return sorted(self.components)


@dataclass(frozen=True)
class DesignMDE:
    """What a proposed design could detect, per metric, and against what.

    ``per_metric`` is the answer; ``provenance`` is the sentence that makes it
    readable. They travel together on purpose -- see :class:`VarianceSource`.
    """

    n_cases: int
    num_runs: int
    source: VarianceSource
    per_metric: Mapping[str, float]

    @property
    def provenance(self) -> str:
        """The claim this object actually supports, in one sentence."""
        values = list(self.per_metric.values())
        return (
            f"Against {self.source.label}'s measured per-case variance "
            f"({self.source.contrast}, {self.source.n_cases} paired cases at "
            f"num_runs={self.source.k_observed}), a design of {self.n_cases} cases at "
            f"num_runs={self.num_runs} resolves {min(values):.4f}-{max(values):.4f} "
            f"across {len(values)} metrics -- and nothing smaller."
        )

    def cases_needed(self, target: float = REFERENCE_EFFECT_CONTRAST) -> dict[str, int]:
        """Cases this design would need per metric to reach ``target``."""
        return {
            metric: comp.cases_for(target, self.num_runs)
            for metric, comp in self.source.components.items()
        }

    def underpowered(self, target: float = REFERENCE_EFFECT_CONTRAST) -> list[str]:
        """Metrics whose MDE exceeds ``target`` -- i.e. cannot resolve it."""
        return sorted(m for m, mde in self.per_metric.items() if mde > target)

    def render(self, *, target: float = REFERENCE_EFFECT_CONTRAST) -> list[str]:
        """The block a caller prints. Provenance and caveats are not optional.

        A bare table of MDEs invites being quoted as a property of the metrics.
        Every line the table needs to be read honestly is emitted with it.
        """
        needed = self.cases_needed(target)
        header = (
            f"design: {self.n_cases} cases at num_runs={self.num_runs}  "
            f"(alpha={ALPHA}, power={POWER:.2f} two-sided, target +-{target:.4f})"
        )
        lines = [
            header,
            "",
            *textwrap.wrap(self.provenance, width=_WRAP),
            "",
            f"  {'metric':27}{'MDE':>9}{'verdict':>15}{'cases needed':>14}",
        ]
        for metric in sorted(self.per_metric):
            mde = self.per_metric[metric]
            verdict = "resolves" if mde <= target else "UNDERPOWERED"
            lines.append(f"  {metric:27}{mde:9.4f}{verdict:>15}{needed[metric]:>14d}")
        lines.append("")
        for caveat in self.source.caveats:
            wrapped = textwrap.wrap(caveat, width=_WRAP - 4)
            lines += [f"  - {wrapped[0]}"] + [f"    {line}" for line in wrapped[1:]]
        return lines


def metrics_in(sides: Mapping[tuple[str, str], EvalSide]) -> list[str]:
    """Metric names scored on every side, read off the artifacts themselves.

    Not a hardcoded list: a metric added to the eval config and absent from
    this module would be silently dropped from a design check, which reads as
    "that metric is fine".
    """
    shared: set[str] | None = None
    for side in sides.values():
        for row in side.per_case.values():
            keys = {k for k, v in row.items() if k != "case_index" and isinstance(v, int | float)}
            shared = keys if shared is None else shared & keys
            break
    return sorted(shared or set())


def load_eval_sides(
    stages_dir: str | Path,
    arms: Sequence[str],
    phases: Sequence[str] = C09_PHASES,
) -> dict[tuple[str, str], EvalSide]:
    """Read ``<stages_dir>/<phase>/<arm>.json`` stage artifacts as `EvalSide`s.

    Raises rather than substituting anything for a missing file: a design check
    that quietly fell back to an assumed variance would produce exactly the
    confident-looking number this module exists to prevent.
    """
    root = Path(stages_dir)
    sides: dict[tuple[str, str], EvalSide] = {}
    for phase in phases:
        for arm in arms:
            path = root / phase / f"{arm}.json"
            if not path.is_file():
                raise FileNotFoundError(
                    f"missing eval stage artifact {path}; the MDE needs per-case scores "
                    f"from a real run, and there is nothing to assume in its place"
                )
            payload = json.loads(path.read_text())
            rows = payload.get("per_case") or []
            if not rows:
                raise ValueError(f"{path} has no per_case rows; the MDE needs per-case scores")
            sides[(phase, arm)] = EvalSide(
                arm=arm,
                phase=phase,
                per_case={int(row["case_index"]): row for row in rows},
                scores_std=payload.get("scores_std") or {},
                num_runs=int(payload.get("num_runs") or 1),
                score_repeats=int(payload.get("score_repeats") or 1),
            )
    return sides


def measured_variance(
    sides: Mapping[tuple[str, str], EvalSide],
    *,
    treatment: str,
    baseline: str,
    label: str,
    caveats: Sequence[str] = (),
    origin: str = "",
    n_sides: int = 4,
) -> VarianceSource:
    """Per-case variance of a ``treatment - baseline`` contrast, per metric.

    The sigma^2 estimate is the **within-window** one, which credits `num_runs`
    with the least of the two estimators and so gives the *worst* MDE at
    ``K > K_OBSERVED``. That choice cannot touch a reading at
    ``K = K_OBSERVED``, where the bracket is identically the measured total
    (module docstring), so it only makes the "would more runs help?" answer
    conservative.
    """
    recorded = {side.num_runs for side in sides.values()}
    if recorded != {K_OBSERVED}:
        raise ValueError(
            f"every side must record num_runs={K_OBSERVED}, got {sorted(recorded)}: "
            f"`within_window_measurement` normalises sigma^2 by K_OBSERVED, so sides "
            f"run at another num_runs would be silently mis-scaled"
        )
    cases = common_cases(sides)
    components = {}
    for metric in metrics_in(sides):
        total = _variance(per_case_contrast(sides, treatment, baseline, metric, cases))
        measurement = within_window_measurement(sides, metric, n_sides=n_sides)
        components[metric] = variance_components(
            total=total, measurement_at_k_observed=measurement, k_observed=K_OBSERVED
        )
    return VarianceSource(
        label=label,
        contrast=f"{treatment} - {baseline}",
        n_cases=len(cases),
        k_observed=K_OBSERVED,
        components=components,
        caveats=tuple(caveats),
        origin=origin,
    )


#: Why campaign 09's variance is not a property of anything. Printed with every
#: MDE derived from it.
C09_CAVEATS = (
    "campaign 09 is ONE RUN PER CONDITION, so each variance estimate carries that run's luck",
    (
        "a floor is not a property of a metric -- re-measure it on the day, on this "
        "design (CLAUDE.md; campaign 09's control drifted 9x DOE 03's figure for the "
        "same metric)"
    ),
    (
        "this bounds CASE-SAMPLING noise only. Which prompt GEPA happens to find is "
        "the larger term: two runs of one manifest have differed by 12.3x the control "
        "floor"
    ),
)


def campaign_09_variance(stages_dir: str | Path = C09_STAGES_DIR) -> VarianceSource:
    """The only measured per-case variance this repo has.

    Six eval sides from campaign 09, paired on ``case_index``; the contrast is
    rationale on minus off, which is the four-sided shape of a two-arm campaign
    readout. Everything a caller needs to say *"against campaign 09's measured
    variance"* rather than *"the variance"* comes back attached.
    """
    sides = load_eval_sides(stages_dir, (C09_CONTROL, C09_TREATMENT, C09_BASELINE))
    return measured_variance(
        sides,
        treatment=C09_TREATMENT,
        baseline=C09_BASELINE,
        label="campaign 09",
        caveats=C09_CAVEATS,
        origin=str(stages_dir),
    )


def mde_for_design(*, n_cases: int, num_runs: int, variance_source: VarianceSource) -> DesignMDE:
    """The smallest effect a design of ``n_cases`` at ``num_runs`` could detect.

    ``variance_source`` is required and has no default: the answer is only ever
    "against *this* measured variance", and a default would let a campaign read
    one run's luck as a property of its design. :func:`campaign_09_variance` is
    currently the only measured source that exists.
    """
    if n_cases <= 0:
        raise ValueError(f"n_cases must be a positive case count, got {n_cases}")
    if num_runs <= 0:
        raise ValueError(f"num_runs must be a positive repeat count, got {num_runs}")
    return DesignMDE(
        n_cases=n_cases,
        num_runs=num_runs,
        source=variance_source,
        per_metric={
            metric: comp.mde(n_cases, num_runs)
            for metric, comp in variance_source.components.items()
        },
    )
