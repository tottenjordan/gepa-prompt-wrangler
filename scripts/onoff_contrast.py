"""Paired per-case readout of the on/off validation (run-413630e488).

Reproduces every number in docs/analysis/2026-09-26-onoff-validation-result.md from the
run's stage artifacts. Same method as the campaign 09 reanalysis: pair on ``case_index``,
restrict to cases scored on every side, bootstrap over cases with 10,000 resamples.

    gsutil -m cp -r gs://$GCP_STAGING_BUCKET/pipeline-runs/run-413630e488/stages /tmp/onoff/
    uv run python scripts/onoff_contrast.py /tmp/onoff/stages
"""

from __future__ import annotations

import argparse
import random
import statistics as st
from typing import TYPE_CHECKING

from wrangler.reporting.inference import (
    common_cases,
    load_eval_sides,
    metrics_in,
    per_case_contrast,
    per_case_delta,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

ON, OFF, CONTROL = "onoff-continuous", "onoff-binary", "onoff-control"
RESAMPLES = 10_000


def bootstrap(values: Sequence[float], *, seed: int = 0) -> tuple[float, float, float]:
    """Mean and 95% percentile interval, resampling cases."""
    rng = random.Random(seed)
    means = sorted(st.mean(rng.choices(values, k=len(values))) for _ in range(RESAMPLES))
    return st.mean(values), means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def line(label: str, values: Sequence[float]) -> str:
    mean, lo, hi = bootstrap(values)
    resolved = "  *" if lo > 0 or hi < 0 else ""
    return f"  {label:32s} {mean:+.4f}  [{lo:+.4f}, {hi:+.4f}]{resolved}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stages_dir")
    args = parser.parse_args()

    sides = load_eval_sides(args.stages_dir, [ON, OFF, CONTROL], ("eval_before", "eval_after"))
    cases = common_cases(sides)
    print(f"{len(cases)} cases scored on all six sides; * = 95% CI excludes zero")
    for metric in metrics_in(sides):
        print(f"\n{metric}")
        for arm in (ON, OFF, CONTROL):
            print(line(f"after - before  {arm}", per_case_delta(sides, arm, metric, cases)))
        contrast = per_case_contrast(sides, ON, OFF, metric, cases)
        print(line("continuous - binary (primary)", contrast))
        print(line("continuous - control", per_case_contrast(sides, ON, CONTROL, metric, cases)))
        print(line("binary - control", per_case_contrast(sides, OFF, CONTROL, metric, cases)))
        better = sum(x > 0 for x in contrast)
        worse = sum(x < 0 for x in contrast)
        print(
            f"  cases continuous better / worse / tied: {better} / {worse} / {len(contrast) - better - worse}"
        )


if __name__ == "__main__":
    main()
