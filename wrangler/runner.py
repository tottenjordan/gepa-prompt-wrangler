"""Pipeline runner — orchestrates the full deploy → eval → optimize → redeploy → eval → report workflow."""

import json
from datetime import datetime
from pathlib import Path

from .factory import PairFactory, AgentPromptPair, Manifest
from .converter import load_eval_file
from .evaluator import run_batch_eval
from .optimizer import optimize
from .reporter import generate_report
from . import deploy as deployer


class WranglerPipeline:
    def __init__(self, manifest_path: str):
        self.manifest = PairFactory.load(manifest_path)
        self.manifest_dir = Path(manifest_path).parent
        self.results: dict[str, dict] = {}

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

    def run(self) -> dict:
        """Execute the full 6-phase pipeline."""
        print(f"{'=' * 60}")
        print(f"GEPA PROMPT WRANGLER")
        print(f"{'=' * 60}")
        print(f"  Experiment: {self.manifest.name}")
        print(f"  Pairs:      {len(self.manifest.pairs)}")
        print()

        eval_cases = self._load_eval_cases()
        print(f"  Eval cases: {len(eval_cases)}")

        for pair in self.manifest.pairs:
            self.results[pair.id] = {
                "model": pair.model,
                "original_prompt": pair.system_prompt,
            }

        # Phase 1: Deploy
        print(f"\n--- Phase 1: Deploy to GEAP ---")
        for pair in self.manifest.pairs:
            if pair.engine_id:
                print(f"  [{pair.id}] Using existing engine: {pair.engine_id}")
                self.results[pair.id]["engine_id"] = pair.engine_id
            else:
                agent = self._load_agent(pair)
                engine_id = deployer.deploy_agent(agent, display_name=pair.id)
                self.results[pair.id]["engine_id"] = engine_id

        # Phase 2: Baseline eval
        print(f"\n--- Phase 2: Baseline Evaluation ---")
        for pair in self.manifest.pairs:
            engine_id = self.results[pair.id]["engine_id"]
            print(f"\n  [{pair.id}]")
            scores = run_batch_eval(engine_id, eval_cases)
            self.results[pair.id]["before"] = scores
            for m, s in sorted(scores.items()):
                print(f"    {m:40s} {s:.2f}")

        # Phase 3: GEPA optimize
        print(f"\n--- Phase 3: GEPA Optimization ---")
        for pair in self.manifest.pairs:
            print(f"\n  [{pair.id}]")
            agent_path = self.manifest_dir / self.manifest.agent_module
            eval_path = self.manifest_dir / self.manifest.eval_data
            optimized = optimize(str(agent_path), str(eval_path))
            self.results[pair.id]["optimized_prompt"] = optimized
            pair.system_prompt = optimized

        # Phase 4: Redeploy with optimized prompt
        print(f"\n--- Phase 4: Redeploy with Optimized Prompt ---")
        for pair in self.manifest.pairs:
            engine_id = self.results[pair.id]["engine_id"]
            agent = self._load_agent(pair)
            deployer.update_agent(agent, engine_id, display_name=pair.id)

        # Phase 5: Post-optimization eval
        print(f"\n--- Phase 5: Post-Optimization Evaluation ---")
        for pair in self.manifest.pairs:
            engine_id = self.results[pair.id]["engine_id"]
            print(f"\n  [{pair.id}]")
            scores = run_batch_eval(engine_id, eval_cases)
            self.results[pair.id]["after"] = scores
            for m, s in sorted(scores.items()):
                print(f"    {m:40s} {s:.2f}")

        # Phase 6: Generate report
        print(f"\n--- Phase 6: Generate Report ---")
        generate_report(self.results, self.manifest.name)

        # Save raw results
        output_path = Path("outputs") / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"Results saved to: {output_path}")

        print(f"\n{'=' * 60}")
        print(f"COMPLETE")
        print(f"{'=' * 60}")

        return self.results
