"""Online Monitors — run quick evals against deployed agents and track results.

Online Monitors complement Online Evaluators by running on-demand evaluation
against deployed agents. While Online Evaluators automatically score OTel traces
every 10 minutes, Online Monitors let you trigger evaluations explicitly and
store results for trend analysis.

Usage:
    uv run python -m wrangler.online_monitors <engine-id>
    uv run python -m wrangler.online_monitors <engine-id> --cases 10
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import vertexai
from vertexai import Client, types

from ..core.config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET, OUTPUTS_DIR
from .evaluator import _alias_tool_use_key, _tool_use_metric

QUICK_EVAL_CASES = [
    "Find flights from SFO to JFK",
    "Search for hotels in New York",
    "What is the meal expense limit?",
    "Book flight FL001 for Alice Johnson",
    "Check if a $50 transport expense is within policy",
    "Submit a $45 meals expense for lunch, user ID EMP001",
    "Find flights to NYC and compare options",
    "Show expenses for user EMP001",
]

# Use the explicit-rubric tool-use metric, NOT the predefined TOOL_USE_QUALITY:
# the predefined one auto-generates inverted rubrics that penalize correct tool
# use (see wrangler.eval.evaluator._tool_use_metric for details).
EVAL_METRICS = [
    types.RubricMetric.FINAL_RESPONSE_QUALITY,
    types.RubricMetric.SAFETY,
    _tool_use_metric(),
]


def _resolve_agent_resource(agent_id: str) -> str:
    if agent_id.startswith("projects/"):
        return agent_id
    return f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{agent_id}"


def run_quick_eval(agent_id: str, num_cases: int | None = None) -> dict:
    """Run a quick evaluation against a deployed agent."""
    agent_resource = _resolve_agent_resource(agent_id)
    cases = QUICK_EVAL_CASES[:num_cases] if num_cases else QUICK_EVAL_CASES
    run_id = f"monitor_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"

    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )
    client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)

    print(f"Online Monitor: {agent_resource}")
    print(f"  Run ID: {run_id}")
    print(f"  Cases:  {len(cases)}")

    import pandas as pd

    session_inputs = types.evals.SessionInput(user_id="monitor-user", state={})
    eval_df = pd.DataFrame([{"prompt": case, "session_inputs": session_inputs} for case in cases])

    print("  Running inference...", end="", flush=True)
    t0 = time.time()
    inference_result = client.evals.run_inference(
        agent=agent_resource,
        src=eval_df,
    )
    print(f" {time.time() - t0:.0f}s")

    print("  Creating evaluation run...", end="", flush=True)
    GCS_DEST = f"gs://{GCP_STAGING_BUCKET}/monitor-results/"
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        agent=agent_resource,
        metrics=EVAL_METRICS,
        dest=GCS_DEST,
        labels={"solution": "promp-wrangler"},
    )

    poll_start = time.time()
    while time.time() - poll_start < 600:
        evaluation_run = client.evals.get_evaluation_run(name=evaluation_run.name)
        state = str(getattr(evaluation_run, "state", ""))
        if "SUCCEEDED" in state or "FAILED" in state:
            break
        print(".", end="", flush=True)
        time.sleep(15)
    print(f" {state}")

    scores = {}
    try:
        run_results = getattr(evaluation_run, "evaluation_run_results", None)
        if run_results:
            sm = getattr(run_results, "summary_metrics", None)
            if sm:
                nested = getattr(sm, "metrics", None)
                if nested:
                    for k, v in (dict(nested) if not isinstance(nested, dict) else nested).items():
                        if "/AVERAGE" in k:
                            short = k.rsplit("/AVERAGE", 1)[0].split("/")[-1]
                            # Alias the custom tool-use metric to the report key,
                            # matching run_batch_eval (single tool-use metric, so
                            # the predefined key never co-occurs).
                            scores[_alias_tool_use_key(short)] = float(v)
    except Exception as e:
        print(f"  Warning: {e}")

    print("\n  Results:")
    for m, s in sorted(scores.items()):
        print(f"    {m:40s} {s:.2f}")

    # Save results
    output_dir = Path(OUTPUTS_DIR) / "monitors"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "agent_id": agent_id,
        "run_id": run_id,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "num_cases": len(cases),
        "scores": scores,
    }
    output_path = output_dir / f"{run_id}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return scores


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()
    example_env = Path(__file__).parent.parent / "examples" / "multi_model_agents" / ".env"
    if example_env.exists():
        load_dotenv(str(example_env), override=True)

    if len(sys.argv) < 2:
        print("Usage: python -m wrangler.online_monitors <engine-id> [--cases N]")
        sys.exit(1)

    agent_id = sys.argv[1]
    num_cases = None
    if "--cases" in sys.argv:
        idx = sys.argv.index("--cases")
        if idx + 1 < len(sys.argv):
            num_cases = int(sys.argv[idx + 1])

    run_quick_eval(agent_id, num_cases)
