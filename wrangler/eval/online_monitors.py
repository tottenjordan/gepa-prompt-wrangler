"""Online Monitors — run quick evals against deployed agents and track results.

Online Monitors complement Online Evaluators by running on-demand evaluation
against deployed agents. While Online Evaluators automatically score OTel traces
every 10 minutes, Online Monitors let you trigger evaluations explicitly and
store results for trend analysis.

Usage:
    uv run python -m wrangler.eval.online_monitors <engine-id>
    uv run python -m wrangler.eval.online_monitors <engine-id> --cases 10
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agentplatform import types

# `init` sets process-global project/location/staging_bucket. vertexai
# and agentplatform both re-export the *same* bound method on the same
# google.cloud.aiplatform initializer object -- verified `is` identical --
# so it is imported from the canonical source. agentplatform's re-export
# falls back to `init = None` when the import fails, which types as
# `... | None` and is not callable as far as ty is concerned.
from google.cloud.aiplatform import init as vertex_init

from wrangler.core.clients import agent_client

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
    """Run a quick evaluation against a deployed agent.

    Returns the scores, `{}` when there are none. **The return value alone cannot tell you
    why it is empty** -- that distinction lives in the record written to
    `outputs/monitors/<run_id>.json`, which is what a trend reading consumes:

    | `scores` | `scores_error` | `scores_empty_reason` | meaning |
    | --- | --- | --- | --- |
    | populated | `""` | `""` | measured |
    | `{}` | `""` | set | nothing to measure -- the reason says which level was absent |
    | `{}` | set | `""` | **not measured**; extraction raised, the point does not exist |

    Two fields because there are two ways to end up empty, and only one of them is a
    failure. An exception is caught by the `try`; a response whose shape has moved is *not*,
    because every `getattr` in the chain passes a default and collapses silently. Naming the
    missing level separates a FAILED run, which legitimately has no results, from an SDK
    surface change -- and from the key format changing, which would otherwise report `{}` on
    every engine forever while looking like an agent that scores nothing.

    The return type is unchanged deliberately: `__main__` is the only caller today, and a
    breaking signature change would buy nothing the record does not already carry.
    """
    agent_resource = _resolve_agent_resource(agent_id)
    cases = QUICK_EVAL_CASES[:num_cases] if num_cases else QUICK_EVAL_CASES
    run_id = f"monitor_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"

    vertex_init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )
    client = agent_client(project=GCP_PROJECT_ID, location=GCP_REGION)

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
    gcs_dest = f"gs://{GCP_STAGING_BUCKET}/monitor-results/"
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        agent=agent_resource,
        metrics=EVAL_METRICS,
        dest=gcs_dest,
        labels={"solution": "promp-wrangler"},
    )

    poll_start = time.time()
    state = ""
    while time.time() - poll_start < 600:
        evaluation_run = client.evals.get_evaluation_run(name=evaluation_run.name)
        state = str(getattr(evaluation_run, "state", ""))
        if "SUCCEEDED" in state or "FAILED" in state:
            break
        print(".", end="", flush=True)
        time.sleep(15)
    print(f" {state}")

    # `scores_error` is the difference between "the agent scored nothing" and "we could not
    # read the scores". Both used to produce `{}` plus a `Warning:` line that scrolls past in
    # a long log -- and the empty record was then written into the directory this module
    # exists to accumulate for trend analysis, where a missing measurement plots as a zero.
    #
    # What reaches the handler is the conversion below -- `float(v)` on a non-numeric value,
    # or `dict(nested)` on something that is not a mapping. Note the getattr chain does NOT:
    # each call passes a default, so a moved attribute is swallowed and degrades to `{}` by a
    # separate, silent route. Both are what an upstream shape change looks like from here,
    # and that surface already moved once at google-cloud-aiplatform 2.1.0.
    scores: dict[str, float] = {}
    scores_error = ""
    # Why `scores` is empty when nothing raised. Each getattr below passes a default, so a
    # response that has moved collapses to `{}` **silently** -- the second route to the same
    # indistinguishability, and the one a `try/except` cannot see. Naming the level that was
    # missing separates a FAILED run (legitimately has no results) from a shape change.
    empty_reason = ""
    try:
        run_results = getattr(evaluation_run, "evaluation_run_results", None)
        sm = getattr(run_results, "summary_metrics", None) if run_results else None
        nested = getattr(sm, "metrics", None) if sm else None
        if not run_results:
            empty_reason = "no evaluation_run_results"
        elif not sm:
            empty_reason = "results present but no summary_metrics"
        elif not nested:
            empty_reason = "summary_metrics present but no metrics"
        else:
            items = (dict(nested) if not isinstance(nested, dict) else nested).items()
            for k, v in items:
                if "/AVERAGE" in k:
                    short = k.rsplit("/AVERAGE", 1)[0].split("/")[-1]
                    # Alias the custom tool-use metric to the report key,
                    # matching run_batch_eval (single tool-use metric, so
                    # the predefined key never co-occurs).
                    scores[_alias_tool_use_key(short)] = float(v)
            if not scores:
                # The key format changing would otherwise report `{}` forever, on every
                # engine, looking exactly like an agent that scores nothing.
                empty_reason = f"{len(items)} metric(s), none matching /AVERAGE"
    except Exception as e:
        scores_error = f"{type(e).__name__}: {e}"
        print(f"  Warning: could not read scores — {scores_error}")

    print("\n  Results:")
    if scores_error:
        print("    UNREADABLE — scores could not be extracted; this run measured nothing.")
        print("    Not the same as a zero. Treat the record as missing, not as data.")
    elif not scores:
        print(f"    none reported — {empty_reason} (run state: {state or 'unknown'})")
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
        # Empty string when extraction succeeded. A reader deciding whether to plot a point
        # should check this first: truthy means the point does not exist, rather than being
        # zero.
        "scores_error": scores_error,
        # Why `scores` is empty when nothing raised. Empty string when scores were found.
        "scores_empty_reason": empty_reason,
        # Recorded so an empty `scores` with no error is still explicable -- a FAILED run and
        # a SUCCEEDED one that reported no metrics are different things, and the state was
        # previously computed and thrown away.
        "eval_run_state": state,
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
        print("Usage: python -m wrangler.eval.online_monitors <engine-id> [--cases N]")
        sys.exit(1)

    agent_id = sys.argv[1]
    num_cases = None
    if "--cases" in sys.argv:
        idx = sys.argv.index("--cases")
        if idx + 1 < len(sys.argv):
            num_cases = int(sys.argv[idx + 1])

    run_quick_eval(agent_id, num_cases)
