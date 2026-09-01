"""Deploy the 2x2 probe arms for the GEAP request-routing experiment.

Four engines, identical in every respect except the two factors under test:

    arm            toolsets        model
    mcp-claude     3 MCP           DEFAULT_AGENT_MODEL_ALT
    bare-claude    none            DEFAULT_AGENT_MODEL_ALT
    mcp-gemini     3 MCP           DEFAULT_AGENT_MODEL
    bare-gemini    none            DEFAULT_AGENT_MODEL

The *toolsets* factor answers "is it your agent's slow startup?" -- the first
thing anyone will say about the empty-stream defect, and a question no
measurement in this repo can currently answer, because every one of them was
taken against an agent that performs three MCP handshakes at import.

The *model* factor answers "is it Claude, or the global endpoint?" -- the
second-most-likely deflection, and cheap to pre-empt. If a request never
reaches the model, the model should not matter; showing that is worth one
extra pair of engines.

``min_instances`` is passed explicitly rather than left to GEAP_MIN_INSTANCES
in the environment, so the recorded spec states what was actually deployed.

Engine ids are printed, not written anywhere. They go on the probe's command
line. Per CLAUDE.md, nothing in this repo may require a pinned engine id.

Usage:
    uv run python scripts/deploy_probe_arms.py
    uv run python scripts/deploy_probe_arms.py --arm bare-claude   # just one
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)

from wrangler.core.deploy import deploy_agent_from_source  # noqa: E402
from wrangler.core.models import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_MODEL_ALT  # noqa: E402

# Any agent module in the multi-model example works: build_source_package only
# reads config.py/prompts/ from its parent and generates everything else. The
# sonnet module is used for both model arms so the two differ by model alone.
AGENT_MODULE = "examples/multi_model_agents/agents/sonnet_agent"

# Deliberately minimal and identical across arms. The probe asks the agent to
# say one word; a prompt that provoked tool use would put the agent's own
# latency and failure modes inside a measurement about request routing.
INSTRUCTION = (
    "You are a probe agent used to measure request routing. "
    "Reply to every message with exactly the word OK and nothing else."
)

MIN_INSTANCES = 2

# Stamped onto every probe engine, so a sweeper knows which campaign owns it.
# Overridable, because Campaign 01 is worth repeating: its distribution is what
# `max_rerolls` is sized from, and a repeat labelled "01" would be
# indistinguishable from the original at teardown.
CAMPAIGN = "01"

ARMS = {
    "mcp-claude": {"model": DEFAULT_AGENT_MODEL_ALT, "include_mcp": True},
    "bare-claude": {"model": DEFAULT_AGENT_MODEL_ALT, "include_mcp": False},
    "mcp-gemini": {"model": DEFAULT_AGENT_MODEL, "include_mcp": True},
    "bare-gemini": {"model": DEFAULT_AGENT_MODEL, "include_mcp": False},
}


def deploy_arm(arm: str, campaign: str = CAMPAIGN) -> str:
    spec = ARMS[arm]
    print(f"\n{'=' * 64}\n{arm}: model={spec['model']}, mcp={spec['include_mcp']}\n{'=' * 64}")
    t0 = time.time()
    engine_id = deploy_agent_from_source(
        agent_module=AGENT_MODULE,
        model=spec["model"],
        instruction=INSTRUCTION,
        display_name=f"geap-probe-{arm}",
        min_instances=MIN_INSTANCES,
        include_mcp=spec["include_mcp"],
        # Scratch by construction. `wrangler engines` finds these by label
        # rather than by matching a name prefix, so a campaign's engines are
        # reapable even if nobody remembers what they were called.
        labels={"lifecycle": "ephemeral", "campaign": campaign},
    )
    print(f"  {arm}: {engine_id}  ({time.time() - t0:.0f}s)")
    return engine_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy the 2x2 probe arms")
    parser.add_argument(
        "--arm",
        action="append",
        choices=sorted(ARMS),
        default=None,
        help="Deploy only these arms (repeatable). Default: all four.",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=0,
        help=(
            "Deploy N byte-identical copies of one arm instead of the 2x2. With one "
            "engine per cell, engine identity and cell are confounded; identical "
            "replicates are the only way to tell a factor effect from a per-deployment "
            "one. See docs/doe/01-engine-lottery.md"
        ),
    )
    parser.add_argument(
        "--campaign",
        default=CAMPAIGN,
        help=(
            "Campaign label stamped on every engine, so `wrangler engines prune` can "
            f"find this batch specifically (default: {CAMPAIGN})"
        ),
    )
    args = parser.parse_args()

    if args.replicates:
        # One arm, N times. bare-gemini by default: no toolsets, so the fastest
        # deploy and the toolset variable is removed from the question entirely.
        base = (args.arm or ["bare-gemini"])[0]
        for i in range(1, args.replicates + 1):
            ARMS[f"lottery-{i:02d}"] = dict(ARMS[base])
        wanted = [f"lottery-{i:02d}" for i in range(1, args.replicates + 1)]
        print(f"Campaign 01: {args.replicates} byte-identical copies of {base}\n")
    else:
        wanted = args.arm or list(ARMS)

    deployed: dict[str, str] = {}
    failed: dict[str, str] = {}
    for arm in wanted:
        try:
            deployed[arm] = deploy_arm(arm, campaign=args.campaign)
        except Exception as exc:
            # Keep going: three arms plus a stated gap beats losing the run to
            # one bad deploy and having to redo the others.
            failed[arm] = f"{type(exc).__name__}: {exc}"
            print(f"  {arm}: FAILED -- {failed[arm]}")

    print(f"\n{'=' * 64}\nPROBE ARMS\n{'=' * 64}")
    for arm, engine_id in deployed.items():
        print(f"  {arm:14s} {engine_id}")
    for arm, err in failed.items():
        print(f"  {arm:14s} FAILED: {err}")

    if deployed:
        args_line = " ".join(f"--arm {a}={e}" for a, e in deployed.items())
        print(f"\nNext:\n  uv run python -m wrangler.tools.boot_probe {args_line} \\")
        print("      --n 120 --spacing 5 --block-size 30")

    sys.exit(1 if failed else 0)
