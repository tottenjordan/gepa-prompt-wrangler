"""Join each probe attempt to the GEAP worker that served it.

The client sees an HTTP 200 with no events and cannot tell why. The server logs
say which worker process served which request and when that process started.
Putting the two together is what turns "roughly three of four requests seem not
to reach the model" into "this request was served by a worker N seconds into a
boot that takes M".

**The join key is the PID**, read off the `[3258]` prefix every container log
line carries. That is only valid if no PID names two different workers in the
window, since a PID is unique within a container and not across them. The note
carried that as an unverified caveat for two days; `pid_reuse_count` checks it,
and `join_summary` refuses to call the join sound when it fails.

**Serialization is what makes the join unambiguous.** The probe keeps one
request in flight per engine, so exactly one `POST /api/stream_reasoning_engine`
line should fall inside each client window. Zero or two is recorded as
unjoinable with a reason rather than guessed at — an unjoinable fraction that
goes unreported is the same failure this whole investigation is about.

Note the container logs everything at DEFAULT severity, so a `severity>=WARNING`
filter returns nothing at all and looks like a clean bill of health. Filter on
`textPayload`.

Usage:
    uv run python -m wrangler.tools.boot_probe_join outputs/probes/probe_x.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..core.config import GCP_PROJECT_ID
from .boot_probe import wilson_interval

STREAM_REQUEST = "POST /api/stream_reasoning_engine"
STARTUP_COMPLETE = "Application startup complete"
SERVER_START = "Started server process"

# Claude's are confirmed from the ReasoningEngine logs. The Gemini equivalent
# must be read off a real deploy before it is trusted -- guessing it here would
# silently report every Gemini request as never having reached the model.
#
# This only affects the corroborating signal. The *primary* response variable is
# the client-side `reached` (>=1 event), which is model-agnostic, so a missed
# pattern degrades the report rather than invalidating it.
DEFAULT_MODEL_PATTERNS = ("Received response from Claude", ":rawPredict")

_LINE = re.compile(r"^(?P<ts>\S+)\t\[(?P<pid>\d+)\]\s*(?P<msg>.*)$")

# Slack after the client saw the stream finish, for the model-response line to
# land in the log pipeline.
_MODEL_SLACK_S = 5.0

# Fetched with a lead-in so a worker that booted before the run still has its
# first log line in the window; without it, worker age is silently truncated to
# the run start and every long-lived worker looks freshly booted.
DEFAULT_LEAD_IN_MINUTES = 15


@dataclass
class LogEntry:
    ts: datetime
    pid: int
    message: str


@dataclass
class Worker:
    pid: int
    first_seen: datetime
    startup_complete: datetime | None = None
    entries: list[LogEntry] = field(default_factory=list)


def parse_log_line(line: str) -> LogEntry | None:
    """Parse one `gcloud logging read --format='value(timestamp,textPayload)'` line."""
    match = _LINE.match(line.rstrip("\n"))
    if not match:
        return None
    try:
        ts = datetime.fromisoformat(match["ts"])
    except ValueError:
        return None
    return LogEntry(ts=ts, pid=int(match["pid"]), message=match["msg"])


def pid_reuse_count(entries: list[LogEntry]) -> int:
    """How many PIDs logged more than one `Started server process` in this window.

    Anything above zero means at least one PID names two workers and every
    per-request join in the window is suspect.
    """
    starts: dict[int, int] = {}
    for entry in entries:
        if SERVER_START in entry.message:
            starts[entry.pid] = starts.get(entry.pid, 0) + 1
    return sum(1 for count in starts.values() if count > 1)


def build_worker_timelines(entries: list[LogEntry]) -> dict[int, Worker]:
    """Group log entries by PID, recording when each worker first appeared."""
    workers: dict[int, Worker] = {}
    for entry in sorted(entries, key=lambda e: e.ts):
        worker = workers.get(entry.pid)
        if worker is None:
            worker = Worker(pid=entry.pid, first_seen=entry.ts)
            workers[entry.pid] = worker
        worker.entries.append(entry)
        if worker.startup_complete is None and STARTUP_COMPLETE in entry.message:
            worker.startup_complete = entry.ts
    return workers


def join_rows(
    rows: list[dict],
    entries: list[LogEntry],
    model_patterns: tuple[str, ...] = DEFAULT_MODEL_PATTERNS,
) -> list[dict]:
    """Attach serving worker, worker age, and boot state to each probe row."""
    workers = build_worker_timelines(entries)
    requests = sorted(
        (e for e in entries if STREAM_REQUEST in e.message),
        key=lambda e: e.ts,
    )

    joined = []
    for row in rows:
        sent = datetime.fromisoformat(row["sent_at"])
        finished = datetime.fromisoformat(row["finished_at"])
        matches = [e for e in requests if sent <= e.ts <= finished]

        out = dict(row)
        if len(matches) != 1:
            out.update(
                serving_pid=None,
                worker_age_s=None,
                booted_before_request=None,
                reached_model=None,
                joinable=False,
                join_note=(
                    "no stream-request log line in the client window"
                    if not matches
                    else f"{len(matches)} stream-request log lines in the client window"
                ),
            )
            joined.append(out)
            continue

        served = matches[0]
        worker = workers[served.pid]
        window_end = finished + timedelta(seconds=_MODEL_SLACK_S)
        out.update(
            serving_pid=served.pid,
            worker_age_s=(served.ts - worker.first_seen).total_seconds(),
            booted_before_request=(
                worker.startup_complete is not None and worker.startup_complete <= served.ts
            ),
            reached_model=any(
                served.ts <= e.ts <= window_end and any(p in e.message for p in model_patterns)
                for e in worker.entries
            ),
            joinable=True,
            join_note="",
        )
        joined.append(out)
    return joined


def dose_response(rows: list[dict], edges: tuple[float, ...] = (0, 2, 5, 10, 30, 90)) -> list[dict]:
    """Reach rate binned by the serving worker's age at request time.

    Unjoinable rows are excluded rather than counted as failures: not knowing
    which worker served a request is not evidence that it was a cold one.
    """
    usable = [r for r in rows if r.get("joinable") and r.get("worker_age_s") is not None]
    curve = []
    for i, low in enumerate(edges):
        high = edges[i + 1] if i + 1 < len(edges) else float("inf")
        binned = [r for r in usable if low <= r["worker_age_s"] < high]
        n = len(binned)
        reached = sum(1 for r in binned if r.get("reached"))
        lo, hi = wilson_interval(reached, n)
        curve.append(
            {
                "age_low": low,
                "age_high": high,
                "n": n,
                "reached": reached,
                "reach_rate": reached / n if n else 0.0,
                "ci_low": lo,
                "ci_high": hi,
            }
        )
    return curve


def join_summary(rows: list[dict], pid_reuse: int) -> dict:
    """How much of the run could be joined, and whether the join means anything."""
    total = len(rows)
    joined = sum(1 for r in rows if r.get("joinable"))
    return {
        "total": total,
        "joined": joined,
        "unjoinable": total - joined,
        "join_rate": joined / total if total else 0.0,
        "pid_reuse": pid_reuse,
        "join_sound": pid_reuse == 0,
    }


def fetch_logs(
    engine_id: str,
    start: datetime,
    end: datetime,
    project: str | None = None,
    limit: int = 20000,
) -> list[LogEntry]:
    """Read a ReasoningEngine's logs for a window via the gcloud CLI."""
    filter_ = (
        'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
        f'AND resource.labels.reasoning_engine_id="{engine_id}" '
        f'AND timestamp>="{start.astimezone(UTC).isoformat()}" '
        f'AND timestamp<="{end.astimezone(UTC).isoformat()}"'
    )
    result = subprocess.run(
        [
            "gcloud",
            "logging",
            "read",
            filter_,
            f"--project={project or GCP_PROJECT_ID}",
            f"--limit={limit}",
            "--format=value(timestamp,textPayload)",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return [e for e in (parse_log_line(line) for line in result.stdout.splitlines()) if e]


def load_rows(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def join_probe_run(
    probe_path: str | Path,
    lead_in_minutes: int = DEFAULT_LEAD_IN_MINUTES,
    model_patterns: tuple[str, ...] = DEFAULT_MODEL_PATTERNS,
    project: str | None = None,
) -> dict:
    """Join a whole probe run, one engine at a time, and print the result."""
    rows = load_rows(probe_path)
    if not rows:
        print(f"No rows in {probe_path}")
        return {}

    by_engine: dict[str, list[dict]] = {}
    for row in rows:
        by_engine.setdefault(row["engine_id"], []).append(row)

    all_joined: list[dict] = []
    per_engine: dict[str, dict] = {}
    for engine_id, engine_rows in by_engine.items():
        start = min(datetime.fromisoformat(r["sent_at"]) for r in engine_rows)
        end = max(datetime.fromisoformat(r["finished_at"]) for r in engine_rows)
        entries = fetch_logs(
            engine_id,
            start - timedelta(minutes=lead_in_minutes),
            end + timedelta(minutes=1),
            project=project,
        )
        reuse = pid_reuse_count(entries)
        joined = join_rows(engine_rows, entries, model_patterns=model_patterns)
        all_joined.extend(joined)
        per_engine[engine_id] = join_summary(joined, pid_reuse=reuse)
        per_engine[engine_id]["log_lines"] = len(entries)

    out_path = Path(probe_path).with_suffix(".joined.jsonl")
    with out_path.open("w") as f:
        for row in all_joined:
            f.write(json.dumps(row, default=str) + "\n")

    print("=" * 64)
    print("JOIN SUMMARY")
    print("=" * 64)
    for engine_id, summary in per_engine.items():
        sound = (
            "sound" if summary["join_sound"] else f"UNSOUND ({summary['pid_reuse']} reused PIDs)"
        )
        print(
            f"  {engine_id}: {summary['joined']}/{summary['total']} joined "
            f"({summary['join_rate']:.0%}), {summary['log_lines']} log lines, join {sound}"
        )

    print(f"\n{'=' * 64}")
    print("REACH BY WORKER AGE AT REQUEST")
    print("=" * 64)
    print(f"  {'worker age':>16s} {'n':>6s} {'reach':>8s}  95% CI")
    for row_bin in dose_response(all_joined):
        if not row_bin["n"]:
            continue
        high = "inf" if row_bin["age_high"] == float("inf") else f"{row_bin['age_high']:g}"
        label = f"{row_bin['age_low']:g}-{high}s"
        print(
            f"  {label:>16s} {row_bin['n']:>6d} {row_bin['reach_rate']:>7.1%}  "
            f"{row_bin['ci_low']:.3f}-{row_bin['ci_high']:.3f}"
        )

    print(f"\n  Joined rows: {out_path}")
    return {"per_engine": per_engine, "rows": all_joined}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Join probe attempts to serving workers")
    parser.add_argument("probe_path", help="Path to a probe JSONL file")
    parser.add_argument(
        "--lead-in",
        type=int,
        default=DEFAULT_LEAD_IN_MINUTES,
        help=(
            "Minutes of log history to fetch before the run. Without it a worker that "
            f"booted earlier looks freshly started (default: {DEFAULT_LEAD_IN_MINUTES})"
        ),
    )
    parser.add_argument(
        "--model-pattern",
        action="append",
        default=None,
        help="Log substring meaning the model was reached (repeatable). Verify per model family.",
    )
    parser.add_argument("--project", default=None, help="GCP project (default: from config)")
    args = parser.parse_args()

    join_probe_run(
        args.probe_path,
        lead_in_minutes=args.lead_in,
        model_patterns=tuple(args.model_pattern) if args.model_pattern else DEFAULT_MODEL_PATTERNS,
        project=args.project,
    )
