#!/usr/bin/env python
"""What would early stopping have cost past campaigns?

Replays gepa's own `NoImprovementStopper` at a range of patience values against
archived optimize runs, and reports for each: where it would have fired, how much of
the metric-call budget that saves, and — the question that actually matters — whether
the candidate GEPA returns is still the same one.

Reads the committed trajectory fixtures by default, so it runs with no GCS access:

    uv run python scripts/replay_stopping.py

To add a campaign, pull its state files and extract trajectories first:

    gcloud storage cp \\
      "gs://$GCP_STAGING_BUCKET/pipeline-runs/<run-id>/stages/optimize/gepa_run/<arm>/gepa_state.bin" \\
      /tmp/<arm>.bin
    uv run python scripts/replay_stopping.py --extract /tmp/<arm>.bin --out tests/fixtures/<set>/

`gepa_state.bin` is a pickle of gepa's internals and may not survive a gepa bump; the
extracted trajectory is JSON and is the thing meant to last. See
`wrangler/optimize/trajectory.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrangler.optimize.trajectory import (
    DEFAULT_PATIENCES,
    load_state,
    load_trajectory,
    replay,
    save_trajectory,
    to_trajectory,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "c09_trajectories"


def _report(traj) -> bool:
    """Print one arm's tradeoff table. Returns True if some patience is lossless."""
    best_score = traj.scores[traj.best_idx]
    print(f"\n{traj.arm}")
    print(
        f"  {traj.total_iterations} iterations, {traj.total_calls} metric calls, "
        f"{len(traj.scores)} candidates, {traj.val_subset_size}-case validation subset"
    )
    print(
        f"  seed {traj.scores[0]:.4f} -> best {best_score:.4f} "
        f"(candidate {traj.best_idx}, first of {traj.scores.count(best_score)} at that score)"
    )
    if best_score >= 1.0:
        print("  NOTE: the validation subset is SATURATED -- the selection signal is at its")
        print("        ceiling, so it cannot rank candidates for the rest of the run.")

    header = f"  {'patience':>8} {'stops@':>7} {'calls':>7} {'saved':>7} {'returns':>8} {'score':>7}  verdict"
    print(header)
    lossless = False
    for patience in DEFAULT_PATIENCES:
        point = replay(traj, patience)
        saved = 1 - point.calls_at_stop / traj.total_calls if traj.total_calls else 0.0
        where = str(point.stop_iteration) if point.fired else "never"
        verdict = "same prompt" if point.same_candidate else "DIFFERENT prompt"
        lossless = lossless or (point.same_candidate and point.fired)
        print(
            f"  {patience:>8} {where:>7} {point.calls_at_stop:>7} {saved:>6.1%} "
            f"{point.best_idx_at_stop:>8} {point.best_score_at_stop:>7.4f}  {verdict}"
        )
    return lossless


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectories", nargs="*", help="trajectory JSON files (default: c09)")
    parser.add_argument("--extract", nargs="+", default=[], help="gepa_state.bin files to convert")
    parser.add_argument("--out", default="", help="directory to write extracted trajectories to")
    args = parser.parse_args(argv)

    if args.extract:
        if not args.out:
            parser.error("--extract requires --out")
        for raw in args.extract:
            arm = Path(raw).stem
            path = save_trajectory(
                to_trajectory(load_state(raw), arm=arm), Path(args.out) / f"{arm}.json"
            )
            print(f"  wrote {path}")
        return 0

    paths = [Path(p) for p in args.trajectories] or sorted(FIXTURES.glob("*.json"))
    if not paths:
        print("no trajectories found", file=sys.stderr)
        return 2

    print("Early-stopping replay -- gepa's own NoImprovementStopper, archived runs")
    print("=" * 78)
    all_lossless = True
    for path in paths:
        all_lossless &= _report(load_trajectory(path))

    print(
        "\nRead this as a budget question, not a quality one: 'same prompt' means the\n"
        "candidate GEPA returns is byte-identical, so the saving is free. A patience that\n"
        "returns a DIFFERENT prompt may still be fine -- but nothing here can say so, "
        "because\nthe validation subset that ranked them has already hit its ceiling."
    )
    return 0 if all_lossless else 1


if __name__ == "__main__":
    raise SystemExit(main())
