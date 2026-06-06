"""Generate optimization analysis — charts + per-agent markdown reports.

Usage:
    uv run python scripts/generate_analysis.py
    uv run python scripts/generate_analysis.py --input outputs/demo_baseline_*.json
"""

import argparse
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from wrangler.config import REPORTS_DIR, OUTPUTS_DIR
from wrangler.analysis import (
    generate_agent_report, generate_comparison_report, normalize_agent_keys,
    generate_all_charts,
    METRIC_LABELS, AGENT_ORDER,
)

CHARTS_DIR = Path(REPORTS_DIR) / "charts"
AGENTS_DIR = Path(REPORTS_DIR) / "agents"


def load_results(input_path: str = None) -> dict:
    if input_path:
        with open(input_path) as f:
            return json.load(f)
    files = sorted(Path(OUTPUTS_DIR).glob("demo_*.json")) + sorted(Path(OUTPUTS_DIR).glob("results_*.json"))
    if not files:
        raise FileNotFoundError("No results files found in outputs/")
    with open(files[-1]) as f:
        return json.load(f)


def _get_agents(results: dict) -> list[str]:
    return [a for a in AGENT_ORDER if a in results]


def _get_case_metadata(results: dict) -> list[dict] | None:
    meta = results.get("_eval_metadata")
    if meta and "cases" in meta:
        return meta["cases"]
    return None


def main(input_path: str = None):
    print("Loading results...")
    results = normalize_agent_keys(load_results(input_path))
    case_metadata = _get_case_metadata(results)
    agents = _get_agents(results)
    print(f"  Loaded {len(agents)} agents")
    if case_metadata:
        print(f"  Eval metadata: {len(case_metadata)} cases")
    print()

    print("Generating charts...")
    generate_all_charts(results, case_metadata, CHARTS_DIR)

    print("\nGenerating per-agent reports...")
    for agent_name, data in results.items():
        if agent_name.startswith("_"):
            continue
        path = generate_agent_report(
            agent_name=agent_name,
            model=data.get("model", "unknown"),
            engine_id=data.get("engine_id", ""),
            original_prompt=data.get("original_prompt", ""),
            optimized_prompt=data.get("optimized_prompt"),
            before_scores=data.get("before", {}),
            after_scores=data.get("after"),
            before_per_case=data.get("before_per_case"),
            after_per_case=data.get("after_per_case"),
            case_metadata=case_metadata,
            before_std=data.get("before_std"),
            after_std=data.get("after_std"),
        )
        print(f"  {agent_name}: {path}")

    print("\nGenerating comparison report...")
    report_path = generate_comparison_report(results, case_metadata=case_metadata)
    print(f"  {report_path}")

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    args = parser.parse_args()
    main(args.input)
