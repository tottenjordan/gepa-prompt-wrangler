"""Traffic generator — send test queries to deployed agents for OTel trace generation.

Creates a new session with a unique user ID per query to keep traces independent.

GEAP answers a share of requests with 200 OK, zero events and no trace, having run no
inference at all — 31.7% over 960 requests, but 4% to 68% depending on the engine. No
client-side trick avoids it: session identity, pacing, warm-up bursts, removing every
toolset and switching model family were all measured and none moved the rate. So this
tool spends *attempts* and *concurrency* to land traces anyway, and reports the
per-attempt rate so the underlying defect stays visible. See the table above
``_DEFAULT_MAX_ATTEMPTS`` and docs/notes/silent-failures.md #5.

Usage:
    # Send all 30 eval cases to one agent
    uv run python -m wrangler.tools.traffic --agent-id 4981388556929859584

    # Send to multiple agents
    uv run python -m wrangler.tools.traffic --agent-id 4981388556929859584 6589173623901126656

    # Control rate and count
    uv run python -m wrangler.tools.traffic --agent-id 4981388556929859584 --count 10 --interval 5

    # Use custom eval data
    uv run python -m wrangler.tools.traffic --agent-id 4981388556929859584 --eval-data eval_data/my_cases.yaml

    # Steady state: 1 query every 3 seconds for 5 minutes
    uv run python -m wrangler.tools.traffic --agent-id 4981388556929859584 --count 100 --interval 3
"""

import argparse
import asyncio
import uuid
from pathlib import Path

import vertexai
from vertexai import agent_engines

from ..core.config import GCP_PROJECT_ID, GCP_REGION, disable_pyopenssl
from ..core.converter import load_eval_file

DEFAULT_QUERIES = [
    ("Find flights from SFO to JFK", "low"),
    ("Search for hotels in New York", "low"),
    ("What is the meal expense limit?", "low"),
    ("Book flight FL001 for Alice Johnson", "low"),
    ("Is a $50 transport expense within policy?", "low"),
    ("Show expenses for user EMP001", "low"),
    ("Find me a hotel in Miami", "low"),
    ("Submit a $45 meals expense for lunch, user ID EMP001", "medium"),
    ("Find flights to JFK and compare the cheapest options", "medium"),
    ("Search hotels in New York, then check if the rate fits lodging policy", "medium"),
    ("Check if a $100 meal and $250 entertainment expense are within policy", "medium"),
    ("Show expense history for EMP001 and flag policy violations", "medium"),
    (
        "Book flight FL001 for Alice, check Grand Hyatt lodging policy, submit $75 meals for EMP001",
        "high",
    ),
    ("Compare flights SFO-JFK vs LAX-ORD with hotel costs in each city", "high"),
    ("I have a $2000 budget for London. Find flights, hotels, check policies.", "high"),
]


def _resolve_resource(engine_id: str) -> str:
    if engine_id.startswith("projects/"):
        return engine_id
    return f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"


# A share of requests come back HTTP 200 with an empty event stream -- no error,
# no trace -- and the server ran no inference for them at all. Established by a
# per-request join on 960 attempts (2026-08-23): a nonce in each prompt, matched
# against GEAP's structured log stream, agreed with the client-side event count
# 960 times out of 960.
#
# The cause is NOT a worker caught mid-boot, which is what this comment claimed
# until that measurement. All 948 joined requests were served by workers that had
# already finished starting up, median age 215s.
#
# **No client-side strategy avoids this.** Interleaved and order-rotated so an
# intermittent fault could not frame one arm (docs/notes/silent-failures.md #5):
#
#   session identity  new user+session 1/12, same user 2/12, same session 1/12
#   pacing            sequential 5/30 (17%), concurrent 10/30 (33%), p=0.23
#   warm-up burst     2/18 (11%) -- no better, plausibly worse
#   no toolsets       bare agent, sub-second startup: still 44.6% failures
#   model family      both Claude and Gemini affected
#
# `GEAP_MIN_INSTANCES=2` does not move it either (1.3 startups per request,
# before and after).
#
# So the design here does not try to dodge the failure. It treats each attempt as
# an independent draw and spends attempts and concurrency to get the traces
# anyway -- while reporting the per-attempt rate, so a server-side defect stays
# visible instead of being buried by the retries.
#
# Six attempts was chosen against a measured ~1-in-4 per-attempt rate on one
# engine. The rate is now known to be strongly per-engine (4% to 68% failure), so
# read the attempt rate this tool prints rather than assuming the budget fits.
_DEFAULT_MAX_ATTEMPTS = 6
_DEFAULT_CONCURRENCY = 4

