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
    evalset_path: str,
    sampler_config: dict | None = None,
) -> str:
    """Run GEPA optimization. Returns the optimized instruction string."""
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
    spec = importlib.util.spec_from_file_location(
        "agent_mod", os.path.join(agent_module_path, "__init__.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_mod"] = module
    spec.loader.exec_module(module)
    root_agent = module.agent.root_agent
    print(f"    Agent: {root_agent.name}")

    app_name = os.path.basename(agent_module_path)
    agents_dir = os.path.dirname(agent_module_path)

    if sampler_config is None:
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
            "train_eval_set": Path(evalset_path).stem,
        }

    sampler_cfg = LocalEvalSamplerConfig.model_validate(sampler_config)
    if sampler_cfg.app_name != app_name:
        sampler_cfg.app_name = app_name

    optimizer_config = GEPARootAgentPromptOptimizerConfig()
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    sampler = LocalEvalSampler(sampler_cfg, eval_sets_manager)
    optimizer = GEPARootAgentPromptOptimizer(optimizer_config)

    print("  [3/3] Running GEPA optimization...")
    optimization_result = asyncio.run(optimizer.optimize(root_agent, sampler))

    best_idx = optimization_result.gepa_result["best_idx"]
    best_agent = optimization_result.optimized_agents[best_idx]
    optimized_instruction = best_agent.optimized_agent.instruction

    print(f"  Best variant: {best_idx}")
    return optimized_instruction
