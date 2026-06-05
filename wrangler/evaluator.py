"""Batch evaluation — runs inference + evaluation against deployed agents on GEAP."""

import statistics
import time
import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore", category=DeprecationWarning, module="vertexai")
warnings.filterwarnings("ignore", message=".*ExperimentalWarning.*")
warnings.filterwarnings("ignore", message=".*experimental.*")

import pandas as pd
import vertexai
from vertexai import Client, types

from .config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

GCS_EVAL_DEST = f"gs://{GCP_STAGING_BUCKET}/eval-results/"
MAX_POLL_SECONDS = 2400

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
    scores_std: dict[str, float] = field(default_factory=dict)
    num_runs: int = 1


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


def _fmt_elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def run_batch_eval(
    engine_id: str,
    eval_cases: list[dict],
    metrics: list | None = None,
    agent_name: str = "",
) -> EvalResult:
    """Run batch eval against a deployed agent. Returns EvalResult with aggregate and per-case scores."""
    tag = f"[{agent_name}] " if agent_name else ""

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

    print(f"  {tag}Inference: sending {len(eval_cases)} cases to engine {engine_id}...", flush=True)
    t0 = time.time()
    inference_result = client.evals.run_inference(
        agent=agent_resource,
        src=eval_df,
    )
    print(f"  {tag}Inference complete ({_fmt_elapsed(t0)})", flush=True)

    print(f"  {tag}Scoring: creating evaluation run ({len(metrics)} metrics)...", flush=True)
    eval_t0 = time.time()
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        agent=agent_resource,
        metrics=metrics,
        dest=GCS_EVAL_DEST,
    )
    run_id = evaluation_run.name.split("/")[-1] if evaluation_run.name else "unknown"
    print(f"  {tag}Scoring: eval run {run_id} created, polling for results...", flush=True)

    poll_start = time.time()
    poll_count = 0
    while time.time() - poll_start < MAX_POLL_SECONDS:
        evaluation_run = client.evals.get_evaluation_run(name=evaluation_run.name)
        state = str(getattr(evaluation_run, "state", ""))
        if "SUCCEEDED" in state or "FAILED" in state or "CANCELLED" in state:
            break
        poll_count += 1
        if poll_count % 4 == 0:
            print(f"  {tag}Scoring: still waiting... ({_fmt_elapsed(eval_t0)})", flush=True)
        time.sleep(15)

    print(f"  {tag}Scoring: {state} ({_fmt_elapsed(eval_t0)})", flush=True)

    if "FAILED" in state:
        err = getattr(evaluation_run, "error", None)
        print(f"  {tag}ERROR: Eval run failed: {err}")
        return EvalResult()

    if "SUCCEEDED" not in state:
        print(f"  {tag}ERROR: Eval run timed out after {MAX_POLL_SECONDS}s (state={state}). Re-run this pair.")
        return EvalResult()

    print(f"  {tag}Fetching per-case results...", flush=True)
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
        print(f"  {tag}Warning: {e}")

    scores = {}
    for key, value in raw_metrics.items():
        if "/AVERAGE" in key:
            metric_name = key.rsplit("/AVERAGE", 1)[0]
            short = metric_name.split("/")[-1] if "/" in metric_name else metric_name
            scores[short] = float(value)

    per_case = _extract_per_case_scores(evaluation_run)

    print(f"  {tag}Eval complete — total: {_fmt_elapsed(t0)}, {len(scores)} metrics, {len(per_case)} cases", flush=True)
    return EvalResult(scores=scores, per_case=per_case)


def run_batch_eval_averaged(
    engine_id: str,
    eval_cases: list[dict],
    num_runs: int = 1,
    metrics: list | None = None,
    agent_name: str = "",
) -> EvalResult:
    """Run batch eval N times and return averaged scores with std dev."""
    if num_runs <= 1:
        return run_batch_eval(engine_id, eval_cases, metrics=metrics, agent_name=agent_name)

    tag = f"[{agent_name}] " if agent_name else ""
    all_results: list[EvalResult] = []

    for i in range(num_runs):
        print(f"  {tag}Run {i + 1}/{num_runs}...", flush=True)
        result = run_batch_eval(engine_id, eval_cases, metrics=metrics, agent_name=agent_name)
        if result.scores:
            all_results.append(result)
            avg = sum(result.scores.values()) / max(len(result.scores), 1)
            print(f"  {tag}Run {i + 1}/{num_runs} avg: {avg:.3f}", flush=True)
        else:
            print(f"  {tag}Run {i + 1}/{num_runs} returned no scores (skipping from average)", flush=True)

    if not all_results:
        print(f"  {tag}WARNING: All {num_runs} runs returned no scores", flush=True)
        return EvalResult(num_runs=num_runs)

    all_metrics = set()
    for r in all_results:
        all_metrics.update(r.scores.keys())

    avg_scores: dict[str, float] = {}
    std_scores: dict[str, float] = {}
    for metric in sorted(all_metrics):
        values = [r.scores[metric] for r in all_results if metric in r.scores]
        avg_scores[metric] = statistics.mean(values)
        std_scores[metric] = statistics.stdev(values) if len(values) > 1 else 0.0

    avg_per_case: list[dict[str, float]] = []
    runs_with_cases = [r for r in all_results if r.per_case]
    if runs_with_cases:
        n_cases = max(len(r.per_case) for r in runs_with_cases)
        for case_idx in range(n_cases):
            case_metrics: dict[str, list[float]] = {}
            for r in runs_with_cases:
                if case_idx < len(r.per_case):
                    for k, v in r.per_case[case_idx].items():
                        case_metrics.setdefault(k, []).append(v)
            avg_per_case.append({k: statistics.mean(vs) for k, vs in case_metrics.items()})

    overall = sum(avg_scores.values()) / max(len(avg_scores), 1)
    successful = len(all_results)
    skipped = num_runs - successful
    skip_note = f" ({skipped} skipped — timeout/failure)" if skipped else ""
    print(f"  {tag}Averaged {successful}/{num_runs} runs — overall: {overall:.3f}{skip_note}", flush=True)

    return EvalResult(
        scores=avg_scores,
        per_case=avg_per_case,
        scores_std=std_scores,
        num_runs=num_runs,
    )


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
