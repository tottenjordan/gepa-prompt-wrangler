"""End-to-end GEPA optimization demo — generic prompt → eval → optimize → redeploy → eval.

Usage:
    uv run python examples/multi_model_agents/run_demo.py
    uv run python examples/multi_model_agents/run_demo.py --agents lite flash
    uv run python examples/multi_model_agents/run_demo.py --skip-deploy  # if already deployed
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "agents"))
os.chdir(SCRIPT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

from generic_prompts import GENERIC_PROMPT, AGENT_GENERIC_PROMPTS
from config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET, resolve_model

AGENTS = {
    "lite": {"module": "lite_agent", "attr": "lite_agent", "model": os.environ.get("LITE_MODEL", "gemini-3.1-flash-lite")},
    "flash": {"module": "flash_agent", "attr": "flash_agent", "model": os.environ.get("FLASH_MODEL", "gemini-3.5-flash")},
    "pro": {"module": "pro_agent", "attr": "pro_agent", "model": os.environ.get("PRO_MODEL", "gemini-3.1-pro-preview")},
    "sonnet": {"module": "sonnet_agent", "attr": "sonnet_agent", "model": os.environ.get("SONNET_MODEL", "claude-sonnet-4-6")},
    "opus": {"module": "opus_agent", "attr": "opus_agent", "model": os.environ.get("OPUS_MODEL", "claude-opus-4-6")},
}

ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
EVAL_DATA = os.path.join(SCRIPT_DIR, "eval_data", "eval_cases.yaml")
RESULTS_DIR = Path("outputs")


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


def load_agent(name: str, prompt: str):
    """Import agent and override its instruction."""
    info = AGENTS[name]
    mod = __import__(info["module"], fromlist=[info["attr"]])
    agent = getattr(mod, info["attr"])
    agent.instruction = prompt
    agent.model = resolve_model(info["model"])
    return agent


def run_demo(agent_names: list[str], skip_deploy: bool = False):
    import vertexai
    vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION, staging_bucket=f"gs://{GCP_STAGING_BUCKET}")

    # Add wrangler lib to path
    sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", ".."))
    from wrangler.eval.evaluator import run_batch_eval
    from wrangler.core.deploy import deploy_agent, update_agent
    from wrangler.converter import load_eval_file
    from wrangler.core.config import disable_pyopenssl
    disable_pyopenssl()

    eval_cases = load_eval_file(EVAL_DATA)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    results = {}
    engine_ids = {}

    print(f"\n{'=' * 60}")
    print(f"GEPA PROMPT WRANGLER — E2E DEMO")
    print(f"{'=' * 60}")
    print(f"  Project:  {GCP_PROJECT_ID}")
    print(f"  Agents:   {agent_names}")
    print(f"  Eval:     {len(eval_cases)} cases")
    print(f"  Prompt:   \"{GENERIC_PROMPT[:50]}...\"")

    # --- Phase 1: Deploy with generic prompts ---
    if not skip_deploy:
        print(f"\n--- Phase 1: Deploy with Generic Prompts ---")
        for name in agent_names:
            print(f"\n  [{name}]")
            agent = load_agent(name, GENERIC_PROMPT)
            eid = deploy_agent(agent, display_name=f"wrangler-{name}-agent")
            engine_ids[name] = eid
            update_env(f"{name.upper()}_ENGINE_ID", eid)
    else:
        print(f"\n--- Phase 1: Skipping deploy (using existing) ---")
        for name in agent_names:
            eid = os.environ.get(f"{name.upper()}_ENGINE_ID", "")
            if not eid:
                print(f"  {name}: no ENGINE_ID in .env, skipping")
                continue
            engine_ids[name] = eid
            print(f"  {name}: {eid}")

    # --- Phase 2: Baseline eval ---
    print(f"\n--- Phase 2: Baseline Evaluation ---")
    for name in agent_names:
        if name not in engine_ids:
            continue
        print(f"\n  [{name}]")
        scores = run_batch_eval(engine_ids[name], eval_cases)
        results[name] = {
            "model": AGENTS[name]["model"],
            "engine_id": engine_ids[name],
            "original_prompt": GENERIC_PROMPT,
            "before": scores,
        }
        for m, s in sorted(scores.items()):
            print(f"    {m:40s} {s:.2f}")

    # Save baseline
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = RESULTS_DIR / f"demo_baseline_{timestamp}.json"
    with open(baseline_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Baseline saved: {baseline_path}")

    # --- Phase 3: GEPA optimize (placeholder) ---
    print(f"\n--- Phase 3: GEPA Optimization ---")
    print(f"  (Run optimizer separately for each agent)")
    print(f"  Command: wrangler optimize manifest.yaml --pair <name>")

    # --- Phase 4-6 would continue after optimization ---
    print(f"\n--- Phases 4-6: Redeploy → Re-eval → Report ---")
    print(f"  After optimization, run:")
    print(f"    uv run python run_demo.py --skip-deploy --agents {' '.join(agent_names)}")
    print(f"  This will eval the optimized agents and generate the final report.")

    print(f"\n{'=' * 60}")
    print(f"BASELINE COMPLETE")
    print(f"{'=' * 60}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E2E GEPA optimization demo")
    parser.add_argument("--agents", nargs="*", default=list(AGENTS.keys()))
    parser.add_argument("--skip-deploy", action="store_true", help="Skip deployment, use existing engine IDs")
    args = parser.parse_args()

    run_demo(args.agents, skip_deploy=args.skip_deploy)
