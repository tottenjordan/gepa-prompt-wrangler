"""Join each probe attempt to the GEAP worker that served it.

The client sees an HTTP 200 with no events and cannot tell why. The server logs
say which worker process served which request and when that process started.
Putting the two together is what turns "roughly three of four requests seem not
to reach the model" into "this request was served by a worker N seconds into a
boot that takes M".

There are two independent joins, and they fail independently — which is exactly
why both are here.

**The user-id join answers "did the model run".** GEAP emits a structured log
stream (`reasoning_engine_stdout`) whose labels carry `user.id` alongside the
full input and output messages. The probe puts its nonce in the user id, so
membership is a lookup, not an inference from co-occurring PIDs. Verified on the
2026-08-23 gate run: of three attempts, only the one that returned events
appears in that stream — the other two got an HTTP 200 for which no inference
was ever performed.

**The PID join answers "how old was the worker".** It reads the `[3258]` prefix
every container log line carries and matches the request line falling inside the
client's window; serialization (one request in flight per engine) is what makes
that unambiguous, and zero or two matches is recorded as unjoinable with a
reason rather than guessed at. A PID is recycled across containers — a settled
engine went 30 minutes with zero reuse while a freshly deployed one had ten in
an hour — so each `Started server process` opens a new *incarnation* and a
request is attributed to whichever was current. What does limit an age is a
worker whose start line fell outside the fetched window; that row is flagged as
a lower bound and `join_sound` goes false.

An unjoinable fraction that goes unreported is the same failure this whole
investigation is about, so it is always printed.

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
    """One *incarnation* of a worker process, not one PID.

    A PID is unique within a container and recycled across them: a settled
    engine went 30 minutes with zero reuse, while the first hour of a freshly
    deployed one had ten. Treating a PID as a worker therefore ages a recycled
    PID from the wrong container -- by minutes -- so each `Started server
    process` opens a new incarnation and a request is attributed to whichever
    one was current when it arrived.

    ``started`` is None when the incarnation's start line fell before the
    fetched window. Its age is then a lower bound rather than a measurement,
    and ``age_is_lower_bound`` says so on the row.
    """

    pid: int
    first_seen: datetime
    started: datetime | None = None
    startup_complete: datetime | None = None
    entries: list[LogEntry] = field(default_factory=list)

    @property
    def birth(self) -> datetime:
        return self.started or self.first_seen


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

    Reported as context, not as a disqualifier: `build_worker_timelines` splits
    a reused PID into separate incarnations, so the age stays correct. It is
    worth printing because it varies enormously with engine age — zero on a
    settled engine, ten in a fresh one's first hour — and anyone reading a
    worker-age figure should know which they are looking at.
    """
    starts: dict[int, int] = {}
    for entry in entries:
        if SERVER_START in entry.message:
            starts[entry.pid] = starts.get(entry.pid, 0) + 1
    return sum(1 for count in starts.values() if count > 1)


def build_worker_timelines(entries: list[LogEntry]) -> dict[int, list[Worker]]:
    """Split each PID's log lines into incarnations, one per `Started server process`.

    Returns ``{pid: [Worker, ...]}`` in time order. A PID whose first lines
    precede any start line gets a leading incarnation with ``started=None``.
    """
    workers: dict[int, list[Worker]] = {}
    for entry in sorted(entries, key=lambda e: e.ts):
        chain = workers.setdefault(entry.pid, [])
        if SERVER_START in entry.message or not chain:
            chain.append(
                Worker(
                    pid=entry.pid,
                    first_seen=entry.ts,
                    started=entry.ts if SERVER_START in entry.message else None,
                )
            )
        current = chain[-1]
        current.entries.append(entry)
        if current.startup_complete is None and STARTUP_COMPLETE in entry.message:
            current.startup_complete = entry.ts
    return workers


def worker_at(workers: dict[int, list[Worker]], pid: int, when: datetime) -> Worker | None:
    """The incarnation of ``pid`` that was current at ``when``."""
    chain = workers.get(pid) or []
    current = None
    for worker in chain:
        if worker.birth <= when:
            current = worker
    return current or (chain[0] if chain else None)


