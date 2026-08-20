"""GEPA optimization — wraps the ADK GEPARootAgentPromptOptimizer with patches."""

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _fmt_elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def _patch_adk():
    """Apply ADK patches for GEPA compatibility."""
    from google.adk.evaluation import eval_case as _ec
    from google.adk.evaluation import eval_set as _es

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
                # Not every ADK model can be rebuilt; the ones that can't are
                # already resolved, so a failure here is not actionable.
                with contextlib.suppress(Exception):
                    _cls.model_rebuild(force=True)

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
        metric_totals = {}
        metric_counts = {}
        rubric_failures = {}

        for case_result in eval_results:
            for inv in getattr(case_result, "eval_metric_result_per_invocation", []):
                for mr in getattr(inv, "eval_metric_results", []):
                    if mr.score is None:
                        mr.score = 0.0
                    name = getattr(mr, "metric_name", "unknown")
                    metric_totals[name] = metric_totals.get(name, 0.0) + mr.score
                    metric_counts[name] = metric_counts.get(name, 0) + 1
                    if hasattr(mr, "rubric_scores") and not mr.rubric_scores:
                        rubric_failures[name] = rubric_failures.get(name, 0) + 1

        if metric_totals:
            breakdown = " | ".join(
                f"{n.split('/')[-1][:25]}={metric_totals[n] / metric_counts[n]:.2f}"
                for n in sorted(metric_totals)
            )
            log.info("Eval batch (%d cases): %s", len(eval_results), breakdown)

        if rubric_failures:
            for metric, count in rubric_failures.items():
                log.warning(
                    "RUBRIC MATCH FAILURE: %s had %d/%d cases with no rubric scores",
                    metric,
                    count,
                    len(eval_results),
                )

        return _orig_extract(self, eval_set_id, eval_results)

    sampler_mod.LocalEvalSampler._extract_eval_data = _patched_extract

    # Patch 5: Fuzzy rubric text matching — ADK's _normalize_text only does
    # .lower().strip(), so judge-garbled text (markdown bullets, non-ASCII)
    # causes exact match failures.  We also override
    # convert_auto_rater_response_to_score with a substring fallback.
    import re as _re
    import unicodedata as _ud

    from google.adk.evaluation import rubric_based_evaluator as _rbe

    _SMART_CHARS = {
        0x2018: "'",
        0x2019: "'",
        0x201C: '"',
        0x201D: '"',
        0x2013: "-",
        0x2014: "-",
        0x2026: "...",
    }

    def _fuzzy_normalize(text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = _ud.normalize("NFKC", text)
        text = text.translate(_SMART_CHARS)
        text = _re.sub(r'^[\s*•\-"\']+', "", text)
        text = _re.sub(r'[\s*•\-"\']+$', "", text)
        text = _re.sub(r"\s+", " ", text)
        return text.lower().strip()

    _rbe._normalize_text = _fuzzy_normalize

    _orig_convert = _rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score

    def _patched_convert(self, auto_rater_response):
        from google.adk.evaluation.rubric_based_evaluator import (
            AutoRaterScore,
            RubricScore,
            get_average_rubric_score,
            get_text_from_content,
        )

        response_text = get_text_from_content(auto_rater_response.content)
        rubric_responses = self._auto_rater_response_parser.parse(response_text)
        rubric_scores = []

        normalized_map = {}
        for r in self.get_effective_rubrics_list():
            normalized_map[_fuzzy_normalize(r.rubric_content.text_property)] = r

        for rubric_response in rubric_responses:
            norm_text = _fuzzy_normalize(rubric_response.property_text)
            rubric = normalized_map.get(norm_text)

            if not rubric and norm_text:
                candidates = [
                    r
                    for ct, r in normalized_map.items()
                    if ct and (ct in norm_text or norm_text in ct)
                ]
                if len(candidates) == 1:
                    rubric = candidates[0]
                    log.debug(
                        "Rubric substring fallback: '%s' → '%s'",
                        rubric_response.property_text[:60],
                        rubric.rubric_id,
                    )

            if rubric:
                rubric_scores.append(
                    RubricScore(
                        rubric_id=rubric.rubric_id,
                        rationale=rubric_response.rationale,
                        score=rubric_response.score,
                    )
                )
            else:
                log.warning(
                    "Rubric not matched (even with fuzzy): '%s'",
                    rubric_response.property_text[:80],
                )

        aggregated_score = get_average_rubric_score(rubric_scores)
        return AutoRaterScore(score=aggregated_score, rubric_scores=rubric_scores)

    _rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score = _patched_convert

    log.info("ADK patches applied (including fuzzy rubric matching)")


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


async def _prewarm_mcp_toolsets(agent, tag: str = "  ", max_retries: int = 3) -> int:
    """Pre-warm MCP tool sessions so GEPA doesn't timeout on first connection.

    Retries with exponential backoff to survive Cloud Run cold starts.
    Returns the number of toolsets successfully warmed.
    """
    from google.adk.tools.base_toolset import BaseToolset

    mcp_toolsets = [t for t in agent.tools if isinstance(t, BaseToolset)]
    warmed = 0
    for ts in mcp_toolsets:
        for attempt in range(max_retries):
            try:
                tools = await ts.get_tools()
                log.info("Pre-warmed %s: %d tools", type(ts).__name__, len(tools))
                warmed += 1
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    log.warning(
                        "MCP pre-warm attempt %d/%d failed, retrying in %ds: %s",
                        attempt + 1,
                        max_retries,
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                else:
                    log.error(
                        "MCP pre-warm failed after %d attempts for %s: %s",
                        max_retries,
                        type(ts).__name__,
                        exc,
                    )
    if mcp_toolsets:
        print(f"{tag}  Pre-warmed {warmed}/{len(mcp_toolsets)} MCP toolset(s)", flush=True)
        if warmed < len(mcp_toolsets):
            print(
                f"{tag}  WARNING: {len(mcp_toolsets) - warmed} toolset(s) failed — "
                f"optimization will proceed with reduced tool context",
                flush=True,
            )
    return warmed


def optimize(
    agent_module_path: str,
    evalset_path: str | None = None,
    sampler_config_path: str | None = None,
    eval_data_path: str | None = None,
    agent_name: str = "",
    eval_thresholds: dict[str, float] | None = None,
    judge_model: str = "gemini-2.5-flash",
    max_metric_calls: int | None = None,
    initial_instruction: str | None = None,
) -> str:
    """Run GEPA optimization. Returns the optimized instruction string.

    Args:
        agent_module_path: Path to agent wrapper module (must export agent.root_agent)
        evalset_path: Path to evalset JSON (ignored if sampler_config_path is set)
        sampler_config_path: Path to sampler config JSON file
        eval_data_path: Path to simplified eval YAML (for auto-generating GEPA evalset)
        agent_name: Display name for logging (e.g. "lite-gemini-3.1-flash-lite")
        eval_thresholds: Per-metric thresholds used ONLY for the fallback criteria
            built when no sampler_config_path is given. When a sampler_config.json
            exists it is authoritative and these are ignored.
        judge_model: Judge model for eval metrics
    """
    tag = f"  [{agent_name}] " if agent_name else "  "
    print(f"{tag}[1/3] Applying ADK patches...", flush=True)
    _patch_adk()

    import vertexai

    from ..core.config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
    from google.adk.optimization.gepa_root_agent_prompt_optimizer import (
        GEPARootAgentPromptOptimizer,
        GEPARootAgentPromptOptimizerConfig,
    )
    from google.adk.optimization.local_eval_sampler import (
        LocalEvalSampler,
        LocalEvalSamplerConfig,
    )

    print(f"{tag}[2/3] Loading agent and configs...", flush=True)
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

    if initial_instruction:
        root_agent.instruction = initial_instruction
        print(
            f"{tag}  Instruction override: {len(initial_instruction)} chars (from manifest)",
            flush=True,
        )
    else:
        print(
            f"{tag}  Instruction: {len(root_agent.instruction)} chars (from _opt module)",
            flush=True,
        )
    print(f"{tag}  Agent: {root_agent.name}", flush=True)

    app_name = os.path.basename(agent_module_path)
    agents_dir = os.path.dirname(agent_module_path)

    if sampler_config_path:
        # sampler_config.json is the single source of truth for GEPA criteria
        # and thresholds. It is used verbatim — experiment eval_thresholds do NOT
        # override it (they only seed the fallback criteria below when no file exists).
        import json as _json

        with open(sampler_config_path) as f:
            sampler_config = _json.load(f)
    else:
        from ..core.converter import build_gepa_criteria

        evalset_stem = Path(evalset_path).stem if evalset_path else "eval_set"
        evalset_stem = evalset_stem.removesuffix(".evalset")
        sampler_config = {
            "eval_config": {
                "criteria": build_gepa_criteria(eval_thresholds, judge_model),
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
    evalset_files = (
        [f for f in os.listdir(evalset_dir) if f.endswith(".evalset.json")]
        if os.path.isdir(evalset_dir)
        else []
    )
    if not evalset_files and eval_data_path:
        print(
            f"{tag}  No evalset files in {evalset_dir} — auto-generating from {eval_data_path}",
            flush=True,
        )
        from ..core.converter import generate_gepa_evalset, generate_sampler_config, load_eval_file

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
        print(f"{tag}  Auto-generated evalset at {evalset_dir}", flush=True)
    elif not evalset_files:
        log.warning(f"No .evalset.json files found in {evalset_dir}. GEPA may fail.")
        print(f"{tag}  WARNING: No evalset files in {evalset_dir}", flush=True)
        print(
            f"{tag}  Run: wrangler generate-evalset --from <eval.yaml> --output {evalset_dir}",
            flush=True,
        )

    run_dir = os.path.join("outputs", "gepa_runs", app_name)
    os.makedirs(run_dir, exist_ok=True)
    opt_kwargs = {"run_dir": run_dir}
    if max_metric_calls is not None:
        opt_kwargs["max_metric_calls"] = max_metric_calls
    optimizer_config = GEPARootAgentPromptOptimizerConfig(**opt_kwargs)
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    sampler = LocalEvalSampler(sampler_cfg, eval_sets_manager)
    optimizer = GEPARootAgentPromptOptimizer(optimizer_config)

    train_count = len(sampler.get_train_example_ids())
    val_count = len(sampler.get_validation_example_ids())
    max_calls = optimizer_config.max_metric_calls
    print(f"{tag}[3/3] Running GEPA optimization...", flush=True)
    print(
        f"{tag}  Train: {train_count} cases, Val: {val_count} cases, Max metric calls: {max_calls}",
        flush=True,
    )
    print(f"{tag}  Optimizer model: {optimizer_config.optimizer_model}", flush=True)
    print(f"{tag}  Run dir: {run_dir}", flush=True)

    t0 = time.time()
    try:

        async def _run_with_warmup():
            await _prewarm_mcp_toolsets(root_agent, tag)

            # Patch sampler to refresh MCP sessions before each generation.
            # Sessions die between generations due to Cloud Run idle timeouts.
            from google.adk.tools.base_toolset import BaseToolset

            _orig_sample = sampler.sample_and_score
            _gen_count = [0]

            async def _refreshed_sample(candidate, *args, **kwargs):
                _gen_count[0] += 1
                gen = _gen_count[0]
                gen_t0 = time.time()
                mcp_count = sum(1 for t in root_agent.tools if isinstance(t, BaseToolset))

                if gen > 1 and mcp_count > 0:
                    print(
                        f"{tag}  Generation {gen}: closing {mcp_count} stale MCP session(s)...",
                        flush=True,
                    )
                    for tool in root_agent.tools:
                        if isinstance(tool, BaseToolset):
                            # Closing a stale session is best-effort: the point is
                            # to re-warm below, and a dead session raises on close.
                            with contextlib.suppress(Exception):
                                await tool.close()
                    warmed = await _prewarm_mcp_toolsets(root_agent, tag, max_retries=2)
                    refresh_elapsed = time.time() - gen_t0
                    print(
                        f"{tag}  Generation {gen}: re-warmed {warmed}/{mcp_count} in {refresh_elapsed:.1f}s",
                        flush=True,
                    )
                else:
                    print(f"{tag}  Generation {gen}: evaluating candidate...", flush=True)

                result = await _orig_sample(candidate, *args, **kwargs)
                gen_elapsed = time.time() - gen_t0
                print(f"{tag}  Generation {gen}: scored in {gen_elapsed:.1f}s", flush=True)
                return result

            sampler.sample_and_score = _refreshed_sample

            return await optimizer.optimize(root_agent, sampler)

        optimization_result = asyncio.run(_run_with_warmup())
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
    n_candidates = optimization_result.gepa_result.get("num_candidates", "?")
    total_calls = optimization_result.gepa_result.get("total_metric_calls", "?")

    scores_summary = ""
    for i, agent_with_score in enumerate(optimization_result.optimized_agents):
        marker = " <-- best" if i == best_idx else ""
        scores_summary += (
            f"\n{tag}    variant {i}: score={agent_with_score.overall_score:.3f}{marker}"
        )

    print(f"{tag}  Optimization complete ({_fmt_elapsed(t0)})", flush=True)
    print(f"{tag}  Candidates: {n_candidates}, Total metric calls: {total_calls}", flush=True)
    print(f"{tag}  Variant scores:{scores_summary}", flush=True)
    print(f"{tag}  Best variant: {best_idx} ({len(optimized_instruction)} chars)", flush=True)

    stderr_path = os.path.join(run_dir, "run_log_stderr.txt")
    if os.path.exists(stderr_path):
        with open(stderr_path) as f:
            rubric_warnings = [line for line in f if "not found in the rubrics" in line]
        if rubric_warnings:
            print(
                f"{tag}  WARNING: {len(rubric_warnings)} rubric match failures during optimization",
                flush=True,
            )
            seen = set()
            for w in rubric_warnings:
                short = w.strip()[:120]
                if short not in seen:
                    print(f"{tag}    {short}", flush=True)
                    seen.add(short)

    return optimized_instruction
