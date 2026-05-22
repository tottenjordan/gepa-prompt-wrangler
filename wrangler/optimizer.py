"""GEPA optimization — wraps the ADK GEPARootAgentPromptOptimizer with patches."""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _patch_adk():
    """Apply ADK patches for GEPA compatibility."""
    from google.adk.evaluation import eval_case as _ec, eval_set as _es
    for _mod in (_ec, _es):
        for _name in dir(_mod):
            _cls = getattr(_mod, _name)
            if isinstance(_cls, type) and hasattr(_cls, "model_config"):
                try:
                    if _cls.model_config.get("extra") == "forbid":
                        _cls.model_config["extra"] = "ignore"
                        _cls.__pydantic_complete__ = False
                except (TypeError, AttributeError):
                    pass
    for _mod in (_ec, _es):
        for _name in dir(_mod):
            _cls = getattr(_mod, _name)
            if isinstance(_cls, type) and hasattr(_cls, "model_rebuild"):
                try:
                    _cls.model_rebuild(force=True)
                except Exception:
                    pass

    from google.adk.evaluation import local_eval_service as les
    _orig = les.LocalEvalService._evaluate_single_inference_result

    async def _patched(self, inference_result, evaluate_config):
        if inference_result.inferences is None:
            from google.adk.evaluation.eval_result import EvalCaseResult, EvalStatus
            return inference_result, EvalCaseResult(
                eval_id=inference_result.eval_case_id,
                eval_set_id=inference_result.eval_set_id,
                final_eval_status=EvalStatus.NOT_EVALUATED,
                overall_eval_metric_results=[],
                eval_metric_result_per_invocation=[],
                session_id="skipped",
            )
        return await _orig(self, inference_result=inference_result, evaluate_config=evaluate_config)

    les.LocalEvalService._evaluate_single_inference_result = _patched

    from google.adk.optimization import local_eval_sampler as sampler_mod
    _orig_extract = sampler_mod.LocalEvalSampler._extract_eval_data

    def _patched_extract(self, eval_set_id, eval_results):
        for case_result in eval_results:
            for inv in getattr(case_result, "eval_metric_result_per_invocation", []):
                for mr in getattr(inv, "eval_metric_results", []):
                    if mr.score is None:
                        mr.score = 0.0
        return _orig_extract(self, eval_set_id, eval_results)

    sampler_mod.LocalEvalSampler._extract_eval_data = _patched_extract
    log.info("ADK patches applied")


def _create_wrapper_module(agent_module_path: str, temp_dir: str) -> str:
    """Create a temporary wrapper that exposes the agent as root_agent."""
    wrapper_dir = os.path.join(temp_dir, "wrapper")
    os.makedirs(wrapper_dir, exist_ok=True)

    init_content = f"""
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location("_agent", "{agent_module_path}/__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root_agent = mod.agent.root_agent
agent = types.SimpleNamespace(root_agent=root_agent)
"""
    with open(os.path.join(wrapper_dir, "__init__.py"), "w") as f:
        f.write(init_content)

    return wrapper_dir


