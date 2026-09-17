"""DOE 03: which knob buys resolution, `num_runs` or `score_repeats`?

Reads the pool collected by `scripts/doe03_collect.py` -- N captures from one engine, each
scored M times -- and rebuilds every (`num_runs` r, `score_repeats` s) cell **offline**. A
control-arm delta at (r, s) is constructed the way production constructs an eval:

    pick two DISJOINT sets of r captures       -> "before" and "after"
    per capture: combine_results(s of its M scorings)    # the score_repeats step
    per side:    combine_results(the r capture results)  # the num_runs step
    delta = after - before, per metric

**Both steps call `combine_results`, which is the function production calls** -- the
`score_repeats` path directly, and the `num_runs` path since 2026-09-17. That is the whole
validity argument for resampling: the cells are not a model of production averaging, they
are production averaging run over stored inputs.

## What this can and cannot say

**Cells are not independent.** Every one is built from the same N captures, so these are
sampling distributions *conditional on this pool*. Two cells differing is informative about
the knobs; the absolute width of any one cell carries the pool's luck with it.

**The max is draw-count dependent, so it is not the headline.**
`measure_noise_floor_per_metric` uses `max(|delta|)` over a handful of real control arms.
A max over 10 resampled draws and a max over 1,000 are different statistics -- the second
is larger for arithmetic reasons, not physical ones. This reports **median, p95 and max**
with the draw count beside them, and comparisons against CLAUDE.md's remembered floors
(~0.058 at num_runs=1, ~0.011-0.014 at 3) must be made against a matched draw count.

## Pre-registered predictions (DOE 02)

Scoring byte-identical responses, the judge disagreed with itself on 64/64 cases for
`instruction_following_v1` and **0/64** for `safety_v1`. So at equal cost:

    instruction_following_v1 -> (1,5) should beat (3,1)   [judge-dominated]
    safety_v1                -> (3,1) should beat (1,5)   [agent-dominated]

Two metrics, opposite directions, same data. **If safety_v1's floor falls with
`score_repeats`, DOE 02's judge/agent split is wrong** and the guidance now in CLAUDE.md
has to be withdrawn.

Usage:
    uv run python scripts/analyze_doe03.py
    uv run python scripts/analyze_doe03.py --max-draws 200 --pool outputs/doe03
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

# Imported, not redeclared: `wrangler/reporting/analyzer.py` is the single place these
# measurements live, so the script and the shipped floor model cannot price the iso-cost
# contrasts differently.
from wrangler.reporting.analyzer import (
    CAPTURE_MIN,
    SCORING_MIN,
    arm_side_cost_min,
)

COST_MEASURED = "2026-09-16 (DOE 02), 64 cases"

# Below this many disjoint splits a cell is reported as UNDERPOWERED rather than as a
# number, in the spirit of analyze_toolset_loss.py refusing to call a truncated query clean.
MIN_SPLITS = 8

# The contrasts the campaign exists to settle: cells of ~equal cost, different knobs.
ISO_COST_SETS = [
    ("~11 min", [(2, 1), (1, 3)]),
    ("~16.5 min", [(3, 1), (2, 2), (1, 5)]),
]

PREDICTIONS = {
    "instruction_following_v1": (1, 5),
    "safety_v1": (3, 1),
}


def load_pool(pool_dir: Path) -> list[list]:
    """[[EvalResult per scoring] per capture]. Raises on a short or ragged grid."""
    from wrangler.eval.evaluator import EvalResult

    manifest_path = pool_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest at {manifest_path}; run scripts/doe03_collect.py first")
    manifest = json.loads(manifest_path.read_text())

    grid: list[list] = []
    widths = set()
    for key in sorted(manifest["captures"], key=lambda k: int(k.lstrip("c"))):
        entry = manifest["captures"][key]
        row = []
        for pass_key in sorted(entry["passes"], key=lambda k: int(k.removeprefix("pass"))):
            payload = json.loads(Path(entry["passes"][pass_key]["file"]).read_text())
            row.append(
                EvalResult(
                    scores=payload["scores"],
                    per_case=payload["per_case"],
                    coverage=payload.get("coverage") or {},
                )
            )
        widths.add(len(row))
        grid.append(row)

    if len(widths) > 1:
        # A ragged grid biases every quantile toward whichever captures were scored more.
        raise SystemExit(f"ragged pool: captures have differing scoring counts {sorted(widths)}")
    if len(grid) < 2:
        raise SystemExit(f"{len(grid)} capture(s); need >= 2 for a disjoint split")
    return grid


def side(grid: list[list], captures: tuple[int, ...], s: int, rng: random.Random):
    """Build one side of a control arm at (num_runs=len(captures), score_repeats=s)."""
    from wrangler.eval.evaluator import combine_results

    per_capture = []
    for c in captures:
        scorings = grid[c]
        chosen = rng.sample(scorings, s) if s < len(scorings) else scorings
        per_capture.append(combine_results(chosen, label="scoring passes"))
    return combine_results(per_capture, label="runs")


def splits(n: int, r: int) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Every unordered pair of disjoint r-subsets of range(n)."""
    out = []
    seen = set()
    for before in itertools.combinations(range(n), r):
        rest = [i for i in range(n) if i not in before]
        for after in itertools.combinations(rest, r):
            key = frozenset((before, after))
            if key in seen:
                continue
            seen.add(key)
            out.append((before, after))
    return out


