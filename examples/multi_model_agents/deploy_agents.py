"""Deploy all 5 standalone agents to GEAP with wrangler- prefix."""

import os
import sys

# Ensure local imports work
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "agents"))

from dotenv import load_dotenv

load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

import vertexai
from config import (
    FLASH_MODEL,
    GCP_PROJECT_ID,
    GCP_REGION,
    GCP_STAGING_BUCKET,
    LITE_MODEL,
    OPUS_MODEL,
    PRO_MODEL,
    SONNET_MODEL,
)

os.chdir(SCRIPT_DIR)
vertexai.init(
    project=GCP_PROJECT_ID, location=GCP_REGION, staging_bucket=f"gs://{GCP_STAGING_BUCKET}"
)

# name -> (agent module, model id). The model is read from config rather than
# off the imported agent, because the agent holds the *resolved* model (an ADK
# Gemini()/Claude() object for the 3.x and Claude tiers) and the build package
# wants the plain id.
AGENTS = {
    "lite": ("lite_agent", LITE_MODEL),
    "flash": ("flash_agent", FLASH_MODEL),
    "pro": ("pro_agent", PRO_MODEL),
    "sonnet": ("sonnet_agent", SONNET_MODEL),
    "opus": ("opus_agent", OPUS_MODEL),
}

ENV_FILE = os.path.join(SCRIPT_DIR, ".env")


def update_env(key: str, value: str):
    lines = []
    found = False
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)


def deploy_single(
    name: str,
    generic: bool = False,
    update: bool = False,
    version: str = "v4",
    gate: bool = True,
):
    """Deploy or update one tier, then check it can actually serve.

    The gate is on by default because a deploy is a lottery: ten byte-identical
    engines measured 0%-100% reach (docs/doe/01-engine-lottery.md), and a dead
    one answers HTTP 200 with no inference rather than an error. This script
    shipped exactly that on 2026-08-31 -- an in-place update rerolled `lite`
    to 0/15 and `opus` to 4/15, and nothing noticed until they were probed by
    hand. Redeploying redraws the rate, so a failure here is worth retrying.
    """
    from wrangler.core.deploy import deploy_agent_from_source, update_agent_from_source

    module_name, model = AGENTS[name]

    if generic:
        from generic_prompts import GENERIC_PROMPT

        instruction = GENERIC_PROMPT
        print("  Using generic prompt")
    else:
        # The agent module defines INSTRUCTION at import time from prompts/.
        instruction = __import__(module_name, fromlist=["INSTRUCTION"]).INSTRUCTION

    agent_module = os.path.join(SCRIPT_DIR, "agents", module_name)
    display_name = f"wrangler-{name}-agent-{version}"

    engine_id = os.environ.get(f"{name.upper()}_ENGINE_ID", "") if update else ""
    if update and not engine_id:
        print(f"  No ENGINE_ID for {name}, deploying new instead")

    if engine_id:
        update_agent_from_source(
            engine_id=engine_id,
            agent_module=agent_module,
            model=model,
            instruction=instruction,
            display_name=display_name,
        )
    else:
        engine_id = deploy_agent_from_source(
            agent_module=agent_module,
            model=model,
            instruction=instruction,
            display_name=display_name,
        )

    if gate:
        from wrangler.orchestration.stages import gate_engine_health

        def _redeploy():
            # Always a *fresh* deploy, never an in-place update: the point is a
            # new draw, and an update of a bad engine is what produced one.
            return deploy_agent_from_source(
                agent_module=agent_module,
                model=model,
                instruction=instruction,
                display_name=display_name,
            )

        from wrangler.tools.engines import delete_engine

        was_update = bool(update)
        health = gate_engine_health(engine_id, redeploy_fn=_redeploy, discard_fn=delete_engine)
        # The gate never discards the engine handed to it. On a fresh deploy we
        # made that one too, so a rejected first draw is ours to clean up; on
        # --update it is a pre-existing deployment and must be left alone.
        if not was_update:
            for stale in health["rejected"]:
                if stale != health["engine_id"]:
                    try:
                        delete_engine(stale)
                        print(f"  Discarded rejected engine {stale}")
                    except Exception as exc:
                        print(f"  Could not discard {stale}: {type(exc).__name__}: {exc}")
        engine_id = health["engine_id"]
        if not health["passed"]:
            print(
                f"  WARNING: {name} is below the health bar after {health['rerolls']} "
                f"reroll(s) at {health['rate']:.0%} reach. Evals against it will lose "
                f"cases. See docs/doe/01-engine-lottery.md."
            )

    env_key = f"{name.upper()}_ENGINE_ID"
    update_env(env_key, engine_id)
    print(f"  .env: {env_key}={engine_id}")
    return engine_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="*", default=list(AGENTS.keys()))
    parser.add_argument(
        "--generic", action="store_true", help="Use generic prompts instead of optimized"
    )
    parser.add_argument(
        "--update", action="store_true", help="Update existing agents instead of creating new"
    )
    parser.add_argument(
        "--version", default="v4", help="Version tag for display name (default: v4)"
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Skip the post-deploy health check. Off by default because a deploy is "
        "a lottery and a dead engine returns 200 with no inference.",
    )
    args = parser.parse_args()

    print(f"Project: {GCP_PROJECT_ID}")
    print(f"Region: {GCP_REGION}")
    print(f"Bucket: {GCP_STAGING_BUCKET}")
    print(f"Agents: {args.agents}\n")

    for name in args.agents:
        if name not in AGENTS:
            print(f"  Unknown agent: {name}. Available: {list(AGENTS.keys())}")
            continue
        action = "Updating" if args.update else "Deploying"
        print(f"\n--- {action} {name} {'(generic prompt)' if args.generic else ''} ---")
        deploy_single(
            name,
            generic=args.generic,
            update=args.update,
            version=args.version,
            gate=not args.no_gate,
        )

    print("\nDone.")
