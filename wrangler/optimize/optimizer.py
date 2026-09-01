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

from ..core.models import DEFAULT_JUDGE_MODEL

log = logging.getLogger(__name__)


def _fmt_elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


class _ToolsetFailureCounter(logging.Handler):
    """Counts the one ADK warning that means "this case ran with no tools".

    `llm_agent._convert_tool_union_to_tools` catches *every* exception from
    `toolset.get_tools()`, logs this warning, and returns `[]`. The agent then
    answers without its tools and GEPA scores the result as a bad prompt — a
    network blip becomes evidence about the instruction. Nothing raises and the
    run does not slow down, so watching the log is the only way to know.

    Counted rather than escalated: one lost toolset in a 100-call budget is
    noise worth reporting, not a reason to throw away the run. The judgement of
    how much is too much belongs to whoever reads the summary.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record):
        if record.getMessage().startswith("Failed to get tools from toolset"):
            self.count += 1


def _patch_adk():
    """Apply ADK patches for GEPA compatibility.

    Verified against google-adk 2.7.1 on 2026-08-20.

    Patch 1/2 — eval_case/eval_set extra="forbid" (issue #5906). Issue is
        CLOSED but extra="forbid" is still present on 8 classes at 2.7.1.
        Still required.
    Patch 3 — LocalEvalService null guard (issue #6071). Issue CLOSED
        2026-08-06 but the fix is NOT in the 2.7.1 release. Still required;
        re-check at 2.8.x.
    Patch 4 — LocalEvalSampler score coercion + logging. Local
        instrumentation, not an upstream workaround.
    Patch 5 — REMOVED. Upstream fixed #6072 in 2.7.1 and went further
        (rubric_id matching). Keeping it was a regression.
    Patch 6 — SafetyEvaluatorV1 metric version pin. ADK asks for the
        *unversioned* safety metric; the SDK resolves that client-side to
        safety_v3, which us-central1 does not serve. Still required.

    Re-run the probe in docs/notes/adk-patch-status.md on every ADK bump.
    """
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

    # Patch 6 — pin GEPA's safety metric to v1.
    #
    # SafetyEvaluatorV1.evaluate_invocations() asks the Vertex eval SDK for the
    # *unversioned* PrebuiltMetric.SAFETY. The SDK resolves that client-side
    # through METRIC_LATEST_SPEC_NAME, which maps "safety" -> "safety_v3" — a
    # version us-central1 does not serve. Every case then comes back
    # `400 INVALID_ARGUMENT: Unsupported predefined metric: safety_v3`, the
    # score lands as None, and patch 4 coerces it to 0.0. GEPA does not fail;
    # it optimizes against a criterion that is pinned at zero.
    #
    # Same client-ahead-of-server mismatch as silent failure #3 in
    # docs/notes/silent-failures.md. Batch eval was pinned to v1 when that was
    # found; GEPA's criteria were not, because the version is chosen inside ADK
    # and never appears in sampler_config.json (which correctly says
    # "safety_v1"). `PrebuiltMetric.SAFETY_V1` falls through the loader's
    # __getattr__ to a bare-name lookup and resolves to "safety_v1", so no
    # private SDK import is needed.
    from google.adk.dependencies.vertexai import vertexai as _vertexai
    from google.adk.evaluation import safety_evaluator as safety_mod
    from google.adk.evaluation import vertex_ai_eval_facade as facade_mod

    _pinned_safety = _vertexai.types.PrebuiltMetric.SAFETY_V1
    try:
        _resolved = _pinned_safety._get_api_metric_spec_name()
    except AttributeError:  # the SDK reorganised its metric loader
        _resolved = None

    if _resolved == "safety_v1":

        def _patched_safety(
            self, actual_invocations, expected_invocations=None, conversation_scenario=None
        ):
            # Resolved off the module, not captured at patch time, so the
            # facade stays substitutable (and follows ADK if it swaps the
            # class out).
            return facade_mod._SingleTurnVertexAiEvalFacade(
                threshold=self._threshold,
                metric_name=_pinned_safety,
            ).evaluate_invocations(actual_invocations, expected_invocations, conversation_scenario)

        safety_mod.SafetyEvaluatorV1.evaluate_invocations = _patched_safety
    else:
        # Pinning to a name the SDK cannot resolve would be worse than leaving
        # ADK alone, so bail out loudly instead.
        log.warning(
            "Patch 6 skipped: PrebuiltMetric.SAFETY_V1 resolves to %r, not 'safety_v1'. "
            "safety_v1 criteria may fail with 'Unsupported predefined metric'. "
            "Re-run the probe in docs/notes/adk-patch-status.md.",
            _resolved,
        )

    log.info("ADK patches applied (1-4, 6; patch 5 removed — upstream #6072 fixed in ADK 2.7.1)")


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
                    log.exception(
                        "MCP pre-warm failed after %d attempts for %s",
                        max_retries,
                        type(ts).__name__,
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


def _apply_model_override(root_agent, model: str, tag: str = "") -> None:
    """Point the loaded agent at ``model``, if one was named.

    GEPA optimizes whatever agent the ``_opt`` module builds, and that module
    reads its model from ``config.py`` -- so without this a manifest pair
    declaring ``model: claude-sonnet-5`` would deploy sonnet-5 and optimize
    sonnet-4-6, then label the result sonnet-5. Nothing failed when that
    happened; the number was just about a different model than its label.

    Routed through ``resolve_model`` for the same reason every other call site
    is: a bare Claude id is not servable, it needs the global resource path.
    An unregistered id raises rather than falling back to the module's model,
    because falling back is exactly the silent substitution being fixed.
    """
    if not model:
        return
    from ..core.config import resolve_model
    from ..core.models import get_spec

    # Validate before resolving. `resolve_model` does not raise on an unknown
    # id -- it falls through to the Gemini branch and hands back
    # Gemini(model="definitely-not-a-model"), so a typo would quietly optimize
    # a nonexistent Gemini model instead of the Claude one intended.
    get_spec(model)
    root_agent.model = resolve_model(model)
    print(f"{tag}  Model override: {model} (from manifest)", flush=True)


def optimize(
    agent_module_path: str,
    evalset_path: str | None = None,
    sampler_config_path: str | None = None,
    eval_data_path: str | None = None,
    agent_name: str = "",
    eval_thresholds: dict[str, float] | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    max_metric_calls: int | None = None,
    initial_instruction: str | None = None,
    model: str = "",
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
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load a Python module from {init_file}")
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
    # Before anything reads the agent: the model must match the manifest, or the
    # whole run is attributed to the wrong one.
    _apply_model_override(root_agent, model, tag)
    print(f"{tag}  Agent: {root_agent.name} | model: {root_agent.model}", flush=True)

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
    optimizer_config = (
        GEPARootAgentPromptOptimizerConfig(run_dir=run_dir, max_metric_calls=max_metric_calls)
        if max_metric_calls is not None
        else GEPARootAgentPromptOptimizerConfig(run_dir=run_dir)
    )
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
    # ADK downgrades "the agent lost its toolset" to a warning and carries on,
    # so hook the logger it warns through for the duration of the run.
    toolset_failures = _ToolsetFailureCounter()
    adk_agent_log = logging.getLogger("google_adk.google.adk.agents.llm_agent")
    adk_agent_log.addHandler(toolset_failures)
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

            # ADK patch: wraps the bound method with per-generation MCP refresh.
            sampler.sample_and_score = _refreshed_sample  # ty: ignore[invalid-assignment]

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
    finally:
        adk_agent_log.removeHandler(toolset_failures)

    if toolset_failures.count:
        print(
            f"{tag}  WARNING: {toolset_failures.count} agent invocation(s) ran with a "
            f"missing toolset and were scored anyway — those cases judged a toolless "
            f"agent, not the prompt",
            flush=True,
        )

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