# Below this, the retries are papering over something that needs looking at
# rather than absorbing ordinary flakiness.
_ATTEMPT_RATE_FLOOR = 0.5


def _event_text(event) -> str:
    """Pull the assistant text out of one ADK event.

    Events come back as ``{"content": {"parts": [{"text": ...}, ...]}}`` — a
    part may instead hold a ``function_call`` or ``function_response``, which
    carry no text. Anything else (a bare object with ``.text``) is tolerated so
    this keeps working if the SDK stops handing back plain dicts.
    """
    if isinstance(event, dict):
        parts = event.get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return getattr(event, "text", "") or ""


async def _stream(agent, user_id: str, session_id: str, query: str) -> tuple[str, int]:
    """Run one query and return (text, event_count).

    Uses ``async_stream_query`` because ADK's class-methods list marks the sync
    ``stream_query`` "Deprecated. Use async_stream_query instead." The two
    behave identically against GEAP — measured, alternating, on one engine — so
    this is future-proofing, not a workaround. See ``_DEFAULT_MAX_ATTEMPTS``
    for the failure the caller does have to handle.
    """
    events = [
        event
        async for event in agent.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=query,
        )
    ]
    return "".join(_event_text(e) for e in events), len(events)


def summarize_run(traces: int, queries: int, attempts: int) -> list[str]:
    """Render the closing report, including the per-attempt success rate.

    Split out from the run loop so the arithmetic is testable without a
    deployed engine. The attempt rate is the number that matters: retries can
    carry the trace count to something respectable while every individual
    request is still being eaten, and reporting only "12/12 queries produced
    traces" would hide exactly the defect this tool exists to observe.
    """
    rate = traces / attempts if attempts else 0.0
    lines = [
        f"  Queries:        {queries}",
        f"  Traces emitted: {traces}",
        f"  Attempts spent: {attempts}",
        f"  Attempt rate:   {rate:.0%} ({traces}/{attempts} reached the agent)",
    ]
    if queries and traces < queries:
        lines.append(f"  ** {queries - traces} queries produced NO trace after all attempts")
    if attempts and rate < _ATTEMPT_RATE_FLOOR:
        lines.append(
            f"  ** Attempt rate below {_ATTEMPT_RATE_FLOOR:.0%} — the engine is dropping most"
        )
        lines.append("     requests on booting workers. See docs/notes/silent-failures.md #5.")
    return lines


async def _one_query(
    agent,
    query: str,
    max_attempts: int,
    sem: asyncio.Semaphore,
) -> tuple[int, int, str]:
    """Resolve one query to a trace. Returns (events, attempts_spent, text).

    Each attempt gets a fresh user and session. That is not a workaround for
    anything — session identity was measured to make no difference (see the
    table at the top) — it is just the cheapest way to keep traces independent,
    which is what this tool is for.
    """
    attempts = 0
    last_error = ""
    for _ in range(max_attempts):
        attempts += 1
        user_id = f"traffic-{uuid.uuid4().hex[:8]}"
        async with sem:
            try:
                # AgentEngine proxies the ADK class_methods list at runtime, so
                # `create_session` does not exist statically on the object.
                session = await asyncio.to_thread(agent.create_session, user_id=user_id)
                session_id = session["id"] if isinstance(session, dict) else session.id
                text, events = await _stream(agent, user_id, session_id, query)
            except Exception as exc:
                # Keep spending attempts rather than abandoning the query. The
                # previous version returned on the first exception, so one
                # transient network blip cost a trace that a retry would have
                # landed — and the failure mode here is *already* transient.
                last_error, events, text = f"error: {exc}", 0, ""
        if events:
            return events, attempts, text
    return 0, attempts, last_error