def cell(grid: list[list], r: int, s: int, max_draws: int, seed: int = 0) -> dict:
    """Per-metric |delta| distribution at (r, s), paired and unpaired."""
    from wrangler.eval.evaluator import paired_deltas

    rng = random.Random(seed)
    all_splits = splits(len(grid), r)
    n_available = len(all_splits)
    if n_available > max_draws:
        all_splits = rng.sample(all_splits, max_draws)

    unpaired: dict[str, list[float]] = {}
    paired: dict[str, list[float]] = {}
    n_paired: list[int] = []
    for before_ids, after_ids in all_splits:
        before = side(grid, before_ids, s, rng)
        after = side(grid, after_ids, s, rng)
        for metric in set(before.scores) & set(after.scores):
            unpaired.setdefault(metric, []).append(
                abs(after.scores[metric] - before.scores[metric])
            )
        # paired_deltas returns {"n_paired", "deltas", "dropped_before", "dropped_after"};
        # only cases BOTH sides scored contribute, which is the comparison worth reporting.
        pd = paired_deltas(before.per_case, after.per_case)
        for metric, value in pd["deltas"].items():
            paired.setdefault(metric, []).append(abs(value))
        n_paired.append(pd["n_paired"])

    return {
        "r": r,
        "s": s,
        "cost_min": arm_side_cost_min(r, s),
        "draws": len(all_splits),
        "available": n_available,
        "unpaired": unpaired,
        "paired": paired,
        "mean_cases_paired": st.mean(n_paired) if n_paired else 0.0,
    }


