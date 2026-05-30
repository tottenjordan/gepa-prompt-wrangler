"""Resume the experiment from Phase 3 using saved baseline results."""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrangler.runner import WranglerPipeline, _fmt_duration

MANIFEST = "examples/multi_model_agents/manifest.yaml"

BASELINE_SCORES = {
    "lite-gemini-3.1-flash-lite": {
        "final_response_match_v2": 0.81,
        "final_response_quality_v1": 0.86,
        "hallucination_v1": 1.00,
        "instruction_following_v1": 0.76,
        "safety_v1": 1.00,
        "tool_use_quality_v1": 0.39,
    },
    "flash-gemini-3.5-flash": {
        "final_response_match_v2": 0.78,
        "final_response_quality_v1": 0.92,
        "hallucination_v1": 1.00,
        "instruction_following_v1": 0.80,
        "safety_v1": 0.98,
        "tool_use_quality_v1": 0.41,
    },
    "pro-gemini-3.1-pro": {
        "final_response_match_v2": 0.80,
        "final_response_quality_v1": 0.92,
        "hallucination_v1": 1.00,
        "instruction_following_v1": 0.73,
        "safety_v1": 0.92,
        "tool_use_quality_v1": 0.42,
    },
    "sonnet-claude-4": {
        "final_response_match_v2": 0.83,
        "final_response_quality_v1": 0.89,
        "hallucination_v1": 0.91,
        "instruction_following_v1": 0.81,
        "safety_v1": 0.88,
        "tool_use_quality_v1": 0.41,
    },
    "opus-claude-4": {
        "final_response_match_v2": 0.91,
        "final_response_quality_v1": 0.89,
        "hallucination_v1": 0.91,
        "instruction_following_v1": 0.79,
        "safety_v1": 0.70,
        "tool_use_quality_v1": 0.42,
    },
}


def main():
    pipeline = WranglerPipeline(MANIFEST)
    pipeline._pipeline_start = time.time()
    eval_cases = pipeline._load_eval_cases()
    n_pairs = len(pipeline.manifest.pairs)

    print(f"{'=' * 60}")
    print(f"GEPA PROMPT WRANGLER — RESUMING FROM PHASE 3")
    print(f"{'=' * 60}")
    print(f"  Experiment: {pipeline.manifest.name}")
    print(f"  Pairs:      {n_pairs}")
    print()

    for pair in pipeline.manifest.pairs:
        pipeline.results[pair.id] = {
            "model": pair.model,
            "original_prompt": pair.system_prompt,
            "engine_id": pair.engine_id,
            "before": BASELINE_SCORES[pair.id],
        }

    pipeline.results["_eval_metadata"] = {
        "case_count": len(eval_cases),
        "cases": [
            {"tier": c.get("tier", ""), "category": c.get("category", ""), "prompt": c.get("prompt", "")}
            for c in eval_cases
        ],
    }

    print("  Baseline scores loaded from previous run:")
    for pair_id, scores in BASELINE_SCORES.items():
        avg = sum(scores.values()) / len(scores)
        print(f"    [{pair_id}] avg: {avg:.2f}")
    print()

    # Phase 3: GEPA optimize (sequential to avoid MCP/API contention)
    with pipeline._phase("Phase 3: GEPA Optimization"):
        pipeline._run_optimize_sequential()

    # Phase 4: Redeploy with optimized prompt
    with pipeline._phase("Phase 4: Redeploy with Optimized Prompt"):
        from wrangler import deploy as deployer

        for i, pair in enumerate(pipeline.manifest.pairs, 1):
            print(f"  [{pair.id}] ({i}/{n_pairs}) Redeploying...", end="", flush=True)
            t0 = time.time()
            engine_id = pipeline.results[pair.id]["engine_id"]
            agent = pipeline._load_agent(pair)
            deployer.update_agent(agent, engine_id, display_name=pair.id)
            print(f" {_fmt_duration(time.time() - t0)}")

    # Phase 5: Post-optimization eval (parallel)
    with pipeline._phase("Phase 5: Post-Optimization Evaluation"):
        pipeline._run_eval_parallel(eval_cases, n_pairs, phase="after")

    # Phase 6: Generate report
    with pipeline._phase("Phase 6: Generate Report"):
        from wrangler.reporter import generate_report

        generate_report(pipeline.results, pipeline.manifest.name)

    # Save raw results
    output_path = Path("outputs") / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(pipeline.results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")

    # Final timing summary
    total = time.time() - pipeline._pipeline_start
    print(f"\n{'=' * 60}")
    print(f"PIPELINE COMPLETE — Total: {_fmt_duration(total)}")
    print(f"{'=' * 60}")
    for phase_name, phase_time in pipeline._phase_times:
        print(f"  {phase_name:40s} {_fmt_duration(phase_time):>8s}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