def generate_traffic(
    agent_ids: list[str],
    queries: list[tuple[str, str]] | None = None,
    count: int | None = None,
    interval: float = 1.0,
    eval_data_path: str | None = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
):
    """Send queries to deployed agents, spending attempts to land traces.

    Args:
        agent_ids: List of Agent Engine IDs to send traffic to.
        queries: List of (query, complexity) tuples. Uses defaults if None.
        count: Max number of queries to send per agent. Sends all if None.
        interval: Seconds between submissions. 0 submits everything at once.
        eval_data_path: Path to eval YAML to use as query source.
        concurrency: Requests in flight at once. Concurrency does not improve
            the per-attempt success rate (measured, p=0.23) but it does divide
            the wall clock, and the rate is not made worse by it.
        max_attempts: Attempts per query before giving up. At the observed
            ~1-in-4 per attempt, 6 attempts lands a trace ~82% of the time;
            the old value of 3 managed ~58%.
    """
    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
    disable_pyopenssl()

    if eval_data_path:
        cases = load_eval_file(eval_data_path)
        queries = [(c["prompt"], c.get("category", "unknown")) for c in cases]
    elif queries is None:
        queries = DEFAULT_QUERIES

    if count:
        if count > len(queries):
            full_cycles = count // len(queries)
            remainder = count % len(queries)
            queries = queries * full_cycles + queries[:remainder]
        else:
            queries = queries[:count]

    total = len(queries)

    # Pre-load agent connections
    agents = {}
    for agent_id in agent_ids:
        resource = _resolve_resource(agent_id)
        agents[agent_id] = agent_engines.get(resource)

    print(f"{'=' * 60}")
    print("TRAFFIC GENERATOR")
    print(f"{'=' * 60}")
    print(f"  Agents:      {len(agent_ids)} (round-robin)")
    print(f"  Queries:     {total} total")
    print(f"  Concurrency: {concurrency} in flight")
    print(f"  Attempts:    up to {max_attempts} per query")
    print(f"  Interval:    {interval}s between submissions")
    print()

    async def _run_all():
        sem = asyncio.Semaphore(concurrency)
        tasks = []

        async def _submit(i, query, complexity):
            # Stagger submissions so `interval` still means what it did, while
            # the semaphore -- not the sleep -- is what bounds load.
            if interval > 0:
                await asyncio.sleep(i * interval)
            events, attempts, text = await _one_query(
                agents[agent_ids[i % len(agent_ids)]], query, max_attempts, sem
            )
            short = agent_ids[i % len(agent_ids)][-8:]
            head = f"  [{i + 1}/{total}] → {short} ({complexity}) {query[:45]}"
            if events:
                print(f"{head}\n    -> [{events} events, {attempts} attempt(s)] {text[:70]}")
            else:
                print(f"{head}\n    x  no trace after {attempts} attempts {text[:60]}")
            return events, attempts

        for i, (query, complexity) in enumerate(queries):
            tasks.append(asyncio.create_task(_submit(i, query, complexity)))
        return await asyncio.gather(*tasks)

    # One event loop for the whole run rather than one per query: the old shape
    # tore down and rebuilt the HTTP client for every single request.
    outcomes = asyncio.run(_run_all())

    traces = sum(1 for events, _ in outcomes if events)
    attempts = sum(a for _, a in outcomes)

    print(f"\n{'=' * 60}")
    print("TRAFFIC COMPLETE")
    print(f"{'=' * 60}")
    for line in summarize_run(traces, total, attempts):
        print(line)
    print(f"  Agents:         {len(agent_ids)} (round-robin)")


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()
    # parents[2] is the repo root: this file is wrangler/tools/traffic.py, so
    # parent.parent stopped inside `wrangler/` and pointed at a path that has
    # never existed — the example .env was silently never loaded.
    repo_root = Path(__file__).resolve().parents[2]
    example_env = repo_root / "examples" / "multi_model_agents" / ".env"
    if example_env.exists():
        load_dotenv(str(example_env), override=True)

    parser = argparse.ArgumentParser(description="Generate traffic for deployed agents")
    parser.add_argument(
        "--agent-id",
        nargs="+",
        required=True,
        help="Agent Engine ID(s) to send traffic to",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Max number of queries per agent (default: all)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between queries (default: 1.0)",
    )
    parser.add_argument(
        "--eval-data",
        type=str,
        default=None,
        help="Path to eval YAML to use as query source",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=_DEFAULT_CONCURRENCY,
        help=f"Requests in flight at once (default: {_DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=_DEFAULT_MAX_ATTEMPTS,
        help=(
            "Attempts per query before giving up "
            f"(default: {_DEFAULT_MAX_ATTEMPTS}; the engine eats ~3 of 4 requests)"
        ),
    )
    args = parser.parse_args()

    generate_traffic(
        agent_ids=args.agent_id,
        count=args.count,
        interval=args.interval,
        eval_data_path=args.eval_data,
        concurrency=args.concurrency,
        max_attempts=args.max_attempts,
    )
