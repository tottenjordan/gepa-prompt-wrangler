"""Stage functions — modular pipeline steps that operate on Experiment directories."""

from __future__ import annotations

import importlib.util
import time
from datetime import datetime
from pathlib import Path

from .experiment import Experiment
from .factory import AgentPromptPair, Manifest
from .converter import load_eval_file
from .evaluator import run_batch_eval_averaged, EvalResult
from .optimizer import optimize
from .reporter import generate_report as _generate_report
from . import deploy as deployer


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _validate_sampler_config(
    sampler_cfg_path: Path,
    eval_thresholds: dict[str, float] | None,
    judge_model: str,
    pair_id: str,
) -> None:
    """Warn about sampler config misalignment before optimization."""
    import json
    try:
        with open(sampler_cfg_path) as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    criteria = cfg.get("eval_config", {}).get("criteria", {})
    if not criteria:
        return

    warnings = []
    for key, val in criteria.items():
        if isinstance(val, dict) and "judge_model_options" in val:
            cfg_judge = val["judge_model_options"].get("judge_model", "")
            if cfg_judge and cfg_judge != judge_model:
                warnings.append(f"judge model mismatch: {key} uses '{cfg_judge}', experiment uses '{judge_model}'")

    if eval_thresholds:
        from .optimizer import METRIC_NAME_MAP
        for metric, threshold in eval_thresholds.items():
            actual_key = metric
            if metric not in criteria and metric in METRIC_NAME_MAP:
                rb_key = METRIC_NAME_MAP[metric]
                if rb_key in criteria:
                    actual_key = rb_key
            if actual_key in criteria:
                val = criteria[actual_key]
                if isinstance(val, dict):
                    cfg_thresh = val.get("threshold")
                    if cfg_thresh is not None and cfg_thresh != threshold:
                        warnings.append(f"{actual_key} threshold {cfg_thresh} != experiment {threshold} (will be overridden)")

    for w in warnings:
        print(f"  [{pair_id}] Warning: sampler_config — {w}")


def _post_eval_sanity_check(
    stage_name: str,
    stage_data: dict,
    pairs: list,
) -> None:
    """Warn about common eval data issues after all pairs complete."""
    all_per_case_empty = all(
        not stage_data.get(p.id, {}).get("per_case")
        for p in pairs
        if p.id in stage_data
    )
    if all_per_case_empty and stage_data:
        print(f"\n  WARNING: All per_case arrays are empty in {stage_name}.")
        print(f"  Tier/category analysis will be unavailable. Check _extract_per_case_scores() field mapping.")

    for p in pairs:
        scores = stage_data.get(p.id, {}).get("scores", {})
        zero_metrics = [m for m, v in scores.items() if v == 0.0]
        if zero_metrics:
            print(f"  WARNING: [{p.id}] has zero scores for: {', '.join(zero_metrics)}")


