"""Batch evaluation — runs inference + evaluation against deployed agents on GEAP."""

import contextlib
import functools
import math
import statistics
import time
import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore", category=DeprecationWarning, module="vertexai")
warnings.filterwarnings("ignore", message=".*ExperimentalWarning.*")
warnings.filterwarnings("ignore", message=".*experimental.*")

# E402 below is deliberate: the filterwarnings() calls above must execute
# before vertexai is imported, or its import-time warnings escape.

import pandas as pd  # noqa: E402
import vertexai  # noqa: E402
from vertexai import Client, types  # noqa: E402
from vertexai._genai import _evals_common  # noqa: E402

from ..core.config import (  # noqa: E402
    GCP_PROJECT_ID,
    GCP_REGION,
    GCP_STAGING_BUCKET,
    get_batch_config,
)

GCS_EVAL_DEST = f"gs://{GCP_STAGING_BUCKET}/eval-results"
MAX_POLL_SECONDS = 2400

# Explicit tool-use criteria, mirroring the GEPA sampler_config's
# rubric_based_tool_use_quality_v1 rubrics (correct_tool_selection +
# correct_parameters). See _tool_use_metric() for why the predefined
# tool_use_quality_v1 cannot be used directly.
# Reference-free by design: the judge scores trajectory correctness
# intrinsically (from the prompt, response, and tool calls) and does NOT
# consult the golden expected_tools — mirroring the predefined metric it
# replaced, which is also reference-free.
_TOOL_USE_JUDGE_PROMPT = """\
You are an expert evaluator scoring whether an AI agent used its tools correctly \
to fulfill a user's request. You are given the user prompt, the agent's final \
response, and the agent's execution trajectory (the tools it called and with what \
arguments).

# User prompt
{prompt}

# Agent final response
{response}

# Agent execution trajectory (tool calls + arguments)
{agent_data}

# Evaluation criteria
Judge the agent ONLY on tool use. Calling tools to satisfy the request is the \
correct behavior — do NOT penalize the agent for calling tools, and do NOT reward \
refusing to act. Score against these two criteria:
1. Correct tool selection: The agent selected the appropriate tool(s) to fulfill \
the user's request.
2. Correct parameters: The agent provided correct and complete parameters to the \
tool(s) it called.

# Scoring
Score in [0.0, 1.0]:
- 1.0  = correct tool(s) selected AND correct/complete parameters.
- ~0.5 = right tool but missing/incorrect parameters, OR partially correct selection.
- 0.0  = wrong tool(s), no tool call when one was clearly required, or wrong parameters.

# Output format
Respond with ONLY a single JSON object and nothing else, in exactly this form:
{{"explanation": "<one-paragraph rationale>", "score": <float between 0.0 and 1.0>}}
"""

# The metric's own name. It must NOT be "tool_use_quality_v1" (a predefined
# metric name) — the SDK routes any metric with that name to the predefined
# handler and IGNORES a custom prompt_template (verified against the SDK's
# t_metrics + handler dispatch). So we name it "tool_use_quality" to route
# through the LLM-judge handler, then alias the resulting score key back to
# "tool_use_quality_v1" so downstream reporting/analysis keep working.
_TOOL_USE_METRIC_NAME = "tool_use_quality"
_TOOL_USE_REPORT_KEY = "tool_use_quality_v1"


def _alias_tool_use_key(short: str) -> str:
    """Map the custom tool-use metric's short name to the report key.

    Returns ``_TOOL_USE_REPORT_KEY`` when ``short`` is the custom metric name,
    otherwise returns ``short`` unchanged. Shared by run_batch_eval (aggregate
    + per_case) and online_monitors so all entry points alias identically.
    """
    return _TOOL_USE_REPORT_KEY if short == _TOOL_USE_METRIC_NAME else short


