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

import math
import statistics as st
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- Design constants -------------------------------------------------------

ALPHA = 0.05
POWER = 0.80

#: Campaign 09 ran `num_runs: 2`, so every variance read off it is a K = 2
#: measurement. Re-apportioning to other K is only meaningful relative to this.
K_OBSERVED = 2


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
