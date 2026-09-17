"""Can GEPA's merge operator fire on our candidates at all? Check, do not assume.

`use_merge=True` was injected by patch 7 on 2026-09-17 on the strength of a published
claim -- GEPA+Merge gives prompts up to 9.2x shorter while scoring higher -- and prompt
length was the mechanism we had hypothesised for the holdout regression. The m01 run then
logged **19 x "No merge candidates found" and zero successful merges** across 113
generations, so the flag was doing nothing. This script establishes *why*, from a real
run_dir, because "it did not fire on one run" and "it cannot fire" are different findings
and only the second one settles whether the flag is worth keeping.

**The mechanism.** Merge here is *field-wise recombination across multiple predictors*:
take predictor A from one parent and predictor B from another, both descending from a
common ancestor. It does not ask an LLM to blend two prompts -- it selects whole fields.
`gepa.proposer.merge.does_triplet_have_desirable_predictors` requires, for at least one
predictor:

    (pred_ancestor == pred_parent1 or pred_ancestor == pred_parent2)
        and pred_parent1 != pred_parent2

i.e. **one parent must have left that predictor byte-identical to the ancestor** while the
other changed it. That is what makes recombining fields meaningful.

**Why that is unsatisfiable for us.** `GEPARootAgentPromptOptimizer` optimizes the root
agent's instruction and nothing else, so a candidate is a dict with exactly one key,
`agent_prompt`. With a single predictor the test demands a descendant whose prompt is
byte-identical to its ancestor's -- but a descendant exists *because* the prompt was
mutated. Measured on a real 14-candidate run: 34 pairs considered, all 34 sharing a common
ancestor, **0** passing the test, and **0** byte-identical candidate pairs.

So the 9.2x figure does not transfer. It comes from multi-module DSPy-style programs that
have separate prompts to recombine; single-prompt optimization gives this operator nothing
to do.

**Leaving the flag on costs ~nothing** -- when `propose()` returns `None` the engine falls
through to the reflective proposer in the *same* iteration, so no evaluation budget is
consumed -- and it becomes correct automatically if ADK ever optimizes sub-agent
instructions too. That is the condition to re-run this under: **more than one predictor.**

Usage:
    uv run python scripts/check_merge_eligibility.py outputs/gepa_runs/sonnet_opt
    uv run python scripts/check_merge_eligibility.py gs://.../stages/optimize/gepa_run/<arm>
"""

from __future__ import annotations

import argparse
import itertools
import json
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

CANDIDATES_FILE = "candidates.json"
STATE_FILE = "gepa_state.bin"


def fetch(run_dir: str) -> Path:
    """Local path for a run dir, downloading from GCS if needed."""
    if not run_dir.startswith("gs://"):
        return Path(run_dir)
    tmp = Path(tempfile.mkdtemp(prefix="gepa_merge_"))
    subprocess.run(["gsutil", "-q", "cp", "-r", run_dir.rstrip("/") + "/*", str(tmp)], check=False)
    return tmp


def load(run_dir: Path) -> tuple[list[dict], list]:
    """(candidates, parent lineage). Raises rather than returning empty."""
    cand_path, state_path = run_dir / CANDIDATES_FILE, run_dir / STATE_FILE
    if not cand_path.is_file():
        raise FileNotFoundError(f"no {CANDIDATES_FILE} under {run_dir}")
    if not state_path.is_file():
        raise FileNotFoundError(
            f"no {STATE_FILE} under {run_dir}; the lineage lives there and the eligibility "
            f"test needs it. Runs predating the 2026-09-16 upload do not have it."
        )
    candidates = json.loads(cand_path.read_text())
    # S301: our own artifact, written by our own optimize container into our own bucket.
    raw = pickle.loads(state_path.read_bytes())  # noqa: S301
    state = raw if isinstance(raw, dict) else raw.__dict__
    return candidates, state.get("parent_program_for_candidate") or []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", help="local path or gs:// prefix of a GEPA run_dir")
    args = ap.parse_args()

    from gepa.proposer.merge import does_triplet_have_desirable_predictors as eligible

    try:
        candidates, parents = load(fetch(args.run_dir))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not candidates or not parents:
        print("error: run produced no candidates or no lineage", file=sys.stderr)
        return 2

    predictors = list(candidates[0].keys())
    print(f"\n  candidates: {len(candidates)}")
    print(f"  predictors per candidate: {len(predictors)}  {predictors}")

    def ancestors(node: int, seen: set[int]) -> set[int]:
        for parent in parents[node] or []:
            if parent is not None and parent not in seen:
                seen.add(parent)
                ancestors(parent, seen)
        return seen

    considered = shared = passing = 0
    for i, j in itertools.combinations(range(len(candidates)), 2):
        anc_i, anc_j = ancestors(i, set()), ancestors(j, set())
        if j in anc_i or i in anc_j:
            continue  # gepa skips pairs where one is an ancestor of the other
        considered += 1
        common = anc_i & anc_j
        if common:
            shared += 1
        if any(eligible(candidates, a, i, j) for a in common):
            passing += 1

    identical = sum(
        1
        for i, j in itertools.combinations(range(len(candidates)), 2)
        if candidates[i] == candidates[j]
    )

    print(f"\n  pairs considered:                    {considered}")
    print(f"  ...sharing a common ancestor:        {shared}")
    print(f"  ...passing the predictor test:       {passing}")
    print(f"  byte-identical candidate pairs:      {identical}")

    if len(predictors) == 1 and passing == 0:
        print(
            "\n  INERT, structurally. One predictor means the test needs a descendant whose\n"
            "  prompt is byte-identical to its ancestor's, and a descendant exists because\n"
            "  the prompt was mutated. use_merge cannot fire in this configuration; keeping\n"
            "  it on is harmless (a failed attempt consumes no budget) but buys nothing, and\n"
            "  the published 9.2x-shorter-prompt figure does not transfer."
        )
    elif passing:
        print(
            f"\n  LIVE: {passing} pair(s) are eligible. Merge can fire here, so re-read the\n"
            "  docs that call it inert -- they were written against a single-predictor run."
        )
    else:
        print(
            f"\n  {len(predictors)} predictors but 0 eligible pairs. Not the structural case;\n"
            "  this run simply produced no recombinable pair. Do not generalise from it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