def _tool_use_metric() -> "types.LLMMetric":
    """Build the tool-use metric used by batch eval.

    The predefined ``tool_use_quality_v1`` metric (``RubricMetric.TOOL_USE_QUALITY``)
    auto-generates its rubrics server-side while BLIND to the agent's available
    tools. For a correctly-tool-using agent it produces inverted rubrics that
    PENALIZE tool use (e.g. ``NO_TOOL_CALL``: "correctly refrains from making a
    tool call, as no tools have been provided", ``INFORMS_USER_OF_INABILITY``).
    Only the INTENT rubric passes, structurally capping the score near ~0.33-0.5
    even when the agent calls the correct tool with correct args.

    To fix this we score with an explicit LLM-judge metric whose criteria mirror
    the GEPA sampler_config's ``rubric_based_tool_use_quality_v1`` rubrics
    (correct_tool_selection + correct_parameters), so correct tool use scores
    well. We use ``LLMMetric`` (not the predefined name) because a metric named
    ``tool_use_quality_v1`` is routed to the predefined handler regardless of any
    custom prompt — the predefined path ignores explicit criteria. The resulting
    score key is aliased back to ``tool_use_quality_v1`` in run_batch_eval so
    report lookups are unaffected.
    """
    # NOTE: do not set judge_model to a bare model id here — the evaluation_run
    # API requires a full autorater_model resource name and rejects "gemini-2.5-flash"
    # with INVALID_ARGUMENT. Leaving it unset uses the service default autorater,
    # matching how the predefined metrics behave.
    return types.LLMMetric(
        name=_TOOL_USE_METRIC_NAME,
        prompt_template=_TOOL_USE_JUDGE_PROMPT,
    )


# Metric versions are PINNED deliberately. An unversioned RubricMetric resolves
# client-side through the SDK's METRIC_LATEST_SPEC_NAME table, which in
# google-cloud-aiplatform 1.165.1 points at hallucination_v2 / safety_v3 /
# instruction_following_v2 — versions the us-central1 eval service does not yet
# serve. It rejects them per-metric with "Unsupported predefined metric: <name>",
# and because the SDK's own CandidateResult model has no `error` field and is
# extra='forbid', that per-metric error makes the ENTIRE result file fail to
# parse. The SDK swallows the exception and returns an empty result, so a single
# unsupported metric silently zeroes out every per-case score in the run.
#
# Pinning v1 keeps us on versions the service actually serves. Revisit when the
# service catches up (symptom of over-pinning is the opposite error: the service
# reporting the v1 name as retired).
DEFAULT_METRICS = [
    types.RubricMetric.FINAL_RESPONSE_QUALITY(version="v1"),
    types.RubricMetric.HALLUCINATION(version="v1"),
    types.RubricMetric.SAFETY(version="v1"),
    _tool_use_metric(),
    types.RubricMetric.INSTRUCTION_FOLLOWING(version="v1"),
]


@dataclass
class EvalResult:
    """Evaluation result with aggregate and per-case scores."""

    scores: dict[str, float] = field(default_factory=dict)
    per_case: list[dict[str, float]] = field(default_factory=list)
    scores_std: dict[str, float] = field(default_factory=dict)
    num_runs: int = 1
    token_usage: dict[str, int | bool] = field(default_factory=dict)


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


def _extract_aggregate_scores(evaluation_run) -> dict[str, float]:
    """Extract aggregate (run-level) metric scores from an evaluation run.

    Reads ``evaluation_run.evaluation_run_results.summary_metrics.metrics``,
    keeps only the ``<metric>/AVERAGE`` entries, strips the metric short name,
    and aliases the custom tool-use metric key back to the predefined report
    key via ``_alias_tool_use_key`` so reporting/analysis lookups keep working.

    Pure and network-free — factored out of run_batch_eval so the
    extraction+alias path can be unit-tested against a stub run object.
    """
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
        print(f"  Warning extracting aggregate scores: {e}")

    scores: dict[str, float] = {}
    for key, value in raw_metrics.items():
        if "/AVERAGE" in key:
            metric_name = key.rsplit("/AVERAGE", 1)[0]
            short = metric_name.split("/")[-1] if "/" in metric_name else metric_name
            # Alias the custom LLM-judge tool-use metric back to the predefined
            # report key so reporting/analysis lookups keep working. Safe to
            # clobber _TOOL_USE_REPORT_KEY: only one tool-use metric is ever
            # sent, so the predefined key never co-occurs with the aliased one.
            scores[_alias_tool_use_key(short)] = float(value)
    return scores