def summarize(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "median": st.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max": ordered[-1],
        "n": len(ordered),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", default="outputs/doe03")
    ap.add_argument("--max-draws", type=int, default=200, help="cap on splits per cell")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    grid = load_pool(Path(args.pool))
    n_captures, n_scorings = len(grid), len(grid[0])
    print(f"\n  Pool: {n_captures} captures x {n_scorings} scorings")
    print(f"  Cost model: {CAPTURE_MIN} min/capture + {SCORING_MIN} min/scoring — {COST_MEASURED}")

    wanted = sorted({rs for _, pairs in ISO_COST_SETS for rs in pairs} | {(1, 1)})
    cells = {}
    for r, s in wanted:
        if 2 * r > n_captures or s > n_scorings:
            print(f"  skipping (r={r}, s={s}): pool too small")
            continue
        cells[(r, s)] = cell(grid, r, s, args.max_draws, seed=args.seed)

    metrics = sorted({m for c in cells.values() for m in c["unpaired"]})

    header = f"  {'metric':34} " + " ".join(f"{f'({r},{s})':>22}" for r, s in cells)
    for kind in ("unpaired", "paired"):
        print(f"\n  {kind.upper()} |delta| per cell — median / p95 / max")
        print(header)
        for metric in metrics:
            row = [f"  {metric:34}"]
            for c in cells.values():
                values = c[kind].get(metric)
                if not values or c["available"] < MIN_SPLITS:
                    row.append(f"{'UNDERPOWERED':>22}")
                    continue
                stats = summarize(values)
                row.append(f"{stats['median']:.4f}/{stats['p95']:.4f}/{stats['max']:.4f}".rjust(22))
            print(" ".join(row))

    # Pairing's value, shown rather than asserted. CLAUDE.md records it as "~15% when evals
    # dropped cases, and no longer material at 100% coverage" -- this is the number behind
    # that claim, per cell, on this pool.
    print("\n  PAIRING GAIN — 1 - (paired median / unpaired median); >0 means pairing helps")
    print(header)
    for metric in metrics:
        row = [f"  {metric:34}"]
        for c in cells.values():
            up, pa = c["unpaired"].get(metric), c["paired"].get(metric)
            if not up or not pa or c["available"] < MIN_SPLITS:
                row.append(f"{'—':>22}")
                continue
            u, p = summarize(up)["median"], summarize(pa)["median"]
            row.append(f"{(1 - p / u) if u else 0.0:+.1%}".rjust(22))
        print(" ".join(row))

    draw_row = " ".join(f"{'n=' + str(c['draws']):>22}" for c in cells.values())
    print(f"  {'draws (of available)':34} {draw_row}")
    avail_row = " ".join(f"{'of ' + str(c['available']):>22}" for c in cells.values())
    print(f"  {'':34} {avail_row}")
    paired_row = " ".join(f"{round(c['mean_cases_paired'], 1)!s:>22}" for c in cells.values())
    print(f"  {'mean cases paired':34} {paired_row}")

    print("\n  ISO-COST VERDICT — at equal budget, which knob is tighter (median |delta|)")
    for label, pairs in ISO_COST_SETS:
        present = [p for p in pairs if p in cells]
        if len(present) < 2:
            continue
        print(
            f"\n  {label}: "
            + ", ".join(f"(r={r},s={s}) {arm_side_cost_min(r, s):.1f}min" for r, s in present)
        )
        for metric in metrics:
            scored = {
                p: summarize(cells[p]["unpaired"][metric])["median"]
                for p in present
                if cells[p]["unpaired"].get(metric)
            }
            if not scored:
                continue
            best = min(scored, key=lambda k: scored[k])
            predicted = PREDICTIONS.get(metric)
            verdict = ""
            if predicted in present:
                verdict = "  MATCHES prediction" if best == predicted else "  AGAINST prediction"
            detail = "  ".join(f"({r},{s})={v:.4f}" for (r, s), v in scored.items())
            print(f"    {metric:34} best=(r={best[0]},s={best[1]})  {detail}{verdict}")

    # Old DOE 03's question, answered from the same pool: does the floor fall as sqrt(n)?
    # Fitted, never assumed -- minimum_detectable_effect() refuses to extrapolate without a
    # measured exponent precisely so this number has to come from data.
    print("\n  SCALING EXPONENT — floor ~ k * n^-e, fitted 1 -> 3 at the other knob = 1")
    print(f"  {'metric':34}{'num_runs e':>14}{'score_repeats e':>18}")
    for metric in metrics:
        row = [f"  {metric:34}"]
        for base, hi in ((("r", 1, 1), ("r", 3, 1)), (("s", 1, 1), ("s", 1, 5))):
            lo_key = (base[1], base[2])
            hi_key = (hi[1], hi[2])
            lo = cells.get(lo_key, {}).get("unpaired", {}).get(metric)
            high = cells.get(hi_key, {}).get("unpaired", {}).get(metric)
            if not lo or not high:
                row.append(f"{'—':>14}")
                continue
            a, b = summarize(lo)["median"], summarize(high)["median"]
            n = hi_key[0] if base[0] == "r" else hi_key[1]
            exp = (math.log(a / b) / math.log(n)) if a > 0 and b > 0 else 0.0
            row.append(f"{exp:>14.2f}")
        print("".join(row))
    print(
        "  e=0.5 is sqrt(n); e=0 means the knob buys nothing; e<0 means it got WORSE,\n"
        "  which at these draw counts means the two cells are not distinguishable."
    )

    print(
        "\n  READ BEFORE QUOTING:\n"
        "  - Cells are resampled from ONE pool and are NOT independent. These are sampling\n"
        "    distributions conditional on this pool.\n"
        "  - The max grows with the draw count. Compare medians across cells; compare a max\n"
        "    to CLAUDE.md's remembered floors only at a matched number of draws.\n"
        "  - This is WITHIN-engine noise. It excludes the deployment lottery DOE 01 measured\n"
        "    at 0-100% reach, so it is the wrong number for 'how much does an arm vary'."
    )
    if any(c["available"] < MIN_SPLITS for c in cells.values()):
        print(
            f"\n  Some cells had fewer than {MIN_SPLITS} disjoint splits and are reported as\n"
            f"  UNDERPOWERED rather than as a number.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
