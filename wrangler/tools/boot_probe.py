"""GEAP request-routing probe — record every attempt, retry nothing.

This is a sibling of ``wrangler/tools/traffic.py``, not a replacement. Traffic
exists to *land* traces and spends up to six attempts per query to do it; that
is correct for its job and fatal for this one. A retry collapses six
independent draws into one "did any of them work", which is exactly the
accounting that let a ~17% server-side success rate read as a respectable trace
count for as long as it did.

The probe measures the individual attempt instead:

* **one request in flight per engine.** Arms run concurrently across *different*
  engines, so the wall clock still divides by the number of arms while each
  engine's log stream stays unambiguous. With two requests overlapping, two
  ``POST /api/stream_reasoning_engine`` lines fall inside both client windows
  and neither can be attributed to a request.
* **no retries.** Every attempt is a row, whatever it did.
* **a nonce in every prompt.** Free now, impossible to add afterwards, and a
  second join key the day any ADK log level surfaces request content.

Rows land in ``outputs/probes/<run_id>.jsonl`` and are joined to the serving
worker by ``wrangler/tools/boot_probe_join.py``.

See docs/notes/silent-failures.md #5.

Usage:
    uv run python -m wrangler.tools.boot_probe \\
        --arm mcp-claude=4981388556929859584 \\
        --arm bare-claude=6589173623901126656 \\
        --n 120 --spacing 5 --block-size 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..core.config import GCP_PROJECT_ID, GCP_REGION, disable_pyopenssl
from .traffic import _resolve_resource, _stream

# Deliberately trivial and tool-free: the probe measures whether a request
# reaches the agent at all, not whether the agent answers well. A prompt that
# provokes tool use would add the agent's own latency and failure modes to a
# measurement about request routing.
PROMPT_TEMPLATE = "Reply with exactly the word OK. Probe id: {nonce}"

# 95%. Kept as a name because the interval is the point of the whole exercise:
# the pacing arm in silent-failures.md #5 read 5/12 vs 1/12 -- an apparent 5x --
# and collapsed to nothing at n=30.
_Z = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = _Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Wilson rather than normal-approximation because the rates here sit near the
    tails and n per cell is in the low hundreds, where the normal interval
    happily returns a negative lower bound.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def append_rows(path: str | Path, rows: list[dict]) -> None:
    """Append rows as JSONL, creating parents.

    Written per block rather than at the end: a probe run is long enough that
    losing it to a crash at attempt 400 would be an expensive way to learn this.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Per-arm reach rate with a Wilson interval.

    An arm with no rows is absent from the result rather than reported at zero —
    "nothing reached the agent" and "we have no data" are different claims and
    this file exists because they were once conflated.
    """
    arms: dict[str, list[dict]] = {}
    for row in rows:
        arms.setdefault(row["arm"], []).append(row)

    out = {}
    for arm, arm_rows in arms.items():
        n = len(arm_rows)
        reached = sum(1 for r in arm_rows if r.get("reached"))
        lo, hi = wilson_interval(reached, n)
        out[arm] = {
            "n": n,
            "reached": reached,
            "rate": reached / n if n else 0.0,
            "ci_low": lo,
            "ci_high": hi,
            "errors": sum(1 for r in arm_rows if r.get("error")),
        }
    return out


async def _one_attempt(agent, arm: str, engine_id: str, index: int, block: int) -> dict:
    """Send exactly one request and describe what happened. Never retries."""
    nonce = uuid.uuid4().hex[:12]
    prompt = PROMPT_TEMPLATE.format(nonce=nonce)
    user_id = f"probe-{nonce}"

    sent_at = datetime.now(tz=UTC)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    events, error = 0, ""
    try:
        session = await asyncio.to_thread(agent.create_session, user_id=user_id)
        session_id = session["id"] if isinstance(session, dict) else session.id
        _, events = await _stream(agent, user_id, session_id, prompt)
    except Exception as exc:
        # A transport failure and an empty 200 are different observations and
        # are recorded as such. The defect under study is the *second* one: a
        # success status carrying no events, which no client can distinguish
        # from a legitimately empty answer.
        error = f"{type(exc).__name__}: {exc}"
    finished_at = datetime.now(tz=UTC)

    return {
        "arm": arm,
        "engine_id": engine_id,
        "attempt_index": index,
        "block": block,
        "nonce": nonce,
        "prompt": prompt,
        "user_id": user_id,
        "sent_at": sent_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "latency_s": loop.time() - t0,
        "event_count": events,
        "reached": events > 0,
        "error": error,
    }


async def run_arm(
    agent,
    arm: str,
    engine_id: str,
    n: int,
    spacing: float = 5.0,
    block_size: int | None = None,
    out_path: str | Path | None = None,
) -> list[dict]:
    """Run ``n`` strictly-serialized attempts against one engine.

    ``spacing`` is the gap *between* attempts, not a rate limit — the point is
    that no two requests to this engine are ever in flight together.

    ``block_size`` labels the attempts in groups. Concurrent arms already share
    the time axis, so blocks do not change what is measured; they make the
    time-of-run visible in the data instead of assumed, which is the lesson from
    the sync-vs-async dead end (silent-failures.md #5).
    """
    size = block_size or n or 1
    rows: list[dict] = []
    pending: list[dict] = []
    for i in range(n):
        if i and spacing > 0:
            await asyncio.sleep(spacing)
        row = await _one_attempt(agent, arm, engine_id, i, i // size)
        rows.append(row)
        pending.append(row)
        if out_path and len(pending) >= size:
            append_rows(out_path, pending)
            pending = []
    if out_path and pending:
        append_rows(out_path, pending)
    return rows


def run_probe(
    arms: dict[str, str],
    n: int,
    spacing: float = 5.0,
    block_size: int | None = None,
    run_id: str | None = None,
    out_dir: str | Path = "outputs/probes",
) -> dict[str, dict]:
    """Probe every arm concurrently, one request in flight per engine."""
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    disable_pyopenssl()

    rid = run_id or datetime.now(tz=UTC).strftime("probe_%Y%m%d_%H%M%S")
    out_path = Path(out_dir) / f"{rid}.jsonl"

    print("=" * 64)
    print("GEAP BOOT PROBE")
    print("=" * 64)
    print(f"  Run id:      {rid}")
    print(f"  Arms:        {len(arms)} (concurrent, one request in flight each)")
    print(f"  Attempts:    {n} per arm, {spacing}s apart, no retries")
    print(f"  Output:      {out_path}")
    for arm, engine_id in arms.items():
        print(f"    {arm:16s} -> {engine_id}")
    print()

    connections = {arm: agent_engines.get(_resolve_resource(eid)) for arm, eid in arms.items()}

    async def _all():
        return await asyncio.gather(
            *(
                run_arm(
                    connections[arm],
                    arm,
                    engine_id,
                    n=n,
                    spacing=spacing,
                    block_size=block_size,
                    out_path=out_path,
                )
                for arm, engine_id in arms.items()
            )
        )

    results = asyncio.run(_all())
    rows = [row for arm_rows in results for row in arm_rows]
    summary = summarize(rows)

    print(f"\n{'=' * 64}")
    print("PROBE COMPLETE")
    print("=" * 64)
    print(f"  {'arm':16s} {'reach':>12s}  {'rate':>7s}  {'95% CI':>16s}  {'errors':>6s}")
    for arm in arms:
        s = summary.get(arm)
        if not s:
            print(f"  {arm:16s} {'no data':>12s}")
            continue
        ci = f"{s['ci_low']:.3f}-{s['ci_high']:.3f}"
        print(
            f"  {arm:16s} {s['reached']:>5d}/{s['n']:<6d} {s['rate']:>7.1%}  {ci:>16s}  "
            f"{s['errors']:>6d}"
        )
    print(f"\n  Rows: {out_path}")
    print(f"  Join to serving workers: python -m wrangler.tools.boot_probe_join {out_path}")
    return summary


def _parse_arm(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"--arm expects name=engine_id, got {spec!r}")
    name, engine_id = spec.split("=", 1)
    if not name or not engine_id:
        raise argparse.ArgumentTypeError(f"--arm expects name=engine_id, got {spec!r}")
    return name, engine_id


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    repo_root = Path(__file__).resolve().parents[2]
    example_env = repo_root / "examples" / "multi_model_agents" / ".env"
    if example_env.exists():
        load_dotenv(str(example_env), override=True)

    parser = argparse.ArgumentParser(description="Probe GEAP request routing")
    parser.add_argument(
        "--arm",
        action="append",
        required=True,
        type=_parse_arm,
        help="name=engine_id (repeatable). Engine ids arrive here, never from a checked-in default.",
    )
    parser.add_argument("--n", type=int, default=120, help="Attempts per arm (default: 120)")
    parser.add_argument(
        "--spacing", type=float, default=5.0, help="Seconds between attempts (default: 5)"
    )
    parser.add_argument(
        "--block-size", type=int, default=30, help="Attempts per labelled block (default: 30)"
    )
    parser.add_argument("--run-id", default=None, help="Run id (default: probe_<timestamp>)")
    args = parser.parse_args()

    run_probe(
        arms=dict(args.arm),
        n=args.n,
        spacing=args.spacing,
        block_size=args.block_size,
        run_id=args.run_id,
    )
