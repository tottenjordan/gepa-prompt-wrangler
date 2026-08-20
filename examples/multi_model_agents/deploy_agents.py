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
from config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

os.chdir(SCRIPT_DIR)
vertexai.init(
    project=GCP_PROJECT_ID, location=GCP_REGION, staging_bucket=f"gs://{GCP_STAGING_BUCKET}"
)

AGENTS = {
    "lite": ("lite_agent", "lite_agent"),
    "flash": ("flash_agent", "flash_agent"),
    "pro": ("pro_agent", "pro_agent"),
    "sonnet": ("sonnet_agent", "sonnet_agent"),
    "opus": ("opus_agent", "opus_agent"),
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


def deploy_single(name: str, generic: bool = False, update: bool = False, version: str = "v4"):
    module_name, agent_attr = AGENTS[name]
    mod = __import__(module_name, fromlist=[agent_attr])
    agent = getattr(mod, agent_attr)

    if generic:
        from generic_prompts import GENERIC_PROMPT

        agent.instruction = GENERIC_PROMPT
        print(f"  Using generic prompt")

    display_name = f"wrangler-{name}-agent-{version}"

    if update:
        engine_id = os.environ.get(f"{name.upper()}_ENGINE_ID", "")
        if not engine_id:
            print(f"  No ENGINE_ID for {name}, deploying new instead")
            from wrangler.core.deploy import deploy_agent

            engine_id = deploy_agent(agent, display_name=display_name)
        else:
            from wrangler.core.deploy import update_agent

            update_agent(agent, engine_id, display_name=display_name)
    else:
        from wrangler.core.deploy import deploy_agent

        engine_id = deploy_agent(agent, display_name=display_name)

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
        deploy_single(name, generic=args.generic, update=args.update, version=args.version)

    print("\nDone.")