def _load_agent(manifest: Manifest, pair: AgentPromptPair, manifest_dir: Path | None = None):
    """Import and instantiate the agent with the pair's model and prompt."""
    module_ref = pair.agent_module or manifest.agent_module
    search_bases = [Path(".")] if manifest_dir is None else [manifest_dir, Path(".")]

    agent_path = None
    for base in search_bases:
        candidate = base / module_ref
        if candidate.exists():
            agent_path = candidate
            break
    if agent_path is None:
        agent_path = Path(module_ref)

    if agent_path.is_file():
        init_file = agent_path
    elif not agent_path.suffix and agent_path.with_suffix(".py").is_file():
        init_file = agent_path.with_suffix(".py")
    else:
        init_file = agent_path / "__init__.py"
        if not init_file.exists():
            for py_file in agent_path.glob("*.py"):
                init_file = py_file
                break

    spec = importlib.util.spec_from_file_location(f"_agent_{pair.id}", str(init_file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from .config import resolve_model

    if hasattr(module, "create_agent"):
        return module.create_agent(pair.model, pair.system_prompt)

    agent = None
    if hasattr(module, "agent") and hasattr(module.agent, "root_agent"):
        agent = module.agent.root_agent
    elif hasattr(module, "root_agent"):
        agent = module.root_agent

    if agent is not None:
        agent.model = resolve_model(pair.model)
        agent.instruction = pair.system_prompt
        return agent

    exports = [k for k in dir(module) if not k.startswith("_")]
    raise ValueError(
        f"Could not load agent from {agent_path}.\n"
        f"  Module exports: {exports}\n"
        f"  Expected: create_agent(model, instruction), agent.root_agent, or root_agent"
    )


def _resolve_optimize_module(manifest: Manifest, pair: AgentPromptPair, manifest_dir: Path | None = None) -> Path:
    """Resolve the GEPA-compatible optimization directory for a pair."""
    agent_ref = pair.agent_module or manifest.agent_module
    agent_path = Path(agent_ref)
    stem = agent_path.stem.replace("_agent", "")
    opt_dir = agent_path.parent / f"{stem}_opt"

    search_bases = [Path(".")] if manifest_dir is None else [manifest_dir, Path(".")]
    for base in search_bases:
        candidate = base / opt_dir
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate

    fallback_ref = manifest.agent_module
    for base in search_bases:
        candidate = base / fallback_ref
        if candidate.exists():
            return candidate
    return Path(fallback_ref)


def _resolve_eval_path(manifest: Manifest, manifest_dir: Path | None = None) -> Path:
    """Resolve eval data file, checking manifest_dir first."""
    search_bases = [Path(".")] if manifest_dir is None else [manifest_dir, Path(".")]
    for base in search_bases:
        candidate = base / manifest.eval_data
        if candidate.exists():
            return candidate
    return Path(manifest.eval_data)


def _filter_pairs(manifest: Manifest, pair_id: str | None) -> list[AgentPromptPair]:
    if pair_id:
        return [manifest.get_pair(pair_id)]
    return list(manifest.pairs)


def _manifest_dir(exp: Experiment) -> Path:
    """Resolve the manifest directory from config.yaml paths.

    Agent module and eval data paths in config.yaml are relative to the
    original manifest location. We walk up from the experiment dir to find
    the project root that contains these paths.
    """
    agent_module = exp.config.get("agent_module", "")
    for base in [Path("."), exp.dir, exp.dir.parent, exp.dir.parent.parent]:
        if (base / agent_module).exists():
            return base
    return Path(".")


# ── Stage functions ────────────────────────────────────────────


def stage_deploy(exp: Experiment, pair_id: str | None = None) -> None:
    ok, msg = exp.check_gate("deploy", pair_id)
    if not ok:
        print(f"  Warning: {msg}")

    manifest = exp.manifest
    pairs = _filter_pairs(manifest, pair_id)
    mdir = _manifest_dir(exp)
    deploy_data = exp.read_stage("deploy")

    for i, pair in enumerate(pairs, 1):
        tag = f"[{pair.id}] ({i}/{len(pairs)})"
        if pair.engine_id:
            print(f"  {tag} Using existing engine: {pair.engine_id}")
            exp.merge_pair("deploy", pair.id, {
                "engine_id": pair.engine_id,
                "model": pair.model,
                "original_prompt": pair.system_prompt,
                "source": "config",
            })
        elif pair.id in deploy_data and deploy_data[pair.id].get("engine_id"):
            eid = deploy_data[pair.id]["engine_id"]
            print(f"  {tag} Already deployed: {eid}")
        else:
            print(f"  {tag} Deploying...", end="", flush=True)
            t0 = time.time()
            agent = _load_agent(manifest, pair, mdir)
            version_tag = exp.version.replace("_", "-") if exp.version else ""
            display = f"{pair.id}_{version_tag}" if version_tag else pair.id
            engine_id = deployer.deploy_agent(agent, display_name=display)
            print(f" {_fmt_duration(time.time() - t0)}")
            exp.merge_pair("deploy", pair.id, {
                "engine_id": engine_id,
                "model": pair.model,
                "original_prompt": pair.system_prompt,
                "source": "deployed",
            })


def stage_eval(
    exp: Experiment,
    phase: str,
    pair_id: str | None = None,
    num_runs: int | None = None,
    retry_failed: bool = True,
) -> None:
    stage_name = f"eval_{phase}"
    ok, msg = exp.check_gate(stage_name, pair_id)
    if not ok:
        print(f"  Gate check failed: {msg}")
        return

    manifest = exp.manifest
    pairs = _filter_pairs(manifest, pair_id)
    mdir = _manifest_dir(exp)
    num_runs = num_runs or exp.config.get("defaults", {}).get("num_runs", 1)

    deploy_data = exp.read_stage("deploy")
    eval_path = _resolve_eval_path(manifest, mdir)
    eval_cases = load_eval_file(str(eval_path))
    print(f"  Eval cases: {len(eval_cases)}, num_runs: {num_runs}")

    for i, pair in enumerate(pairs, 1):
        engine_id = deploy_data.get(pair.id, {}).get("engine_id") or pair.engine_id
        if not engine_id:
            print(f"  [{pair.id}] No engine_id found — skipping (run deploy first)")
            continue

        model = deploy_data.get(pair.id, {}).get("model", "")
        print(f"\n  [{pair.id}] ({i}/{len(pairs)}) Evaluating ({phase})...", flush=True)
        t0 = time.time()
        result = run_batch_eval_averaged(
            engine_id, eval_cases, num_runs=num_runs, agent_name=pair.id,
            model=model, retry_failed=retry_failed,
        )
        elapsed = time.time() - t0

        avg = sum(result.scores.values()) / max(len(result.scores), 1)
        suffix = f" (avg of {result.num_runs} runs)" if result.num_runs > 1 else ""
        print(f"  [{pair.id}] Done ({_fmt_duration(elapsed)}) — avg score: {avg:.2f}{suffix}")
        for m, s in sorted(result.scores.items()):
            std = result.scores_std.get(m)
            std_str = f" +/- {std:.3f}" if std else ""
            print(f"    {m:40s} {s:.2f}{std_str}")

        exp.merge_pair(stage_name, pair.id, {
            "scores": result.scores,
            "per_case": result.per_case,
            "scores_std": result.scores_std,
            "num_runs": result.num_runs,
            "elapsed": elapsed,
            "token_usage": result.token_usage,
        })

    # Post-eval sanity checks
    stage_data = exp.read_stage(stage_name)
    _post_eval_sanity_check(stage_name, stage_data, pairs)


def stage_optimize(exp: Experiment, pair_id: str | None = None) -> None:
    ok, msg = exp.check_gate("optimize", pair_id)
    if not ok:
        print(f"  Gate check failed: {msg}")
        return

    manifest = exp.manifest
    pairs = _filter_pairs(manifest, pair_id)
    mdir = _manifest_dir(exp)

    eval_path = _resolve_eval_path(manifest, mdir)

    eval_thresholds = exp.eval_thresholds
    judge = exp.config.get("eval_config", {}).get("judge_model", "gemini-3.5-flash")

    for i, pair in enumerate(pairs, 1):
        print(f"\n  [{pair.id}] ({i}/{len(pairs)}) Optimizing...", flush=True)
        agent_path = _resolve_optimize_module(manifest, pair, mdir)
        sampler_cfg = agent_path / "sampler_config.json"
        if sampler_cfg.exists():
            _validate_sampler_config(sampler_cfg, eval_thresholds, judge, pair.id)
        t0 = time.time()
        optimized = optimize(
            str(agent_path),
            eval_data_path=str(eval_path),
            sampler_config_path=str(sampler_cfg) if sampler_cfg.exists() else None,
            agent_name=pair.id,
            eval_thresholds=eval_thresholds,
            judge_model=judge,
        )
        elapsed = time.time() - t0
        print(f"  [{pair.id}] Done ({_fmt_duration(elapsed)}) — {len(optimized)} chars")

        exp.merge_pair("optimize", pair.id, {
            "optimized_prompt": optimized,
            "elapsed": elapsed,
            "chars": len(optimized),
        })

        _save_optimized_prompt(exp, pair, optimized)


def _save_optimized_prompt(exp: Experiment, pair: AgentPromptPair, prompt: str) -> None:
    """Save optimized prompt to the agent's prompts.py file."""
    mdir = _manifest_dir(exp)
    agent_ref = pair.agent_module or exp.manifest.agent_module
    stem = Path(agent_ref).stem.replace("_agent", "")
    prompts_file = mdir / "prompts" / f"{stem}_prompts.py"
    if not prompts_file.exists():
        print(f"  [{pair.id}] Warning: prompts file not found at {prompts_file}")
        return

    import ast

    version = exp.version
    judge = exp.manifest.eval_config.get("judge_model", "gemini-3.5-flash")

    eval_before = exp.read_stage("eval_before")
    case_count = len(eval_before.get(pair.id, {}).get("per_case", []))

    entry = {
        "prompt": prompt,
        "source": "wrangler GEPA optimization",
        "eval_cases": case_count,
        "judge_model": judge,
        "timestamp": datetime.now().isoformat(),
    }

    content = prompts_file.read_text()
    tree = ast.parse(content)
    optimized_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "OPTIMIZED":
                    optimized_node = node

    if optimized_node is None:
        print(f"  [{pair.id}] Warning: OPTIMIZED dict not found in {prompts_file}")
        return

    lines = [f'    "{version}": {{']
    for k, v in entry.items():
        if k == "prompt":
            lines.append(f'        "prompt": """{v}""",')
        elif isinstance(v, int):
            lines.append(f'        "{k}": {v},')
        else:
            lines.append(f'        "{k}": "{v}",')
    lines.append("    },")
    new_entry = "\n".join(lines)

    closing = content.rstrip()
    if closing.endswith("}"):
        insert_pos = closing.rfind("}")
        updated = closing[:insert_pos] + new_entry + "\n}\n"
    else:
        print(f"  [{pair.id}] Warning: unexpected format in {prompts_file}")
        return

    prompts_file.write_text(updated)
    print(f"  [{pair.id}] Saved {version} to {prompts_file}")


def stage_redeploy(exp: Experiment, pair_id: str | None = None) -> None:
    ok, msg = exp.check_gate("redeploy", pair_id)
    if not ok:
        print(f"  Gate check failed: {msg}")
        return

    manifest = exp.manifest
    pairs = _filter_pairs(manifest, pair_id)
    mdir = _manifest_dir(exp)

    deploy_data = exp.read_stage("deploy")
    optimize_data = exp.read_stage("optimize")

    for i, pair in enumerate(pairs, 1):
        engine_id = deploy_data.get(pair.id, {}).get("engine_id") or pair.engine_id
        optimized_prompt = optimize_data.get(pair.id, {}).get("optimized_prompt")

        if not engine_id:
            print(f"  [{pair.id}] No engine_id — skipping")
            continue
        if not optimized_prompt:
            print(f"  [{pair.id}] No optimized_prompt — skipping")
            continue

        pair.system_prompt = optimized_prompt
        print(f"  [{pair.id}] ({i}/{len(pairs)}) Redeploying...", end="", flush=True)
        t0 = time.time()
        agent = _load_agent(manifest, pair, mdir)
        version_tag = exp.version.replace("_", "-") if exp.version else ""
        display = f"{pair.id}_{version_tag}" if version_tag else pair.id
        deployer.update_agent(agent, engine_id, display_name=display)
        elapsed = time.time() - t0
        print(f" {_fmt_duration(elapsed)}")

        exp.merge_pair("redeploy", pair.id, {
            "engine_id": engine_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed": elapsed,
        })


def stage_report(exp: Experiment, use_paperbanana: bool = True) -> None:
    ok, msg = exp.check_gate("report")
    if not ok:
        print(f"  Warning: {msg}")

    eval_before = exp.read_stage("eval_before")
    eval_after = exp.read_stage("eval_after")
    optimize_data = exp.read_stage("optimize")
    deploy_data = exp.read_stage("deploy")

    pair_descriptions = {p["id"]: p.get("description", "") for p in exp.config.get("pairs", [])}

    results = {}
    for pair_id in exp.pair_ids:
        results[pair_id] = {
            "model": deploy_data.get(pair_id, {}).get("model", ""),
            "description": pair_descriptions.get(pair_id, ""),
            "original_prompt": deploy_data.get(pair_id, {}).get("original_prompt", ""),
            "before": eval_before.get(pair_id, {}).get("scores", {}),
            "before_per_case": eval_before.get(pair_id, {}).get("per_case", []),
            "before_std": eval_before.get(pair_id, {}).get("scores_std", {}),
            "after": eval_after.get(pair_id, {}).get("scores", {}),
            "after_per_case": eval_after.get(pair_id, {}).get("per_case", []),
            "after_std": eval_after.get(pair_id, {}).get("scores_std", {}),
            "optimized_prompt": optimize_data.get(pair_id, {}).get("optimized_prompt", ""),
        }

    import matplotlib
    matplotlib.use("Agg")

    from .reporter import REPORTS_DIR, CHARTS_DIR
    original_reports = REPORTS_DIR
    original_charts = CHARTS_DIR

    try:
        from . import reporter
        reporter.REPORTS_DIR = exp.dir / "reports"
        reporter.CHARTS_DIR = exp.dir / "images"
        reporter.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        reporter.CHARTS_DIR.mkdir(parents=True, exist_ok=True)

        _generate_report(results, exp.name, use_paperbanana=use_paperbanana)
    finally:
        reporter.REPORTS_DIR = original_reports
        reporter.CHARTS_DIR = original_charts

    exp.update_tracking("report", "_all", "complete")
    print(f"  Report saved to {exp.dir / 'reports'}")
