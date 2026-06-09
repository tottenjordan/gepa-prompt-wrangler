#!/usr/bin/env python3
"""Smoke test — validates each pipeline component with real API calls before a full run.

Tests in order:
  1. MCP server connectivity (can the agent load tools from all 3 servers?)
  2. Sampler config validation (does ADK accept the sampler config?)
  3. Single-agent eval (2 cases against lite engine)
  4. GEPA optimization dry run (loads agent locally, runs 1 metric call)

Usage:
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --skip-optimize   # skip the slow GEPA test
"""

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples", "multi_model_agents"))

os.environ.setdefault("ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS", "true")

import warnings
warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*")
warnings.filterwarnings("ignore", message=".*GEMINI_VIA_LITELLM.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="vertexai")

LITE_ENGINE_ID = "4981388556929859584"
MANIFEST_PATH = "examples/multi_model_agents/manifest.yaml"
EVAL_DATA_PATH = "examples/multi_model_agents/eval_data/eval_cases.yaml"
LITE_OPT_DIR = "examples/multi_model_agents/agents/lite_opt"
SAMPLER_CONFIG = "examples/multi_model_agents/agents/lite_opt/sampler_config.json"

SMOKE_CASES = [
    {
        "prompt": "Find flights from SFO to JFK",
        "expected_response": "I found flights from SFO to JFK.",
        "tier": "low",
        "category": "search",
        "expected_tools": [{"name": "wrangler_search_mcp_search_flights"}],
    },
    {
        "prompt": "What is the corporate travel policy for international flights?",
        "expected_response": "International flights require manager approval.",
        "tier": "low",
        "category": "policy",
        "expected_tools": [],
    },
]


def _header(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def _pass(msg):
    print(f"  PASS: {msg}")


def _fail(msg):
    print(f"  FAIL: {msg}")


def test_mcp_connectivity():
    """Test 1: Can we load MCP toolsets from all 3 servers via Agent Registry?"""
    _header("Test 1: MCP Server Connectivity")

    from config import SEARCH_MCP_SERVER, BOOKING_MCP_SERVER, EXPENSE_MCP_SERVER
    from registry import get_mcp_tools

    servers = {
        "search": SEARCH_MCP_SERVER,
        "booking": BOOKING_MCP_SERVER,
        "expense": EXPENSE_MCP_SERVER,
    }

    all_ok = True
    for name, server in servers.items():
        t0 = time.time()
        try:
            toolset = get_mcp_tools(server)
            elapsed = time.time() - t0
            _pass(f"{name} MCP server loaded ({elapsed:.1f}s) — {server}")
        except Exception as e:
            elapsed = time.time() - t0
            _fail(f"{name} MCP server FAILED ({elapsed:.1f}s) — {e}")
            all_ok = False

    return all_ok


def test_sampler_config():
    """Test 2: Does ADK accept the sampler config without validation errors?"""
    _header("Test 2: Sampler Config Validation")

    import json
    from wrangler.optimize.optimizer import _patch_adk
    _patch_adk()

    from google.adk.optimization.local_eval_sampler import LocalEvalSamplerConfig

    with open(SAMPLER_CONFIG) as f:
        config = json.load(f)

    try:
        cfg = LocalEvalSamplerConfig.model_validate(config)
        _pass(f"LocalEvalSamplerConfig validated — app_name={cfg.app_name}, "
              f"train_eval_set={cfg.train_eval_set}")

        criteria = config.get("eval_config", {}).get("criteria", {})
        for k, v in criteria.items():
            if isinstance(v, dict):
                threshold = v.get("threshold", "MISSING")
                print(f"    {k}: threshold={threshold}")
            else:
                print(f"    {k}: {v} (scalar)")

        return True
    except Exception as e:
        _fail(f"Sampler config validation failed: {e}")
        traceback.print_exc()
        return False


def test_single_eval():
    """Test 3: Run a real eval with 2 cases against the lite agent engine."""
    _header("Test 3: Single-Agent Eval (2 cases)")

    from wrangler.eval.evaluator import run_batch_eval

    t0 = time.time()
    try:
        result = run_batch_eval(
            engine_id=LITE_ENGINE_ID,
            eval_cases=SMOKE_CASES,
            agent_name="smoke-test-lite",
        )
        elapsed = time.time() - t0

        if not result.scores:
            _fail(f"Eval returned no scores ({elapsed:.0f}s)")
            return False

        avg = sum(result.scores.values()) / len(result.scores)
        _pass(f"Eval complete ({elapsed:.0f}s) — {len(result.scores)} metrics, avg={avg:.2f}")
        for m, s in sorted(result.scores.items()):
            print(f"    {m:40s} {s:.2f}")
        return True
    except Exception as e:
        elapsed = time.time() - t0
        _fail(f"Eval failed ({elapsed:.0f}s): {e}")
        traceback.print_exc()
        return False


def test_agent_load():
    """Test 4: Load the lite_opt agent module locally (same as GEPA does)."""
    _header("Test 4: Local Agent Module Load")

    import importlib.util

    agent_path = os.path.abspath(LITE_OPT_DIR)
    init_file = os.path.join(agent_path, "__init__.py")

    if not os.path.exists(init_file):
        _fail(f"__init__.py not found at {init_file}")
        return False

    try:
        spec = importlib.util.spec_from_file_location("agent_mod", init_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules["agent_mod"] = module
        spec.loader.exec_module(module)

        root_agent = None
        if hasattr(module, "agent") and hasattr(module.agent, "root_agent"):
            root_agent = module.agent.root_agent
        elif hasattr(module, "root_agent"):
            root_agent = module.root_agent

        if root_agent is None:
            _fail("Could not find root_agent in module")
            return False

        _pass(f"Agent loaded: name={root_agent.name}, model={root_agent.model}")

        tools = getattr(root_agent, "tools", [])
        print(f"    Tools registered: {len(tools)}")
        for t in tools:
            tname = getattr(t, "name", type(t).__name__)
            print(f"      - {tname}")

        return True
    except Exception as e:
        _fail(f"Agent load failed: {e}")
        traceback.print_exc()
        return False


def test_gepa_optimize():
    """Test 5: Run a minimal GEPA optimization (1 metric call max)."""
    _header("Test 5: GEPA Optimization (minimal)")

    import json
    import vertexai
    from wrangler.core.config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    from wrangler.optimize.optimizer import _patch_adk
    _patch_adk()

    from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
    from google.adk.optimization.gepa_root_agent_prompt_optimizer import (
        GEPARootAgentPromptOptimizer,
        GEPARootAgentPromptOptimizerConfig,
    )
    from google.adk.optimization.local_eval_sampler import (
        LocalEvalSampler,
        LocalEvalSamplerConfig,
    )
    import asyncio
    import importlib.util

    agent_path = os.path.abspath(LITE_OPT_DIR)
    agents_dir = os.path.dirname(agent_path)
    app_name = os.path.basename(agent_path)

    # Load agent
    init_file = os.path.join(agent_path, "__init__.py")
    spec = importlib.util.spec_from_file_location("agent_mod_gepa", init_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_mod_gepa"] = module
    spec.loader.exec_module(module)

    root_agent = module.agent.root_agent

    # Load sampler config
    with open(SAMPLER_CONFIG) as f:
        sampler_config = json.load(f)

    sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
    if sampler_cfg.app_name != app_name:
        sampler_cfg.app_name = app_name

    run_dir = os.path.join("outputs", "gepa_runs", "smoke_test")
    os.makedirs(run_dir, exist_ok=True)

    # Use minimal config: 1 metric call to test the full loop quickly
    optimizer_config = GEPARootAgentPromptOptimizerConfig(
        run_dir=run_dir,
        max_metric_calls=1,
    )

    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    sampler = LocalEvalSampler(sampler_cfg, eval_sets_manager)

    train_ids = sampler.get_train_example_ids()
    val_ids = sampler.get_validation_example_ids()
    print(f"    Train: {len(train_ids)} cases, Val: {len(val_ids)} cases")
    print(f"    Max metric calls: {optimizer_config.max_metric_calls}")

    optimizer = GEPARootAgentPromptOptimizer(optimizer_config)

    t0 = time.time()
    try:
        result = asyncio.run(optimizer.optimize(root_agent, sampler))
        elapsed = time.time() - t0

        best_idx = result.gepa_result["best_idx"]
        best = result.optimized_agents[best_idx]
        prompt = best.optimized_agent.instruction
        _pass(f"GEPA complete ({elapsed:.0f}s) — best variant {best_idx}, "
              f"score={best.overall_score:.3f}, prompt={len(prompt)} chars")
        return True
    except Exception as e:
        elapsed = time.time() - t0
        _fail(f"GEPA failed ({elapsed:.0f}s): {e}")
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Pipeline smoke test")
    parser.add_argument("--skip-optimize", action="store_true",
                        help="Skip the GEPA optimization test (slowest)")
    parser.add_argument("--skip-eval", action="store_true",
                        help="Skip the batch eval test")
    args = parser.parse_args()

    print("GEPA Prompt Wrangler — Smoke Test")
    print(f"  Engine: {LITE_ENGINE_ID}")
    print(f"  Manifest: {MANIFEST_PATH}")

    results = {}
    t0 = time.time()

    results["mcp"] = test_mcp_connectivity()
    results["sampler"] = test_sampler_config()

    if not args.skip_eval:
        results["eval"] = test_single_eval()

    results["agent_load"] = test_agent_load()

    if not args.skip_optimize:
        results["gepa"] = test_gepa_optimize()

    elapsed = time.time() - t0
    _header(f"Results ({elapsed:.0f}s)")

    all_pass = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: {name}")

    if all_pass:
        print(f"\n  All tests passed. Safe to run the full pipeline.")
    else:
        print(f"\n  Some tests FAILED. Fix issues before running the pipeline.")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
