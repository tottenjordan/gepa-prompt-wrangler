"""Locate MCP toolset losses in an optimize log, and test what precedes them.

Silent failure #12. Three mechanisms were proposed and shipped before this script
existed; all three were argued from a log read by eye. This measures instead.

The headline output is the disjoint-band table: how many `Closing toolset` events
fall in each 30-second band before a failure, against a permutation baseline drawn
from random instants in the same stage. A cause should show up as enrichment in the
band containing the moment the hung call *started* -- which is one timeout before
the error is logged, not at the error itself.

**Pass the real timeout.** The window is the whole analysis. Run at `--timeout 60`
and the 2026-09-11 data says closes are *depleted* before failures (0.1x, p=0.98);
run at the true 120 s and the same data says 10x enriched, p<0.0001. The constant
lives in ADK's connection params, not here, so it is an argument.

Usage:
    uv run python scripts/analyze_toolset_loss.py --job-id 1679178152259092480
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import subprocess

# Both wordings. ADK's own `_ToolsetFailureCounter` matches only the second, and on
# 2026-09-08 and again on 2026-09-11 the container emitted only the first -- so the
# run's self-reported degradation was 0 while the log held 16.
FAILURE_PATTERNS = ("will run without the tools", "Failed to get tools from toolset")
CLOSE_PATTERN = "Closing toolset"


def read_timestamps(job_id: str, pattern: str, limit: int = 20000) -> list[dt.datetime]:
    """Timestamps of log lines matching `pattern`, oldest first.

    Filtered server-side: one optimize component emits ~50k lines and paging them
    through Python is minutes of wall clock for no extra information.
    """
    out = subprocess.run(
        [
            "gcloud",
            "logging",
            "read",
            (
                f'resource.type="ml_job" AND resource.labels.job_id="{job_id}" '
                f'AND jsonPayload.message:"{pattern}"'
            ),
            "--project=" + _project(),
            f"--limit={limit}",
            "--format=value(timestamp)",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return sorted(dt.datetime.fromisoformat(t) for t in out if t)


def _project() -> str:
    from wrangler.core.config import GCP_PROJECT_ID

    return GCP_PROJECT_ID


def burst(times: list[dt.datetime], within: float = 2.0) -> list[list[dt.datetime]]:
    """Group failures that land within `within` seconds into one event.

    Several toolsets die together when one shared session is torn down, and counting
    those separately would triple-weight a single incident.
    """
    groups: list[list[dt.datetime]] = []
    for t in times:
        if groups and (t - groups[-1][-1]).total_seconds() < within:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def count_in_band(events: list[dt.datetime], anchor: dt.datetime, lo: float, hi: float) -> int:
    """Events in [anchor-hi, anchor-lo) -- i.e. `lo` to `hi` seconds *before* anchor."""
    a, b = anchor - dt.timedelta(seconds=hi), anchor - dt.timedelta(seconds=lo)
    return sum(1 for e in events if a <= e < b)


def band_table(
    failures: list[list[dt.datetime]],
    closes: list[dt.datetime],
    timeout: float,
    draws: int = 1500,
    seed: int = 2,
) -> list[tuple[float, float, float, float]]:
    """(lo, hi, observed mean, baseline mean) per band, out to twice the timeout."""
    random.seed(seed)
    start, end = closes[0], closes[-1]
    span = (end - start).total_seconds()
    step = timeout / 4
    bands = [(i * step, (i + 1) * step) for i in range(int(2 * timeout / step))]
    rows = []
    for lo, hi in bands:
        obs = sum(count_in_band(closes, f[0], lo, hi) for f in failures) / len(failures)
        sampled = []
        for _ in range(draws):
            pts = [
                start + dt.timedelta(seconds=random.random() * span) for _ in range(len(failures))
            ]
            sampled.append(sum(count_in_band(closes, p, lo, hi) for p in pts) / len(pts))
        rows.append((lo, hi, obs, sum(sampled) / len(sampled)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job-id", required=True, help="ml_job custom job id, not the pipeline name")
    ap.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="ADK's MCP call timeout in seconds. The window is the whole analysis; "
        "see the module docstring before changing it.",
    )
    args = ap.parse_args()

    failures: list[dt.datetime] = []
    for pattern in FAILURE_PATTERNS:
        hits = read_timestamps(args.job_id, pattern, limit=500)
        print(f"{pattern!r}: {len(hits)}")
        failures.extend(hits)
    if not failures:
        print("\nNo toolset losses in this run.")
        return 0

    closes = read_timestamps(args.job_id, CLOSE_PATTERN)
    groups = burst(sorted(failures))
    print(f"\n{len(failures)} losses in {len(groups)} bursts; {len(closes)} toolset closes")

    print(f"\nToolset closes by band before a failure (timeout {args.timeout:g}s):")
    print("  before failure    observed   baseline   enrichment")
    for lo, hi, obs, base in band_table(groups, closes, args.timeout):
        ratio = f"{obs / base:5.1f}x" if base else "    --"
        print(f"  {lo:5.0f}-{hi:5.0f}s      {obs:7.1f}   {base:7.2f}   {ratio}  {'#' * int(obs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
