"""Traffic generator — send test queries to deployed agents for OTel trace generation.

Creates a new session with a unique user ID per query to keep traces independent.

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
import time
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


# GEAP routes a request to a worker that has not finished booting, and that
# request comes back HTTP 200 with an empty event stream -- no error, no trace.
# Confirmed in the ReasoningEngine logs: a worker logged "Application startup
# complete" and served "POST /api/stream_reasoning_engine 200 OK" in the same
# second, while a warm worker handling the neighbouring request logged a real
# rawPredict to the model. Startup here is ~8s: three MCP handshakes.
#
# A retry lands on a different (by then warm) worker. Setting GEAP_MIN_INSTANCES
# on the deployment makes it rarer but cannot remove it -- scaling up under load
# creates cold workers whatever the floor is.
_EMPTY_STREAM_RETRIES = 2


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
    this is future-proofing, not a workaround. See ``_EMPTY_STREAM_RETRIES``
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


def generate_traffic(
    agent_ids: list[str],
    queries: list[tuple[str, str]] | None = None,
    count: int | None = None,
    interval: float = 1.0,
    eval_data_path: str | None = None,
):
    """Send queries to deployed agents with unique sessions per query.

    Args:
        agent_ids: List of Agent Engine IDs to send traffic to.
        queries: List of (query, complexity) tuples. Uses defaults if None.
        count: Max number of queries to send per agent. Sends all if None.
        interval: Seconds between queries.
        eval_data_path: Path to eval YAML to use as query source.
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
    errors = 0
    query_num = 0

    # Pre-load agent connections
    agents = {}
    for agent_id in agent_ids:
        resource = _resolve_resource(agent_id)
        agents[agent_id] = agent_engines.get(resource)

    print(f"{'=' * 60}")
    print("TRAFFIC GENERATOR")
    print(f"{'=' * 60}")
    print(f"  Agents:    {len(agent_ids)} (round-robin)")
    print(f"  Queries:   {total} total")
    print(f"  Interval:  {interval}s between queries")
    print("  Sessions:  new session + unique user per query")
    print()

    for i, (query, complexity) in enumerate(queries):
        query_num += 1
        agent_id = agent_ids[i % len(agent_ids)]
        agent = agents[agent_id]
        agent_short = agent_id[-8:]
        user_id = f"traffic-{uuid.uuid4().hex[:8]}"

        print(f"  [{query_num}/{total}] → {agent_short} ({complexity}) {query[:55]}")

        try:
            for attempt in range(_EMPTY_STREAM_RETRIES + 1):
                # AgentEngine proxies the ADK class_methods list at runtime, so
                # these attributes do not exist statically. Each attempt gets a
                # fresh session: the empty-stream worker may have consumed the
                # old one.
                session = agent.create_session(  # ty: ignore[unresolved-attribute]
                    user_id=user_id
                )
                session_id = session["id"] if isinstance(session, dict) else session.id

                full_response, event_count = asyncio.run(_stream(agent, user_id, session_id, query))
                if event_count:
                    break
                if attempt < _EMPTY_STREAM_RETRIES:
                    print("    ~ Empty stream (cold worker?) — retrying")

            # Zero events is a failure, not a quiet success. The point of this
            # tool is to emit traces; an empty stream emits none.
            if event_count == 0:
                errors += 1
                print(f"    x No events after {_EMPTY_STREAM_RETRIES + 1} attempts")
            else:
                print(f"    -> [{event_count} events] {full_response[:80]}...")
        except Exception as e:
            errors += 1
            print(f"    x Error: {e}")

        if interval > 0 and i < len(queries) - 1:
            time.sleep(interval)

    print(f"\n{'=' * 60}")
    print("TRAFFIC COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Total queries: {query_num}")
    print(f"  Errors:        {errors}")
    print(f"  Agents:        {len(agent_ids)} (round-robin)")


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
    args = parser.parse_args()

    generate_traffic(
        agent_ids=args.agent_id,
        count=args.count,
        interval=args.interval,
        eval_data_path=args.eval_data,
    )