def _extract_per_case_scores(evaluation_run) -> list[dict[str, float]]:
    """Extract per-case metric scores from evaluation run results.

    Primary path: evaluation_item_results.eval_case_results[i]
                  .response_candidate_results[0].metric_results

    The SDK populates evaluation_item_results via
    _convert_evaluation_run_results(), which returns None when
    evaluation_run_results.evaluation_set is missing. When that
    happens, fall back to fetching evaluation items directly via
    the API client.
    """
    per_case: list[dict[str, float]] = []
    try:
        item_results = getattr(evaluation_run, "evaluation_item_results", None)
        if item_results is None:
            per_case = _extract_per_case_via_api(evaluation_run)
            if not per_case:
                print(
                    "  Warning: evaluation_item_results is None and API fallback returned no results"
                )
            return per_case

        case_results = getattr(item_results, "eval_case_results", None)
        if not case_results:
            print("  Warning: eval_case_results is empty")
            return per_case

        for case_result in case_results:
            case_scores: dict[str, float] = {}
            candidates = getattr(case_result, "response_candidate_results", [])
            if candidates:
                metric_results = getattr(candidates[0], "metric_results", None)
                if metric_results:
                    items_dict = (
                        metric_results if isinstance(metric_results, dict) else dict(metric_results)
                    )
                    for metric_key, metric_val in items_dict.items():
                        short = metric_key.split("/")[-1] if "/" in metric_key else metric_key
                        score = (
                            getattr(metric_val, "score", None)
                            if not isinstance(metric_val, (int, float))
                            else metric_val
                        )
                        if score is not None:
                            case_scores[short] = float(score)
            per_case.append(case_scores)
    except Exception as e:
        print(f"  Warning extracting per-case scores: {e}")
    return per_case


def _extract_per_case_via_api(evaluation_run) -> list[dict[str, float]]:
    """Fallback: fetch per-case scores directly from the Evaluation Management API."""
    per_case: list[dict[str, float]] = []
    try:
        run_results = getattr(evaluation_run, "evaluation_run_results", None)
        if not run_results or not getattr(run_results, "evaluation_set", None):
            return per_case

        client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)
        eval_set_name = run_results.evaluation_set
        eval_set = client.evals.get_evaluation_set(name=eval_set_name)
        if not eval_set or not getattr(eval_set, "evaluation_items", None):
            return per_case

        for item_name in eval_set.evaluation_items:
            case_scores: dict[str, float] = {}
            # One unreadable item must not drop the scores for every other case;
            # it just contributes an empty dict.
            with contextlib.suppress(Exception):
                item = client.evals.get_evaluation_item(name=item_name)
                response = getattr(item, "evaluation_response", None)
                if response:
                    for candidate in getattr(response, "candidate_results", []):
                        metric = getattr(candidate, "metric", "")
                        score = getattr(candidate, "score", None)
                        if metric and score is not None:
                            short = metric.split("/")[-1] if "/" in metric else metric
                            case_scores[short] = float(score)
            per_case.append(case_scores)
    except Exception as e:
        print(f"  Warning in API fallback for per-case scores: {e}")
    return per_case


