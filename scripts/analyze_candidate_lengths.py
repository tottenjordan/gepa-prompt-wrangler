"""Does prompt length track GEPA's score, within a single optimize run?

Written because the opposite claim was made without checking. On 2026-09-16 a merged
document asserted that prompt-length regularization was "better evidenced" than the writer
model choice, on the strength of GEPA's published verbosity-overfitting failure mode plus
campaign 07 growing its prompt 78 -> 3,873 characters. Tested against the seven arms in this
repo's own bucket, the correlation between optimized length and the holdout delta **flips
sign depending on the subset** and is ~0 on the only comparable cohort. The claim was wrong.

Arm-level data cannot settle it: one nine-hour stage yields **one** (length, delta) pair.
GEPA's own run_dir yields one per *candidate* -- 14 on the surviving local run, spanning 78
to 12,741 characters. That is what this reads.

**Two limits, both load-bearing, both printed in the output rather than buried here:**

1. **These are GEPA's criteria, not the holdout.** `instruction_following_v1` is not a GEPA
   criterion and is never scored during the search, so this can test whether length tracks
   the score GEPA optimises -- the first half of the overfitting story -- and cannot test
   the holdout half at all. A correlation here is not evidence about the regression.
2. **n is small and within one run.** Candidates inside a run are not independent: each is
   derived from a parent, so later ones are both longer and better by construction. A
   positive correlation is the expected shape of a working search, not a finding.

`candidates.json` holds the prompts as plain JSON but **no scores**; the scores are in
`gepa_state.bin`. Anything correlating the two needs the pickle, which is why this reads it.

Usage:
    uv run python scripts/analyze_candidate_lengths.py outputs/gepa_runs/sonnet_opt
    uv run python scripts/analyze_candidate_lengths.py gs://.../stages/optimize/gepa_run/c08-new-r1
"""

from __future__ import annotations

import argparse
import pickle
import statistics as st
import subprocess
import sys
import tempfile
from pathlib import Path

# Below this, a correlation is noise wearing a number. Reported as "n too small" rather
# than printed, because a printed number gets quoted and the caveat does not travel with it.
MIN_N_FOR_CORRELATION = 8

STATE_FILE = "gepa_state.bin"


def fetch(run_dir: str) -> Path:
    """Local path for a run dir, downloading from GCS if needed."""
    if not run_dir.startswith("gs://"):
        return Path(run_dir)
    tmp = Path(tempfile.mkdtemp(prefix="gepa_run_"))
    subprocess.run(
        ["gsutil", "-q", "cp", "-r", run_dir.rstrip("/") + "/*", str(tmp)],
        check=False,
    )
    return tmp


def load_candidates(run_dir: Path) -> list[tuple[int, float]]:
    """(prompt length, mean validation score) per candidate, in discovery order.

    Raises rather than returning empty on a missing or unreadable state file: "no
    candidates" and "looked in the wrong place" must not print the same thing.
    """
    state_path = run_dir / STATE_FILE
    if not state_path.is_file():
        raise FileNotFoundError(
            f"no {STATE_FILE} under {run_dir}. Either this is not a GEPA run_dir, or the "
            f"run predates the upload added on 2026-09-16 and its candidates are gone."
        )
    # S301: this is our own artifact, written by our own optimize container and read
    # back from our own bucket. It is not third-party input. If that ever stops being
    # true -- someone hands you a run_dir from elsewhere -- do not run this on it.
    raw = pickle.loads(state_path.read_bytes())  # noqa: S301
    state = raw if isinstance(raw, dict) else raw.__dict__

    candidates = state.get("program_candidates") or []
    subscores = state.get("prog_candidate_val_subscores") or []
    if not candidates:
        raise ValueError(f"{state_path} has no program_candidates; the run did not get going")

    rows: list[tuple[int, float]] = []
    for cand, scores in zip(candidates, subscores, strict=False):
        text = "".join(str(v) for v in cand.values()) if isinstance(cand, dict) else str(cand)
        vals = list(scores.values()) if isinstance(scores, dict) else list(scores or [])
        if not vals:
            continue
        rows.append((len(text), st.mean(vals)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", help="local path or gs:// prefix of a GEPA run_dir")
    ap.add_argument(
        "--min-n",
        type=int,
        default=MIN_N_FOR_CORRELATION,
        help="refuse to print a correlation below this many candidates",
    )
    args = ap.parse_args()

    try:
        rows = load_candidates(fetch(args.run_dir))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\n{len(rows)} candidates with scores\n")
    print(f"  {'#':>3}  {'chars':>7}  {'score':>7}")
    for i, (chars, score) in enumerate(rows):
        print(f"  {i:>3}  {chars:>7}  {score:>7.4f}")

    if len(rows) < args.min_n:
        print(
            f"\nn={len(rows)}, below --min-n={args.min_n}: NO correlation reported. A number "
            f"from this few points would be quoted without its sample size, which is the "
            f"mistake that prompted this script."
        )
    else:
        xs, ys = zip(*rows, strict=True)
        print(f"\nn={len(rows)}  Pearson r(chars, score) = {st.correlation(xs, ys):+.3f}")

    print(
        "\nREAD BEFORE QUOTING:\n"
        "  - These are GEPA's CRITERIA scores, not the holdout. instruction_following_v1 is\n"
        "    not a criterion and is never scored during the search, so nothing here is\n"
        "    evidence about the holdout regression.\n"
        "  - Candidates within a run are NOT independent -- each descends from a parent, so\n"
        "    later ones are longer and better by construction. A positive r is the expected\n"
        "    shape of a working search, not a finding.\n"
        "  - One run is one run. Pool across runs before believing anything."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
