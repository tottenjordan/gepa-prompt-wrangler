"""Pipeline runner — orchestrates the full deploy → eval → optimize → redeploy → eval → report workflow."""

import json
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

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

        agent_path = self.manifest_dir / self.manifest.agent_module
        if not agent_path.exists():
            agent_path = Path(self.manifest.agent_module)

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

        # Phase 2: Baseline eval
        with self._phase("Phase 2: Baseline Evaluation"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                engine_id = self.results[pair.id]["engine_id"]
                print(f"\n  [{pair.id}] ({i}/{n_pairs})")
                t0 = time.time()
                result = run_batch_eval(engine_id, eval_cases)
                self.results[pair.id]["before"] = result.scores
                self.results[pair.id]["before_per_case"] = result.per_case
                avg = sum(result.scores.values()) / max(len(result.scores), 1)
                print(f"  [{pair.id}] ({i}/{n_pairs}) Done ({_fmt_duration(time.time() - t0)}) "
                      f"— avg score: {avg:.2f}")
                for m, s in sorted(result.scores.items()):
                    print(f"    {m:40s} {s:.2f}")

        # Phase 3: GEPA optimize
        with self._phase("Phase 3: GEPA Optimization"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                print(f"\n  [{pair.id}] ({i}/{n_pairs}) Optimizing...", flush=True)
                t0 = time.time()
                agent_path = self.manifest_dir / self.manifest.agent_module
                eval_path = self.manifest_dir / self.manifest.eval_data
                optimized = optimize(str(agent_path), str(eval_path))
                self.results[pair.id]["optimized_prompt"] = optimized
                pair.system_prompt = optimized
                print(f"  [{pair.id}] ({i}/{n_pairs}) Done ({_fmt_duration(time.time() - t0)}) "
                      f"— {len(optimized)} chars")

        # Phase 4: Redeploy with optimized prompt
        with self._phase("Phase 4: Redeploy with Optimized Prompt"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                print(f"  [{pair.id}] ({i}/{n_pairs}) Redeploying...", end="", flush=True)
                t0 = time.time()
                engine_id = self.results[pair.id]["engine_id"]
                agent = self._load_agent(pair)
                deployer.update_agent(agent, engine_id, display_name=pair.id)
                print(f" {_fmt_duration(time.time() - t0)}")

        # Phase 5: Post-optimization eval
        with self._phase("Phase 5: Post-Optimization Evaluation"):
            for i, pair in enumerate(self.manifest.pairs, 1):
                engine_id = self.results[pair.id]["engine_id"]
                print(f"\n  [{pair.id}] ({i}/{n_pairs})")
                t0 = time.time()
                result = run_batch_eval(engine_id, eval_cases)
                self.results[pair.id]["after"] = result.scores
                self.results[pair.id]["after_per_case"] = result.per_case
                avg = sum(result.scores.values()) / max(len(result.scores), 1)
                print(f"  [{pair.id}] ({i}/{n_pairs}) Done ({_fmt_duration(time.time() - t0)}) "
                      f"— avg score: {avg:.2f}")
                for m, s in sorted(result.scores.items()):
                    print(f"    {m:40s} {s:.2f}")

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