def _estimate_token_usage(inference_df: pd.DataFrame) -> dict[str, int | bool]:
    """Estimate input/output token counts from inference result text lengths."""
    input_tokens = 0
    output_tokens = 0
    for _, row in inference_df.iterrows():
        prompt = row.get("prompt", "")
        response = row.get("response", "")
        if isinstance(prompt, str):
            input_tokens += max(1, len(prompt) // 4)
        if isinstance(response, str):
            output_tokens += max(1, len(response) // 4)
        elif isinstance(response, dict):
            output_tokens += max(1, len(str(response)) // 4)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "is_estimate": True,
    }


def _fmt_elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def _run_batched_inference(
    client: Client,
    agent_resource: str,
    eval_df: pd.DataFrame,
    batch_size: int,
    delay: float,
    max_workers: int,
    tag: str = "",
) -> types.EvaluationDataset:
    """Run inference in batches with rate-limit-aware throttling."""
    n_cases = len(eval_df)
    n_batches = math.ceil(n_cases / batch_size)

    original_max_workers = _evals_common.AGENT_MAX_WORKERS
    original_retry_fn = _evals_common._execute_agent_run_with_retry

    try:
        # Deliberate monkey-patch: ty infers Literal[20] from the module constant.
        _evals_common.AGENT_MAX_WORKERS = min(max_workers, batch_size)  # ty: ignore[invalid-assignment]

        @functools.wraps(original_retry_fn)
        def _patched_retry(*args, max_retries=6, **kwargs):
            return original_retry_fn(*args, max_retries=max_retries, **kwargs)

        # Deliberate monkey-patch: the functools.wraps wrapper is not the same type.
        _evals_common._execute_agent_run_with_retry = _patched_retry  # ty: ignore[invalid-assignment]

        all_dfs = []
        for i in range(n_batches):
            start = i * batch_size
            end = min(start + batch_size, n_cases)
            chunk = eval_df.iloc[start:end].reset_index(drop=True)

            if n_batches > 1:
                print(f"  {tag}Batch {i + 1}/{n_batches} (cases {start + 1}-{end})...", flush=True)

            result = client.evals.run_inference(agent=agent_resource, src=chunk)
            all_dfs.append(result.eval_dataset_df)

            if delay > 0 and i < n_batches - 1:
                time.sleep(delay)

        combined_df = pd.concat(all_dfs, ignore_index=True)
        return types.EvaluationDataset(eval_dataset_df=combined_df)
    finally:
        _evals_common.AGENT_MAX_WORKERS = original_max_workers
        _evals_common._execute_agent_run_with_retry = original_retry_fn


def _retry_failed_cases(
    client: Client,
    agent_resource: str,
    eval_df: pd.DataFrame,
    inference_result: types.EvaluationDataset,
    model: str,
    tag: str = "",
) -> types.EvaluationDataset:
    """Detect failed inference cases and re-run them in micro-batches."""
    result_df = inference_result.eval_dataset_df
    failed_indices = []

    for idx, row in result_df.iterrows():
        response = row.get("response")
        if (
            (
                response is None
                or (not isinstance(response, dict) and pd.isna(response))
                or response == ""
            )
            or (isinstance(response, dict) and "error" in response)
            or (isinstance(response, str) and response.strip() == "")
        ):
            failed_indices.append(idx)

    if not failed_indices:
        return inference_result

    n_failed = len(failed_indices)
    print(
        f"  {tag}Detected {n_failed}/{len(result_df)} failed cases — waiting 30s for rate limit cooldown...",
        flush=True,
    )
    time.sleep(30)

    failed_eval_df = eval_df.iloc[failed_indices].reset_index(drop=True)
    retry_result = _run_batched_inference(
        client,
        agent_resource,
        failed_eval_df,
        batch_size=2,
        delay=20.0,
        max_workers=2,
        tag=f"{tag}retry ",
    )

    recovered = 0
    retry_df = retry_result.eval_dataset_df
    for retry_idx, original_idx in enumerate(failed_indices):
        if retry_idx < len(retry_df):
            retry_response = retry_df.iloc[retry_idx].get("response")
            if (
                retry_response is not None
                and retry_response != ""
                and not (isinstance(retry_response, dict) and "error" in retry_response)
            ):
                result_df.iloc[original_idx] = retry_df.iloc[retry_idx]
                recovered += 1

    print(f"  {tag}Recovered {recovered}/{n_failed} failed cases", flush=True)
    return types.EvaluationDataset(eval_dataset_df=result_df)


def run_batch_eval(
    engine_id: str,
    eval_cases: list[dict],
    metrics: list | None = None,
    agent_name: str = "",
    model: str = "",
    retry_failed: bool = True,
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
    batch_size, delay, max_workers = get_batch_config(model)

    if batch_size < len(eval_cases):
        print(
            f"  {tag}Inference: sending {len(eval_cases)} cases in batches of {batch_size} "
            f"({max_workers} workers, {delay}s delay) to engine {engine_id}...",
            flush=True,
        )
    else:
        print(
            f"  {tag}Inference: sending {len(eval_cases)} cases to engine {engine_id}...",
            flush=True,
        )

    t0 = time.time()
    inference_result = _run_batched_inference(
        client,
        agent_resource,
        eval_df,
        batch_size,
        delay,
        max_workers,
        tag,
    )

    if retry_failed:
        inference_result = _retry_failed_cases(
            client,
            agent_resource,
            eval_df,
            inference_result,
            model,
            tag,
        )

    print(f"  {tag}Inference complete ({_fmt_elapsed(t0)})", flush=True)

    # Clean invalid rows before scoring — rows with NaN/float in response or
    # agent_data cause ValidationError in the SDK
    result_df = inference_result.eval_dataset_df

    def _is_invalid(val):
        return val is None or (isinstance(val, float) and pd.isna(val)) or val == ""

    invalid_mask = result_df["response"].apply(_is_invalid)
    if "agent_data" in result_df.columns:
        invalid_mask = invalid_mask | result_df["agent_data"].apply(_is_invalid)
    n_invalid = invalid_mask.sum()
    if n_invalid > 0:
        print(
            f"  {tag}Dropped {n_invalid}/{len(result_df)} rows with invalid agent_data", flush=True
        )
        clean_df = result_df[~invalid_mask].reset_index(drop=True)
        inference_result = types.EvaluationDataset(eval_dataset_df=clean_df)

    print(f"  {tag}Scoring: creating evaluation run ({len(metrics)} metrics)...", flush=True)
    eval_t0 = time.time()
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        agent=agent_resource,
        metrics=metrics,
        dest=GCS_EVAL_DEST,
        labels={"solution": "promp-wrangler"},
    )
    run_id = evaluation_run.name.split("/")[-1] if evaluation_run.name else "unknown"
    print(f"  {tag}Scoring: eval run {run_id} created, polling for results...", flush=True)

    poll_start = time.time()
    poll_count = 0
    state = ""
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
        print(
            f"  {tag}ERROR: Eval run timed out after {MAX_POLL_SECONDS}s (state={state}). Re-run this pair."
        )
        return EvalResult()

    print(f"  {tag}Fetching per-case results...", flush=True)
    evaluation_run = client.evals.get_evaluation_run(
        name=evaluation_run.name,
        include_evaluation_items=True,
    )

    scores = _extract_aggregate_scores(evaluation_run)

    per_case = _extract_per_case_scores(evaluation_run)
    for case_scores in per_case:
        # Same single-tool-use-metric clobber assumption as above.
        if _TOOL_USE_METRIC_NAME in case_scores:
            case_scores[_alias_tool_use_key(_TOOL_USE_METRIC_NAME)] = case_scores.pop(
                _TOOL_USE_METRIC_NAME
            )
    token_usage = _estimate_token_usage(inference_result.eval_dataset_df)

    print(
        f"  {tag}Eval complete — total: {_fmt_elapsed(t0)}, {len(scores)} metrics, {len(per_case)} cases, "
        f"~{token_usage['input_tokens']:,} in / ~{token_usage['output_tokens']:,} out tokens (est.)",
        flush=True,
    )
    return EvalResult(scores=scores, per_case=per_case, token_usage=token_usage)


def run_batch_eval_averaged(
    engine_id: str,
    eval_cases: list[dict],
    num_runs: int = 1,
    metrics: list | None = None,
    agent_name: str = "",
    model: str = "",
    retry_failed: bool = True,
) -> EvalResult:
    """Run batch eval N times and return averaged scores with std dev."""
    if num_runs <= 1:
        return run_batch_eval(
            engine_id,
            eval_cases,
            metrics=metrics,
            agent_name=agent_name,
            model=model,
            retry_failed=retry_failed,
        )

    tag = f"[{agent_name}] " if agent_name else ""
    all_results: list[EvalResult] = []

    for i in range(num_runs):
        print(f"  {tag}Run {i + 1}/{num_runs}...", flush=True)
        result = run_batch_eval(
            engine_id,
            eval_cases,
            metrics=metrics,
            agent_name=agent_name,
            model=model,
            retry_failed=retry_failed,
        )
        if result.scores:
            all_results.append(result)
            avg = sum(result.scores.values()) / max(len(result.scores), 1)
            print(f"  {tag}Run {i + 1}/{num_runs} avg: {avg:.3f}", flush=True)
        else:
            print(
                f"  {tag}Run {i + 1}/{num_runs} returned no scores (skipping from average)",
                flush=True,
            )

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

    agg_tokens: dict[str, int | bool] = {"input_tokens": 0, "output_tokens": 0, "is_estimate": True}
    for r in all_results:
        if r.token_usage:
            agg_tokens["input_tokens"] += r.token_usage.get("input_tokens", 0)
            agg_tokens["output_tokens"] += r.token_usage.get("output_tokens", 0)

    overall = sum(avg_scores.values()) / max(len(avg_scores), 1)
    successful = len(all_results)
    skipped = num_runs - successful
    skip_note = f" ({skipped} skipped — timeout/failure)" if skipped else ""
    print(
        f"  {tag}Averaged {successful}/{num_runs} runs — overall: {overall:.3f}{skip_note}",
        flush=True,
    )

    return EvalResult(
        scores=avg_scores,
        per_case=avg_per_case,
        scores_std=std_scores,
        num_runs=num_runs,
        token_usage=agg_tokens,
    )


def save_eval_results(
    agent_name: str,
    scores: dict[str, float],
    phase: str = "baseline",
    output_dir: str | None = None,
) -> str:
    """Save eval results to JSON. Returns the file path."""
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    out_dir = Path(output_dir or "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"eval_{agent_name}_{phase}_{timestamp}.json"
    path = out_dir / filename

    data = {
        "agent": agent_name,
        "phase": phase,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "scores": scores,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return str(path)