def join_rows(
    rows: list[dict],
    entries: list[LogEntry],
    model_patterns: tuple[str, ...] = DEFAULT_MODEL_PATTERNS,
    served_user_ids: set[str] | None = None,
) -> list[dict]:
    """Attach serving worker, worker age, boot state, and model reach to each row.

    Two independent joins, deliberately kept separate:

    * **the PID join** places the request on a worker incarnation and gives its
      age. It depends on a timestamp window and can fail.
    * **the user-id join** says whether the model was reached. GEAP's structured
      log stream carries ``labels."user.id"``, and the probe puts its nonce
      there, so this is a lookup rather than an inference. Supply
      ``served_user_ids`` and it is used; omit it and the weaker log-pattern
      match on the serving worker is used instead.

    They fail independently, which is the point of having both: on the gate run
    the PID join was unsound while the user-id join was exact.
    """
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

        if served_user_ids is not None:
            out["reached_model"] = row.get("user_id") in served_user_ids
            out["model_join"] = "user_id"

        if len(matches) != 1:
            out.update(
                serving_pid=None,
                worker_age_s=None,
                worker_age_is_lower_bound=None,
                booted_before_request=None,
                joinable=False,
                join_note=(
                    "no stream-request log line in the client window"
                    if not matches
                    else f"{len(matches)} stream-request log lines in the client window"
                ),
            )
            if served_user_ids is None:
                out.update(reached_model=None, model_join="log_pattern")
            joined.append(out)
            continue

        served = matches[0]
        # The matched line came from this PID, so an incarnation always exists.
        worker = worker_at(workers, served.pid, served.ts) or Worker(
            pid=served.pid, first_seen=served.ts
        )
        window_end = finished + timedelta(seconds=_MODEL_SLACK_S)
        out.update(
            serving_pid=served.pid,
            worker_age_s=(served.ts - worker.birth).total_seconds(),
            worker_age_is_lower_bound=worker.started is None,
            booted_before_request=(
                worker.startup_complete is not None and worker.startup_complete <= served.ts
            ),
            joinable=True,
            join_note="",
        )
        if served_user_ids is None:
            out.update(
                reached_model=any(
                    served.ts <= e.ts <= window_end and any(p in e.message for p in model_patterns)
                    for e in worker.entries
                ),
                model_join="log_pattern",
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
    """How much of the run could be joined, and how far the ages can be trusted.

    ``pid_reuse`` is reported but no longer disqualifying: incarnations handle a
    recycled PID correctly. What does compromise an age is a worker whose start
    line fell outside the fetched window, since its age is then a lower bound.
    ``ages_measured`` is the count that are not, and ``join_sound`` means every
    joined row has a real one — raise ``--lead-in`` when it does not.
    """
    total = len(rows)
    joined = [r for r in rows if r.get("joinable")]
    lower_bound = sum(1 for r in joined if r.get("worker_age_is_lower_bound"))
    return {
        "total": total,
        "joined": len(joined),
        "unjoinable": total - len(joined),
        "join_rate": len(joined) / total if total else 0.0,
        "pid_reuse": pid_reuse,
        "ages_measured": len(joined) - lower_bound,
        "ages_lower_bound": lower_bound,
        "join_sound": bool(joined) and lower_bound == 0,
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


def fetch_served_user_ids(
    engine_id: str,
    start: datetime,
    end: datetime,
    project: str | None = None,
    limit: int = 20000,
) -> set[str]:
    """User ids GEAP actually ran an inference for, from the structured log stream.

    `reasoning_engine_stdout` entries carry ``labels."user.id"`` alongside the
    input and output messages, and the probe puts its nonce in the user id. So
    membership in this set *is* the answer to "did this request reach the
    model" — no timestamp window, no PID, no pattern matching.

    Verified on the 2026-08-23 gate run: of three attempts, only the one that
    returned events appears here. The other two got an HTTP 200 for which no
    inference was ever performed.
    """
    filter_ = (
        'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
        f'AND resource.labels.reasoning_engine_id="{engine_id}" '
        'AND labels."user.id":"probe-" '
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
            '--format=value(labels."user.id")',
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


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
        served = fetch_served_user_ids(
            engine_id,
            start - timedelta(minutes=1),
            end + timedelta(minutes=1),
            project=project,
        )
        reuse = pid_reuse_count(entries)
        joined = join_rows(
            engine_rows, entries, model_patterns=model_patterns, served_user_ids=served
        )
        all_joined.extend(joined)
        per_engine[engine_id] = join_summary(joined, pid_reuse=reuse)
        per_engine[engine_id]["log_lines"] = len(entries)
        per_engine[engine_id]["served_user_ids"] = len(served)
        per_engine[engine_id]["reached_model"] = sum(1 for r in joined if r.get("reached_model"))

    out_path = Path(probe_path).with_suffix(".joined.jsonl")
    with out_path.open("w") as f:
        for row in all_joined:
            f.write(json.dumps(row, default=str) + "\n")

    print("=" * 64)
    print("JOIN SUMMARY")
    print("=" * 64)
    for engine_id, summary in per_engine.items():
        ages = (
            "all ages measured"
            if summary["join_sound"]
            else f"{summary['ages_lower_bound']} age(s) are lower bounds — raise --lead-in"
        )
        print(
            f"  {engine_id}: {summary['joined']}/{summary['total']} joined "
            f"({summary['join_rate']:.0%}), {summary['log_lines']} log lines, "
            f"{summary['pid_reuse']} reused PIDs, {ages}"
        )
        print(
            f"    reached the model (user-id join): {summary['reached_model']}/{summary['total']}"
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