def optimize(
    agent_module_path: str,
    evalset_path: str = None,
    sampler_config_path: str = None,
    eval_data_path: str = None,
) -> str:
    """Run GEPA optimization. Returns the optimized instruction string.

    Args:
        agent_module_path: Path to agent wrapper module (must export agent.root_agent)
        evalset_path: Path to evalset JSON (ignored if sampler_config_path is set)
        sampler_config_path: Path to sampler config JSON file
        eval_data_path: Path to simplified eval YAML (for auto-generating GEPA evalset)
    """
    print("  [1/3] Applying ADK patches...")
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

    print("  [2/3] Loading agent and configs...")
    agent_module_path = str(Path(agent_module_path).resolve())
    init_file = os.path.join(agent_module_path, "__init__.py")
    if not os.path.exists(init_file):
        raise FileNotFoundError(
            f"Agent module not found: {init_file}\n"
            f"  The optimizer expects a directory with an __init__.py that exports agent.root_agent.\n"
            f"  Directory contents: {os.listdir(agent_module_path) if os.path.isdir(agent_module_path) else 'NOT A DIRECTORY'}"
        )

    spec = importlib.util.spec_from_file_location("agent_mod", init_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_mod"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise RuntimeError(
            f"Failed to import agent module at {init_file}: {e}\n"
            f"  Common causes:\n"
            f"    - Missing dependencies (check imports in __init__.py)\n"
            f"    - Relative imports that don't resolve (use absolute paths or sys.path)"
        ) from e

    root_agent = None
    if hasattr(module, "agent") and hasattr(module.agent, "root_agent"):
        root_agent = module.agent.root_agent
    elif hasattr(module, "root_agent"):
        root_agent = module.root_agent

    if root_agent is None:
        exports = [k for k in dir(module) if not k.startswith("_")]
        raise ValueError(
            f"Could not find root_agent in {agent_module_path}\n"
            f"  Module exports: {exports}\n"
            f"  Expected: agent.root_agent (SimpleNamespace) or root_agent (LlmAgent)"
        )
    print(f"    Agent: {root_agent.name}")

    app_name = os.path.basename(agent_module_path)
    agents_dir = os.path.dirname(agent_module_path)

    if sampler_config_path:
        import json as _json
        with open(sampler_config_path) as f:
            sampler_config = _json.load(f)
    else:
        evalset_stem = Path(evalset_path).stem if evalset_path else "eval_set"
        if evalset_stem.endswith(".evalset"):
            evalset_stem = evalset_stem[:-len(".evalset")]
        sampler_config = {
            "eval_config": {
                "criteria": {
                    "response_match_score": 0.1,
                    "final_response_match_v2": {
                        "threshold": 0.5,
                        "judge_model_options": {"judge_model": "gemini-2.5-pro"},
                    },
                    "safety_v1": 0.8,
                }
            },
            "app_name": app_name,
            "train_eval_set": evalset_stem,
        }

    try:
        sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
    except Exception as e:
        raise ValueError(
            f"Invalid sampler config: {e}\n"
            f"  Config: {json.dumps(sampler_config, indent=2) if 'json' in dir() else sampler_config}\n"
            f"  If using a sampler_config.json, check that app_name and train_eval_set match your directory structure."
        ) from e

    if sampler_cfg.app_name != app_name:
        sampler_cfg.app_name = app_name

    evalset_dir = os.path.join(agents_dir, app_name)
    evalset_files = [f for f in os.listdir(evalset_dir) if f.endswith(".evalset.json")] if os.path.isdir(evalset_dir) else []
    if not evalset_files and eval_data_path:
        print(f"    No evalset files in {evalset_dir} — auto-generating from {eval_data_path}")
        from .converter import load_eval_file, generate_gepa_evalset, generate_sampler_config
        cases = load_eval_file(eval_data_path)
        eval_set_id = f"{app_name}_eval_set"
        generate_gepa_evalset(cases, evalset_dir, eval_set_id=eval_set_id, app_name=app_name)
        if not sampler_config_path:
            generate_sampler_config(app_name, eval_set_id, output_dir=evalset_dir)
            sampler_config_path_auto = os.path.join(evalset_dir, "sampler_config.json")
            with open(sampler_config_path_auto) as f:
                sampler_config = json.load(f)
            sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
            if sampler_cfg.app_name != app_name:
                sampler_cfg.app_name = app_name
        print(f"    Auto-generated evalset at {evalset_dir}")
    elif not evalset_files:
        log.warning(f"No .evalset.json files found in {evalset_dir}. GEPA may fail.")
        print(f"    WARNING: No evalset files in {evalset_dir}")
        print(f"    Run: wrangler generate-evalset --from <eval.yaml> --output {evalset_dir}")

    optimizer_config = GEPARootAgentPromptOptimizerConfig()
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    sampler = LocalEvalSampler(sampler_cfg, eval_sets_manager)
    optimizer = GEPARootAgentPromptOptimizer(optimizer_config)

    print("  [3/3] Running GEPA optimization...")
    try:
        optimization_result = asyncio.run(optimizer.optimize(root_agent, sampler))
    except Exception as e:
        error_msg = str(e)
        if "ValidationError" in type(e).__name__ or "validation" in error_msg.lower():
            raise RuntimeError(
                f"GEPA optimization failed with validation error: {e}\n"
                f"  Common causes:\n"
                f"    - Evalset JSON has fields GEPA doesn't expect (check .evalset.json format)\n"
                f"    - Tool names in evalset don't match agent's actual tool names\n"
                f"    - Run: wrangler inspect <agent_dir> to see correct tool names"
            ) from e
        raise

    best_idx = optimization_result.gepa_result["best_idx"]
    best_agent = optimization_result.optimized_agents[best_idx]
    optimized_instruction = best_agent.optimized_agent.instruction

    print(f"  Best variant: {best_idx}")
    return optimized_instruction
