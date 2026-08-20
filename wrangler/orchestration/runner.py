"""Pipeline runner — orchestrates the full deploy → eval → optimize → redeploy → eval → report workflow."""

import json
import os
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS", "true")
warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*")
warnings.filterwarnings("ignore", message=".*GEMINI_VIA_LITELLM.*")

# E402 below is deliberate: the filterwarnings() calls above must execute
# before the ADK/Vertex imports, or their import-time warnings escape.

from ..core import deploy as deployer  # noqa: E402
from ..core.converter import load_eval_file  # noqa: E402
from ..core.factory import AgentPromptPair, PairFactory  # noqa: E402
from ..core.models import DEFAULT_MANIFEST_JUDGE_MODEL  # noqa: E402
from ..eval.evaluator import EvalResult, run_batch_eval_averaged  # noqa: E402
from ..optimize.optimizer import optimize  # noqa: E402
from ..reporting.reporter import generate_report  # noqa: E402


def _fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xm Ys' or 'Xs' for short durations."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


class WranglerPipeline:
    def __init__(
        self,
        manifest_path: str,
        max_concurrent: int = 1,
        version: str | None = None,
        num_runs: int = 1,
    ):
        self.manifest = PairFactory.load(manifest_path)
        self.manifest_dir = Path(manifest_path).parent
        self.results: dict[str, dict] = {}
        self._phase_times: list[tuple[str, float]] = []
        self._pipeline_start: float = 0.0
        self.max_concurrent = max_concurrent
        self.version = version
        self.num_runs = num_runs

    @contextmanager
    def _phase(self, name: str):
        """Context manager that tracks and prints phase timing."""
        print(f"\n--- {name} ---")
        t0 = time.time()
        yield
        elapsed = time.time() - t0
        self._phase_times.append((name, elapsed))
        total = time.time() - self._pipeline_start
        print(
            f"\n  >> {name} complete ({_fmt_duration(elapsed)}) "
            f"[total elapsed: {_fmt_duration(total)}]"
        )

    def load_results(self, results_path: str):
        """Load previous results JSON to resume from a later phase."""
        with open(results_path) as f:
            saved = json.load(f)
        for key, value in saved.items():
            if key.startswith("_"):
                self.results[key] = value
                continue
            self.results[key] = value
            for pair in self.manifest.pairs:
                if pair.id == key:
                    if "engine_id" in value:
                        pair.engine_id = value["engine_id"]
                    if "optimized_prompt" in value:
                        pair.system_prompt = value["optimized_prompt"]
                    break

    def _next_version(self) -> str:
        """Auto-detect the next wrangler version from existing prompt files."""
        if self.version:
            return self.version
        max_ver = 0
        prompts_dir = self.manifest_dir / "prompts"
        if prompts_dir.exists():
            import re

            for py_file in prompts_dir.glob("*_prompts.py"):
                for match in re.finditer(r"wrangler_v(\d+)", py_file.read_text()):
                    max_ver = max(max_ver, int(match.group(1)))
        return f"wrangler_v{max_ver + 1}"

    def _load_eval_cases(self) -> list[dict]:
        eval_path = self.manifest_dir / self.manifest.eval_data
        if not eval_path.exists():
            eval_path = Path(self.manifest.eval_data)
        return load_eval_file(str(eval_path))

    def _load_agent(self, pair: AgentPromptPair):
        """Import and instantiate the agent with the pair's model and prompt."""
        import importlib.util

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
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load a Python module from {init_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from ..core.config import resolve_model

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

        for base in [self.manifest_dir, Path()]:
            candidate = base / opt_dir
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                return candidate

        fallback = self.manifest_dir / self.manifest.agent_module
        if not fallback.exists():
            fallback = Path(self.manifest.agent_module)
        return fallback

    def _save_optimized_prompt(
        self, pair: AgentPromptPair, prompt: str, version: str | None = None
    ):
        """Save optimized prompt to the agent's prompts.py file."""
        agent_ref = pair.agent_module or self.manifest.agent_module
        stem = Path(agent_ref).stem.replace("_agent", "")
        prompts_file = self.manifest_dir / "prompts" / f"{stem}_prompts.py"
        if not prompts_file.exists():
            print(f"  [{pair.id}] Warning: prompts file not found at {prompts_file}", flush=True)
            return

        version = version or self._next_version()
        judge = self.manifest.eval_config.get("judge_model", DEFAULT_MANIFEST_JUDGE_MODEL)
        case_count = self.results.get("_eval_metadata", {}).get("case_count", 0)
        entry = {
            "prompt": prompt,
            "source": "wrangler GEPA optimization",
            "eval_cases": case_count,
            "judge_model": judge,
            "timestamp": datetime.now(tz=UTC).isoformat(),
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

        optimized_dict = ast.literal_eval(
            content[content.index("OPTIMIZED =") + len("OPTIMIZED =") :]
        )
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

    def _run_eval(self, eval_cases: list[dict], n_pairs: int, phase: str):
        """Run batch eval for all agents, sequentially or in batches."""
        score_key = "before" if phase == "before" else "after"
        per_case_key = f"{score_key}_per_case"
        std_key = f"{score_key}_std"

        def _eval_one(pair: AgentPromptPair) -> tuple[str, EvalResult, float]:
            engine_id = self.results[pair.id]["engine_id"]
            t0 = time.time()
            result = run_batch_eval_averaged(
                engine_id, eval_cases, num_runs=self.num_runs, agent_name=pair.id
            )
            elapsed = time.time() - t0
            return pair.id, result, elapsed

        def _record(pair_id, result, elapsed):
            self.results[pair_id][score_key] = result.scores
            self.results[pair_id][per_case_key] = result.per_case
            if result.scores_std:
                self.results[pair_id][std_key] = result.scores_std
            avg = sum(result.scores.values()) / max(len(result.scores), 1)
            suffix = f" (avg of {result.num_runs} runs)" if result.num_runs > 1 else ""
            print(f"  [{pair_id}] Done ({_fmt_duration(elapsed)}) — avg score: {avg:.2f}{suffix}")
            for m, s in sorted(result.scores.items()):
                std = result.scores_std.get(m)
                std_str = f" +/- {std:.3f}" if std else ""
                print(f"    {m:40s} {s:.2f}{std_str}")

        pairs = list(self.manifest.pairs)
        mc = self.max_concurrent

        if mc <= 1:
            for i, pair in enumerate(pairs, 1):
                print(f"\n  [{pair.id}] ({i}/{n_pairs}) Evaluating...", flush=True)
                pair_id, result, elapsed = _eval_one(pair)
                _record(pair_id, result, elapsed)
        else:
            for batch_start in range(0, len(pairs), mc):
                batch = pairs[batch_start : batch_start + mc]
                batch_num = batch_start // mc + 1
                total_batches = (len(pairs) + mc - 1) // mc
                print(
                    f"\n  --- Batch {batch_num}/{total_batches} ({len(batch)} agents) ---",
                    flush=True,
                )

                with ThreadPoolExecutor(max_workers=mc) as pool:
                    futures = {pool.submit(_eval_one, pair): pair for pair in batch}
                    for future in as_completed(futures):
                        pair_id, result, elapsed = future.result()
                        _record(pair_id, result, elapsed)

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

        print("  Pre-flight: PASSED")

    def run(self, from_phase: int = 0) -> dict:
        """Execute the full pipeline with progress tracking.

        Args:
            from_phase: Skip phases before this number (use with load_results()).
        """
        self._pipeline_start = time.time()

        print(f"{'=' * 60}")
        print("GEPA PROMPT WRANGLER")
        print(f"{'=' * 60}")
        print(f"  Experiment: {self.manifest.name}")
        print(f"  Pairs:      {len(self.manifest.pairs)}")
        if from_phase > 0:
            print(f"  Resuming:   from phase {from_phase}")
        print()

        eval_cases = self._load_eval_cases()
        n_pairs = len(self.manifest.pairs)

        for pair in self.manifest.pairs:
            self.results.setdefault(
                pair.id,
                {
                    "model": pair.model,
                    "original_prompt": pair.system_prompt,
                },
            )

        self.results["_eval_metadata"] = {
            "version": self._next_version(),
            "num_runs": self.num_runs,
            "case_count": len(eval_cases),
            "cases": [
                {
                    "tier": c.get("tier", ""),
                    "category": c.get("category", ""),
                    "prompt": c.get("prompt", ""),
                }
                for c in eval_cases
            ],
        }

        if from_phase <= 0:
            with self._phase("Phase 0: Pre-flight Validation"):
                self._preflight(eval_cases)

        if from_phase <= 1:
            with self._phase("Phase 1: Deploy to GEAP"):
                for i, pair in enumerate(self.manifest.pairs, 1):
                    if pair.engine_id:
                        print(
                            f"  [{pair.id}] ({i}/{n_pairs}) Using existing engine: {pair.engine_id}"
                        )
                        self.results[pair.id]["engine_id"] = pair.engine_id
                    else:
                        print(f"  [{pair.id}] ({i}/{n_pairs}) Deploying...", end="", flush=True)
                        t0 = time.time()
                        agent = self._load_agent(pair)
                        engine_id = deployer.deploy_agent(agent, display_name=pair.id)
                        self.results[pair.id]["engine_id"] = engine_id
                        print(f" {_fmt_duration(time.time() - t0)}")

        if from_phase <= 2:
            with self._phase("Phase 2: Baseline Evaluation"):
                self._run_eval(eval_cases, n_pairs, phase="before")

        if from_phase <= 3:
            with self._phase("Phase 3: GEPA Optimization"):
                self._run_optimize_sequential()

        if from_phase <= 4:
            with self._phase("Phase 4: Redeploy with Optimized Prompt"):
                for i, pair in enumerate(self.manifest.pairs, 1):
                    print(f"  [{pair.id}] ({i}/{n_pairs}) Redeploying...", end="", flush=True)
                    t0 = time.time()
                    engine_id = self.results[pair.id]["engine_id"]
                    agent = self._load_agent(pair)
                    deployer.update_agent(agent, engine_id, display_name=pair.id)
                    print(f" {_fmt_duration(time.time() - t0)}")

        if from_phase <= 5:
            with self._phase("Phase 5: Post-Optimization Evaluation"):
                self._run_eval(eval_cases, n_pairs, phase="after")

        with self._phase("Phase 6: Generate Report"):
            generate_report(self.results, self.manifest.name)

        # Save raw results
        output_path = (
            Path("outputs") / f"results_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.json"
        )
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
