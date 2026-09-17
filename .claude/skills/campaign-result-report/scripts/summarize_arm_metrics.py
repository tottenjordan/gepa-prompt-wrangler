#!/usr/bin/env python3
"""Per-metric deltas for every arm in a campaign, classified against the noise floor.

Used by the `campaign-result-report` skill so a report quotes computed numbers rather than
eyeballed ones -- and, more importantly, so it quotes the SAME numbers the report generator
and the analyzer produce. Every statistic here comes from `wrangler.reporting.analyzer`;
nothing is re-implemented locally.

That is deliberate. A summary script with its own copy of "what a delta is" will eventually
disagree with `wrangler report`, and the disagreement will surface in a published document.

    uv run python .claude/skills/campaign-result-report/scripts/summarize_arm_metrics.py \
        experiments/active/<name>

    # JSON for further processing
    uv run python .../summarize_arm_metrics.py experiments/active/<name> --json

**What it will not do:** invent a floor. With no control arm it prints `UNCALIBRATED` and
refuses to classify, because CLAUDE.md requires a control arm in every sweep and a report
that classifies without one is asserting a precision it does not have.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_stage(exp_dir: Path, stage: str) -> dict:
    """Read one stage artifact. Missing is empty -- a stage that never ran is not an error."""
    path = exp_dir / "stages" / f"{stage}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def build_pairs(exp_dir: Path) -> list:
    """Reconstruct PairAnalysis objects from stage artifacts on disk.

    Mirrors `analyze_experiment()` but reads the directory directly, so the script works on a
    finished campaign without needing the Experiment object or a live GCP session.
    """
    from wrangler.reporting.analyzer import PairAnalysis

    before = load_stage(exp_dir, "eval_before")
    after = load_stage(exp_dir, "eval_after")
    deploy = load_stage(exp_dir, "deploy")
    optimize = load_stage(exp_dir, "optimize")

    pairs = []
    for pair_id in sorted(set(before) | set(after)):
        b, a = before.get(pair_id, {}), after.get(pair_id, {})
        pairs.append(
            PairAnalysis(
                pair_id=pair_id,
                model=deploy.get(pair_id, {}).get("model", ""),
                before=b.get("scores", {}),
                after=a.get("scores", {}),
                before_per_case=b.get("per_case", []),
                after_per_case=a.get("per_case", []),
                original_prompt=deploy.get(pair_id, {}).get("original_prompt", ""),
                optimized_prompt=optimize.get(pair_id, {}).get("optimized_prompt", ""),
            )
        )
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_dir", help="e.g. experiments/active/<name>")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    from wrangler.reporting.analyzer import classify_deltas, measure_noise_floor_per_metric

    exp_dir = Path(args.experiment_dir)
    if not (exp_dir / "stages").is_dir():
        print(f"error: no stages/ under {exp_dir}", file=sys.stderr)
        return 2

    pairs = build_pairs(exp_dir)
    if not pairs:
        print("error: no pairs with eval results", file=sys.stderr)
        return 2

    # Per-metric, never pooled: the 2026-09-02 control arm spanned 0.0028 to 0.0747, a 27x
    # range, so one number would be wrong for almost every metric.
    floors = measure_noise_floor_per_metric(pairs)
    controls = [p.pair_id for p in pairs if p.is_control]

    payload = {
        "experiment": exp_dir.name,
        "control_arms": controls,
        "floors": floors,
        "arms": {},
    }
    for pair in pairs:
        labels = classify_deltas(pair, floors)
        payload["arms"][pair.pair_id] = {
            "is_control": pair.is_control,
            "num_runs_before": load_stage(exp_dir, "eval_before")
            .get(pair.pair_id, {})
            .get("num_runs"),
            "deltas": {m: round(d, 4) for m, d in sorted(pair.deltas.items())},
            "classification": labels,
            "paired": pair.paired.get("deltas", {}),
            "n_paired": pair.paired.get("n_paired", 0),
        }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if not controls:
        print(
            "UNCALIBRATED — no control arm, so nothing below can be called an improvement.\n"
            "CLAUDE.md requires an arm whose prompt does not change. Report the deltas as\n"
            "uncalibrated rather than classifying them.\n"
        )
    else:
        print(f"\nControl arm(s): {', '.join(controls)}")
        print("Per-metric floor (largest movement on an unchanged prompt):")
        for metric, value in sorted((floors or {}).items()):
            print(f"  {metric:34} {value:.4f}")

    for pair_id, arm in payload["arms"].items():
        tag = "  [CONTROL]" if arm["is_control"] else ""
        print(f"\n{pair_id}{tag}   paired over {arm['n_paired']} cases")
        print(f"  {'metric':34}{'delta':>10}  {'verdict':<14} paired")
        for metric, delta in arm["deltas"].items():
            verdict = arm["classification"].get(metric, "uncalibrated")
            paired = arm["paired"].get(metric)
            shown = f"{paired:+.4f}" if isinstance(paired, int | float) else "—"
            print(f"  {metric:34}{delta:>+10.4f}  {verdict:<14} {shown}")

    print(
        "\nREAD BEFORE QUOTING:\n"
        "  - 'within-noise' is not 'no effect'. It means this campaign could not resolve it;\n"
        "    quote the floor beside the number so the reader can see the bar.\n"
        "  - Floors here come from THIS run's control arm. Do not reuse a floor measured on\n"
        "    an earlier run -- dropout varies with load and with how many arms ran at once.\n"
        "  - A delta must clear the floor AND the run-to-run spread. The floor bounds\n"
        "    evaluation noise only; GEPA's search is stochastic on top of it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
