"""Pipeline runner — orchestrates the full deploy → eval → optimize → redeploy → eval → report workflow."""

import json
import os
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

os.environ.setdefault("ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS", "true")
warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*")
warnings.filterwarnings("ignore", message=".*GEMINI_VIA_LITELLM.*")

from .factory import PairFactory, AgentPromptPair, Manifest
from .converter import load_eval_file
from .evaluator import run_batch_eval, EvalResult
from .optimizer import optimize
from .reporter import generate_report
from . import deploy as deployer


def _fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xm Ys' or 'Xs' for short durations."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


class WranglerPipeline:
    def __init__(self, manifest_path: str):
        self.manifest = PairFactory.load(manifest_path)
        self.manifest_dir = Path(manifest_path).parent
        self.results: dict[str, dict] = {}
        self._phase_times: list[tuple[str, float]] = []
        self._pipeline_start: float = 0.0

    @contextmanager
    def _phase(self, name: str):
        """Context manager that tracks and prints phase timing."""
        print(f"\n--- {name} ---")
        t0 = time.time()
        yield
        elapsed = time.time() - t0
        self._phase_times.append((name, elapsed))
        total = time.time() - self._pipeline_start
        print(f"\n  >> {name} complete ({_fmt_duration(elapsed)}) "
              f"[total elapsed: {_fmt_duration(total)}]")

    def _load_eval_cases(self) -> list[dict]:
        eval_path = self.manifest_dir / self.manifest.eval_data
        if not eval_path.exists():
            eval_path = Path(self.manifest.eval_data)
        return load_eval_file(str(eval_path))

    def _load_agent(self, pair: AgentPromptPair):
        """Import and instantiate the agent with the pair's model and prompt."""
        import importlib.util
        import sys

        module_ref = pair.agent_module or self.manifest.agent_module
        agent_path = self.manifest_dir / module_ref
        if not agent_path.exists():
            agent_path = Path(module_ref)

        if agent_path.is_file():
            init_file = agent_path
        elif not agent_path.suffix and (agent_path.with_suffix(".py")).is_file():
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
            f"  Expected one of:\n"
            f"    1. create_agent(model, instruction) — factory function (recommended)\n"
            f"    2. agent.root_agent — SimpleNamespace wrapping an LlmAgent\n"
            f"    3. root_agent — LlmAgent directly"
        )

    def _resolve_optimize_module(self, pair: AgentPromptPair) -> Path:
        """Resolve the GEPA-compatible optimization directory for a pair.

        Maps agent_module (e.g. agents/lite_agent.py) to the *_opt directory
        (e.g. agents/lite_opt/) which contains __init__.py, evalset, and sampler config.
        Falls back to the manifest-level agent_module if no *_opt directory exists.
        """
        agent_ref = pair.agent_module or self.manifest.agent_module
        agent_path = Path(agent_ref)
        stem = agent_path.stem.replace("_agent", "")
        opt_dir = agent_path.parent / f"{stem}_opt"

        for base in [self.manifest_dir, Path(".")]:
            candidate = base / opt_dir
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                return candidate

        fallback = self.manifest_dir / self.manifest.agent_module
        if not fallback.exists():
            fallback = Path(self.manifest.agent_module)
        return fallback

    def _save_optimized_prompt(self, pair: AgentPromptPair, prompt: str, version: str = "wrangler_v3"):
        """Save optimized prompt to the agent's prompts.py file."""
        agent_ref = pair.agent_module or self.manifest.agent_module
        stem = Path(agent_ref).stem.replace("_agent", "")
        prompts_file = self.manifest_dir / "prompts" / f"{stem}_prompts.py"
        if not prompts_file.exists():
            print(f"  [{pair.id}] Warning: prompts file not found at {prompts_file}", flush=True)
            return

        entry = {
            "prompt": prompt,
            "source": "wrangler sequential GEPA optimization",
            "eval_cases": self.results.get("_eval_metadata", {}).get("case_count", 40),
            "judge_model": "gemini-2.5-pro",
            "notes": "Sequential optimization (no parallel contention), 40-case evalset",
            "timestamp": datetime.now().isoformat(),
        }

        content = prompts_file.read_text()
        import ast
        tree = ast.parse(content)
        optimized_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "OPTIMIZED":
                        optimized_node = node

        if optimized_node is None:
            print(f"  [{pair.id}] Warning: OPTIMIZED dict not found in {prompts_file}", flush=True)
            return

        optimized_dict = ast.literal_eval(content[content.index("OPTIMIZED =") + len("OPTIMIZED ="):])
        optimized_dict[version] = entry

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
            print(f"  [{pair.id}] Warning: unexpected format in {prompts_file}", flush=True)
            return

        prompts_file.write_text(updated)
        print(f"  [{pair.id}] Saved {version} to {prompts_file}", flush=True)

    def _run_optimize_sequential(self):
        """Run GEPA optimization for all agents sequentially."""
        n_pairs = len(self.manifest.pairs)
        for i, pair in enumerate(self.manifest.pairs, 1):
            print(f"\n  [{pair.id}] ({i}/{n_pairs}) Optimizing...", flush=True)
            agent_path = self._resolve_optimize_module(pair)
            eval_path = self.manifest_dir / self.manifest.eval_data
            if not eval_path.exists():
                eval_path = Path(self.manifest.eval_data)
            sampler_cfg = agent_path / "sampler_config.json"
            t0 = time.time()
            optimized = optimize(
                str(agent_path),
                eval_data_path=str(eval_path),
                sampler_config_path=str(sampler_cfg) if sampler_cfg.exists() else None,
                agent_name=pair.id,
            )
            elapsed = time.time() - t0
            self.results[pair.id]["optimized_prompt"] = optimized
            pair.system_prompt = optimized
            self._save_optimized_prompt(pair, optimized)
            print(f"  [{pair.id}] Done ({_fmt_duration(elapsed)}) — {len(optimized)} chars")

    def _run_eval_parallel(self, eval_cases: list[dict], n_pairs: int, phase: str, max_concurrent: int = 2):
        """Run batch eval for all agents in staggered parallel batches."""
        score_key = "before" if phase == "before" else "after"
        per_case_key = f"{score_key}_per_case"

        def _eval_one(pair: AgentPromptPair) -> tuple[str, EvalResult, float]:
            engine_id = self.results[pair.id]["engine_id"]
            t0 = time.time()
            result = run_batch_eval(engine_id, eval_cases, agent_name=pair.id)
            elapsed = time.time() - t0
            return pair.id, result, elapsed

        pairs = list(self.manifest.pairs)
        for batch_start in range(0, len(pairs), max_concurrent):
            batch = pairs[batch_start:batch_start + max_concurrent]
            batch_num = batch_start // max_concurrent + 1
            total_batches = (len(pairs) + max_concurrent - 1) // max_concurrent
            print(f"\n  --- Batch {batch_num}/{total_batches} ({len(batch)} agents) ---", flush=True)

            with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
                futures = {pool.submit(_eval_one, pair): pair for pair in batch}
                for future in as_completed(futures):
                    pair_id, result, elapsed = future.result()
                    self.results[pair_id][score_key] = result.scores
                    self.results[pair_id][per_case_key] = result.per_case
                    avg = sum(result.scores.values()) / max(len(result.scores), 1)
                    print(f"  [{pair_id}] Done ({_fmt_duration(elapsed)}) — avg score: {avg:.2f}")
                    for m, s in sorted(result.scores.items()):
                        print(f"    {m:40s} {s:.2f}")

    def _preflight(self, eval_cases: list[dict]):
        """Validate eval data before running the pipeline."""
        tier_counts = Counter(c.get("tier", "") for c in eval_cases)
        cat_counts = Counter(c.get("category", "") for c in eval_cases)

        missing_tier = [i for i, c in enumerate(eval_cases) if not c.get("tier")]
        missing_cat = [i for i, c in enumerate(eval_cases) if not c.get("category")]

        print(f"  Cases:      {len(eval_cases)}")
        print(f"  Tiers:      {dict(tier_counts)}")
        print(f"  Categories: {dict(cat_counts)}")

        if missing_tier:
            raise ValueError(f"Cases missing 'tier' field at indices: {missing_tier[:5]}")
        if missing_cat:
            raise ValueError(f"Cases missing 'category' field at indices: {missing_cat[:5]}")

        print(f"  Pre-flight: PASSED")

    def run(self) -> dict:
        """Execute the full pipeline with progress tracking."""
        self._pipeline_start = time.time()

        print(f"{'=' * 60}")
        print(f"GEPA PROMPT WRANGLER")
        print(f"{'=' * 60}")
        print(f"  Experiment: {self.manifest.name}")
        print(f"  Pairs:      {len(self.manifest.pairs)}")
        print()

        eval_cases = self._load_eval_cases()
        n_pairs = len(self.manifest.pairs)

        for pair in self.manifest.pairs:
            self.results[pair.id] = {
                "model": pair.model,
                "original_prompt": pair.system_prompt,
            }

        # Store eval case metadata for downstream analysis
        self.results["_eval_metadata"] = {
            "case_count": len(eval_cases),
            "cases": [
                {"tier": c.get("tier", ""), "category": c.get("category", ""), "prompt": c.get("prompt", "")}
                for c in eval_cases
            ],
        }

        # Phase 0: Pre-flight validation
        with self._phase("Phase 0: Pre-flight Validation"):
            self._preflight(eval_cases)

        # Phase 1: Deploy
        with self._phase("Phase 1: Deploy to GEAP"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                if pair.engine_id:
                    print(f"  [{pair.id}] ({i}/{n_pairs}) Using existing engine: {pair.engine_id}")
                    self.results[pair.id]["engine_id"] = pair.engine_id
                else:
                    print(f"  [{pair.id}] ({i}/{n_pairs}) Deploying...", end="", flush=True)
                    t0 = time.time()
                    agent = self._load_agent(pair)
                    engine_id = deployer.deploy_agent(agent, display_name=pair.id)
                    self.results[pair.id]["engine_id"] = engine_id
                    print(f" {_fmt_duration(time.time() - t0)}")

        # Phase 2: Baseline eval (parallel)
        with self._phase("Phase 2: Baseline Evaluation"):
            self._run_eval_parallel(eval_cases, n_pairs, phase="before")

        # Phase 3: GEPA optimize (sequential to avoid MCP/API contention)
        with self._phase("Phase 3: GEPA Optimization"):
            self._run_optimize_sequential()

        # Phase 4: Redeploy with optimized prompt
        with self._phase("Phase 4: Redeploy with Optimized Prompt"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                print(f"  [{pair.id}] ({i}/{n_pairs}) Redeploying...", end="", flush=True)
                t0 = time.time()
                engine_id = self.results[pair.id]["engine_id"]
                agent = self._load_agent(pair)
                deployer.update_agent(agent, engine_id, display_name=pair.id)
                print(f" {_fmt_duration(time.time() - t0)}")

        # Phase 5: Post-optimization eval (parallel)
        with self._phase("Phase 5: Post-Optimization Evaluation"):
            self._run_eval_parallel(eval_cases, n_pairs, phase="after")

        # Phase 6: Generate report
        with self._phase("Phase 6: Generate Report"):
            generate_report(self.results, self.manifest.name)

        # Save raw results
        output_path = Path("outputs") / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"\nResults saved to: {output_path}")

        # Final timing summary
        total = time.time() - self._pipeline_start
        print(f"\n{'=' * 60}")
        print(f"PIPELINE COMPLETE — Total: {_fmt_duration(total)}")
        print(f"{'=' * 60}")
        for phase_name, phase_time in self._phase_times:
            print(f"  {phase_name:40s} {_fmt_duration(phase_time):>8s}")
        print(f"{'=' * 60}")

        return self.results
