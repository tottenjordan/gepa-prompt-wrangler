"""Traffic generator — send test queries to deployed agents for OTel trace generation.

Creates a new session with a unique user ID per query to keep traces independent.

Usage:
    # Send all 30 eval cases to one agent
    uv run python -m wrangler.traffic --agent-id 4981388556929859584

    # Send to multiple agents
    uv run python -m wrangler.traffic --agent-id 4981388556929859584 6589173623901126656

    # Control rate and count
    uv run python -m wrangler.traffic --agent-id 4981388556929859584 --count 10 --interval 5

    # Use custom eval data
    uv run python -m wrangler.traffic --agent-id 4981388556929859584 --eval-data eval_data/my_cases.yaml

    # Steady state: 1 query every 3 seconds for 5 minutes
    uv run python -m wrangler.traffic --agent-id 4981388556929859584 --count 100 --interval 3
"""

import argparse
import os
import random
import sys
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
    print(f"TRAFFIC GENERATOR")
    print(f"{'=' * 60}")
    print(f"  Agents:    {len(agent_ids)} (round-robin)")
    print(f"  Queries:   {total} total")
    print(f"  Interval:  {interval}s between queries")
    print(f"  Sessions:  new session + unique user per query")
    print()

    for i, (query, complexity) in enumerate(queries):
        query_num += 1
        agent_id = agent_ids[i % len(agent_ids)]
        agent = agents[agent_id]
        agent_short = agent_id[-8:]
        user_id = f"traffic-{uuid.uuid4().hex[:8]}"

        print(f"  [{query_num}/{total}] → {agent_short} ({complexity}) {query[:55]}")

        try:
            session = agent.create_session(user_id=user_id)
            session_id = session["id"] if isinstance(session, dict) else session.id

            response = agent.stream_query(
                user_id=user_id,
                session_id=session_id,
                message=query,
            )
            full_response = ""
            for chunk in response:
                if hasattr(chunk, "text"):
                    full_response += chunk.text
                elif isinstance(chunk, dict) and "text" in chunk:
                    full_response += chunk["text"]

            print(f"    -> {full_response[:80]}...")
        except Exception as e:
            errors += 1
            print(f"    x Error: {e}")

        if interval > 0 and i < len(queries) - 1:
            time.sleep(interval)

    print(f"\n{'=' * 60}")
    print(f"TRAFFIC COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Total queries: {query_num}")
    print(f"  Errors:        {errors}")
    print(f"  Agents:        {len(agent_ids)} (round-robin)")


if __name__ == "__main__":
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv()
    example_env = Path(__file__).parent.parent / "examples" / "multi_model_agents" / ".env"
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
