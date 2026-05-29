"""Batch evaluation — runs inference + evaluation against deployed agents on GEAP."""

import time
from dataclasses import dataclass, field

import pandas as pd
import vertexai
from vertexai import Client, types

from .config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

GCS_EVAL_DEST = f"gs://{GCP_STAGING_BUCKET}/eval-results/"
MAX_POLL_SECONDS = 1200

DEFAULT_METRICS = [
    types.RubricMetric.FINAL_RESPONSE_QUALITY,
    types.RubricMetric.HALLUCINATION,
    types.RubricMetric.SAFETY,
    types.RubricMetric.TOOL_USE_QUALITY,
    types.RubricMetric.INSTRUCTION_FOLLOWING,
    types.RubricMetric.FINAL_RESPONSE_MATCH,
]


@dataclass
class EvalResult:
    """Evaluation result with aggregate and per-case scores."""

    scores: dict[str, float] = field(default_factory=dict)
    per_case: list[dict[str, float]] = field(default_factory=list)


def _build_eval_dataset(cases: list[dict]) -> pd.DataFrame:
    session_inputs = types.evals.SessionInput(user_id="wrangler-eval", state={})
    rows = []
    for case in cases:
        row = {
            "prompt": case["prompt"],
            "session_inputs": session_inputs,
            "expected_tool": case.get("expected_tool", ""),
            "case_description": case.get("description", ""),
        }
        if "reference" in case:
            row["reference"] = case["reference"]
        if "expected_response" in case:
            row["reference"] = case["expected_response"]
        rows.append(row)
    return pd.DataFrame(rows)


def _resolve_resource_name(engine_id: str) -> str:
    if engine_id.startswith("projects/"):
        return engine_id
    return f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"


def _extract_per_case_scores(evaluation_run) -> list[dict[str, float]]:
    """Extract per-case metric scores from evaluation run items."""
    per_case: list[dict[str, float]] = []
    try:
        run_results = getattr(evaluation_run, "evaluation_run_results", None)
        if not run_results:
            return per_case
        eval_items = getattr(run_results, "evaluation_items", None)
        if not eval_items:
            return per_case

        sorted_items = sorted(
            eval_items,
            key=lambda item: getattr(item, "eval_case_index", 0) or 0,
        )
        for item in sorted_items:
            case_scores: dict[str, float] = {}
            metric_results = getattr(item, "metric_results", None)
            if metric_results:
                items_dict = metric_results if isinstance(metric_results, dict) else dict(metric_results)
                for metric_key, metric_val in items_dict.items():
                    short = metric_key.split("/")[-1] if "/" in metric_key else metric_key
                    score = getattr(metric_val, "score", None) if not isinstance(metric_val, (int, float)) else metric_val
                    if score is not None:
                        case_scores[short] = float(score)
            per_case.append(case_scores)
    except Exception as e:
        print(f"  Warning extracting per-case scores: {e}")
    return per_case


def run_batch_eval(
    engine_id: str,
    eval_cases: list[dict],
    metrics: list | None = None,
) -> EvalResult:
    """Run batch eval against a deployed agent. Returns EvalResult with aggregate and per-case scores."""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )
    client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)
    agent_resource = _resolve_resource_name(engine_id)
    if metrics is None:
        metrics = DEFAULT_METRICS

    eval_df = _build_eval_dataset(eval_cases)

    print(f"  Running inference ({len(eval_cases)} cases)...", end="", flush=True)
    t0 = time.time()
    inference_result = client.evals.run_inference(
        agent=agent_resource,
        src=eval_df,
    )
    print(f" {time.time() - t0:.0f}s")

    print(f"  Creating evaluation run...", end="", flush=True)
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        agent=agent_resource,
        metrics=metrics,
        dest=GCS_EVAL_DEST,
    )

    poll_start = time.time()
    while time.time() - poll_start < MAX_POLL_SECONDS:
        evaluation_run = client.evals.get_evaluation_run(name=evaluation_run.name)
        state = str(getattr(evaluation_run, "state", ""))
        if "SUCCEEDED" in state or "FAILED" in state or "CANCELLED" in state:
            break
        print(".", end="", flush=True)
        time.sleep(15)
    print(f" {state}")

    if "FAILED" in state:
        err = getattr(evaluation_run, "error", None)
        print(f"  ERROR: {err}")
        return EvalResult()

    evaluation_run = client.evals.get_evaluation_run(
        name=evaluation_run.name,
        include_evaluation_items=True,
    )

    raw_metrics: dict = {}
    try:
        run_results = getattr(evaluation_run, "evaluation_run_results", None)
        if run_results:
            sm = getattr(run_results, "summary_metrics", None)
            if sm:
                nested = getattr(sm, "metrics", None)
                if nested:
                    raw_metrics = dict(nested) if not isinstance(nested, dict) else nested
    except Exception as e:
        print(f"  Warning: {e}")

    scores = {}
    for key, value in raw_metrics.items():
        if "/AVERAGE" in key:
            metric_name = key.rsplit("/AVERAGE", 1)[0]
            short = metric_name.split("/")[-1] if "/" in metric_name else metric_name
            scores[short] = float(value)

    per_case = _extract_per_case_scores(evaluation_run)

    return EvalResult(scores=scores, per_case=per_case)


def save_eval_results(
    agent_name: str,
    scores: dict[str, float],
    phase: str = "baseline",
    output_dir: str | None = None,
) -> str:
    """Save eval results to JSON. Returns the file path."""
    import json
    from datetime import datetime
    from pathlib import Path

    output_dir = Path(output_dir or "outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"eval_{agent_name}_{phase}_{timestamp}.json"
    path = output_dir / filename

    data = {
        "agent": agent_name,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "scores": scores,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return str(path)
