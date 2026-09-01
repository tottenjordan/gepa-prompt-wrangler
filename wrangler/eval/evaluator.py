"""Batch evaluation — runs inference + evaluation against deployed agents on GEAP."""

import contextlib
import functools
import json
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

# Attempts per eval case, initial inference included. GEAP drops ~3 of every 4
# requests on a booting worker (silent-failures.md #5), so at p~0.25:
#
#     attempts   1     2     3     6     10    12
#     coverage   25%   44%   58%   82%   94%   97%
#
# Six was chosen to match `wrangler/tools/traffic.py`, whose job is only to emit
# some traces. Eval has a stricter requirement: *both* sides must score the same
# cases or the delta compares different subsets. The 2026-08-22 sweep landed
# 30/64 and 57/64 on one arm, leaving 23 paired -- and 82% per side implies only
# ~67% expected overlap, which is the arithmetic behind that.
#
# Ten pushes per-side coverage to ~94% and expected overlap to ~88%. Attempts are
# spent only on cases still failing and each pass shrinks, so the cost falls on
# the runs that need it. Raise further only with a measurement: past ~12 the
# curve flattens and the wall-clock does not.
_INFERENCE_MAX_ATTEMPTS = 10

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
# Retries per case inside `client.evals.run_inference`.
#
# The SDK default is lower; this override exists because an engine that answers
# HTTP 200 with no inference loses the case outright. The number matters more
# than it looks: single-attempt reach on a healthy engine is 97-100%, but a
# degraded one measured 25%, and coverage is 1-(1-reach)^attempts. At 25% reach,
# 7 attempts gives 87% and 16 gives 99% -- which is the difference between a
# noise floor that measures dropout and one that measures noise.
#
# On a healthy engine the first attempt succeeds, so raising this costs nothing
# in the good case. It is not a substitute for the health gate: it absorbs the
# dropout without fixing it, so `health_gate.required` and the recorded coverage
# fields are what keep the sickness visible.
# See docs/analysis/2026-09-01-health-gate-vs-eval-coverage.md
EVAL_MAX_RETRIES = 16

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
    # How many cases each metric actually scored. Defaults empty so existing
    # constructions are unaffected. See _coverage_warning for why it matters.
    coverage: dict[str, int] = field(default_factory=dict)
    # submitted / scored / source, where source names the extraction path that
    # produced `per_case`. Read this before treating a run as evidence that the
    # scoring-loss recovery works: "sdk" means the recovery never fired.
    scoring: dict[str, object] = field(default_factory=dict)


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


def _extract_per_case_scores(
    evaluation_run, expected: int | None = None
) -> tuple[list[dict[str, float]], str]:
    """Extract per-case metric scores from evaluation run results.

    Primary path: evaluation_item_results.eval_case_results[i]
                  .response_candidate_results[0].metric_results

    The SDK populates evaluation_item_results via
    _convert_evaluation_run_results(), which returns None when
    evaluation_run_results.evaluation_set is missing. When that
    happens, fall back to fetching evaluation items directly via
    the API client.

    Returns ``(rows, source)`` where source is ``"sdk"`` or
    ``"gcs_recovery"``. The caller records it in the saved artifact: the
    2026-08-23 control run could only be read back through the *shape* of its
    rows, because the one statement of which path ran was a print to a log
    that no longer existed by the time anyone looked.
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
                return per_case, "sdk"
            return per_case, "gcs_recovery"

        case_results = getattr(item_results, "eval_case_results", None)
        if not case_results:
            print("  Warning: eval_case_results is empty")
            return per_case, "sdk"

        for position, case_result in enumerate(case_results):
            # eval_case_index is the case's position in the eval set and is what
            # makes before/after pairable. Fall back to enumeration order, which
            # is the same ordering the SDK returns results in.
            idx = getattr(case_result, "eval_case_index", None)
            case_scores: dict[str, float] = {CASE_INDEX_KEY: position if idx is None else idx}
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

    # A case can infer perfectly and still vanish here. Its result file carries
    # one per-metric error, `extra='forbid'` makes the whole file unparseable,
    # and the SDK's conversion drops the item -- so `eval_case_results` is
    # simply short and nothing above notices. Measured 2026-08-23: 50 rows
    # submitted / 41 scored, and 44 / 39.
    #
    # The GCS recovery added for this cascade lives on the API-fallback path,
    # which only runs when `evaluation_item_results is None`. When it is present
    # but incomplete, fall back anyway and keep whichever is more complete.
    if expected is not None and len(per_case) < expected:
        print(
            f"  Scoring dropped cases: {len(per_case)}/{expected} scored. Recovering "
            f"from raw GCS (see docs/notes/silent-failures.md #7).",
            flush=True,
        )
        recovered = _extract_per_case_via_api(evaluation_run)
        if len(recovered) > len(per_case):
            print(f"  Recovered {len(recovered)}/{expected} cases from GCS", flush=True)
            return recovered, "gcs_recovery"
        print(
            f"  GCS recovery returned {len(recovered)}; keeping {len(per_case)}. "
            f"{expected - len(per_case)} case(s) remain unscored.",
            flush=True,
        )
    return per_case, "sdk"


def _scores_from_raw_result(raw: dict) -> tuple[dict[str, float], list[str]]:
    """Pull metric scores out of a raw result payload, skipping errored metrics.

    Returns ``(scores, errored_metric_names)``.

    This exists because the SDK's own loader cannot. ``types.CandidateResult``
    has no ``error`` field and inherits ``extra='forbid'`` from
    ``google.genai._common.BaseModel``, so a single per-metric error makes the
    *entire* result file fail validation;
    ``_evals_common._convert_gcs_to_evaluation_item_result`` catches that,
    logs, and returns an empty ``EvaluationItemResult()``. The caller receives
    a well-formed object containing nothing and has no way to tell the
    difference between "this case scored nothing" and "this file would not
    parse", so the case silently contributes to no metric at all.

    Reading the JSON ourselves makes each metric independent, which is the
    whole point: the same boundary has now destroyed scores three separate ways
    (silent-failures #3 unsupported metric version, #5 empty agent stream, #7
    autorater tool call). Fixing the boundary retires the class rather than the
    latest instance.

    A ``None`` score is dropped rather than coerced — see #6, where coercing a
    missing score to 0.0 had GEPA optimize against a criterion nailed to zero.
    """
    scores: dict[str, float] = {}
    errored: list[str] = []
    for candidate in raw.get("candidateResults") or []:
        if not isinstance(candidate, dict):
            continue
        metric = candidate.get("metric") or ""
        short = metric.rsplit("/", 1)[-1] if metric else ""
        if "error" in candidate:
            if short:
                errored.append(short)
            continue
        score = candidate.get("score")
        if short and score is not None:
            scores[_alias_tool_use_key(short)] = float(score)
    return scores, errored


# Reserved key in a per_case row: which eval case it is, by position in the
# eval set. NOT a metric — every consumer that walks a row's keys must strip it
# with case_metrics() first.
#
# Without it a per_case row is a bare {metric: score} dict that cannot say which
# case it came from, so before/after cannot be paired. The 2026-08-22 sweep
# scored 30 cases before and 57 after on one arm (60/36 on another), meaning
# every reported delta compared two different subsets of the 64. That unpairing
# is the largest single contributor to the +0.039 noise floor that sweep
# measured.
CASE_INDEX_KEY = "case_index"


def case_metrics(row: dict) -> dict[str, float]:
    """A per_case row's metric scores, with the reserved case key removed."""
    return {k: v for k, v in row.items() if k != CASE_INDEX_KEY}


def pair_per_case(
    before: list[dict], after: list[dict]
) -> dict[int, tuple[dict[str, float], dict[str, float]]]:
    """Match before/after rows by case index. Only cases present on both sides.

    Returns ``{case_index: (before_metrics, after_metrics)}``.

    Rows without an index are skipped rather than matched by position. Positional
    matching would pair case 0 of one subset with case 0 of a *different* subset
    and present the result as a like-for-like comparison, which is precisely the
    error this function exists to prevent.
    """
    b = {r[CASE_INDEX_KEY]: case_metrics(r) for r in before if CASE_INDEX_KEY in r}
    a = {r[CASE_INDEX_KEY]: case_metrics(r) for r in after if CASE_INDEX_KEY in r}
    return {i: (b[i], a[i]) for i in sorted(b.keys() & a.keys())}


def paired_deltas(before: list[dict], after: list[dict]) -> dict:
    """Per-metric deltas over only the cases that both sides scored.

    This is the comparison worth reporting: same cases, same agent, one prompt
    change between them. Also returns how many cases each side contributed that
    the other did not, because a paired delta over 12 of 64 cases is a different
    claim from one over 60.
    """
    paired = pair_per_case(before, after)
    deltas: dict[str, float] = {}
    if paired:
        metrics = {m for b, a in paired.values() for m in (b.keys() | a.keys())}
        for m in metrics:
            pairs = [(b[m], a[m]) for b, a in paired.values() if m in b and m in a]
            if pairs:
                deltas[m] = sum(y - x for x, y in pairs) / len(pairs)
    b_idx = {r[CASE_INDEX_KEY] for r in before if CASE_INDEX_KEY in r}
    a_idx = {r[CASE_INDEX_KEY] for r in after if CASE_INDEX_KEY in r}
    return {
        "n_paired": len(paired),
        "deltas": deltas,
        "dropped_before": len(b_idx - a_idx),
        "dropped_after": len(a_idx - b_idx),
    }


def _metric_coverage(per_case: list[dict[str, float]]) -> dict[str, int]:
    """Count how many cases produced a score for each metric."""
    coverage: dict[str, int] = {}
    for case_scores in per_case:
        for metric in case_metrics(case_scores):
            coverage[metric] = coverage.get(metric, 0) + 1
    return coverage


def _coverage_warning(coverage: dict[str, int], num_cases: int) -> list[str]:
    """Report metrics that scored fewer cases than the run contained.

    The aggregate numbers in ``EvalResult.scores`` come from the *server-side*
    summary metrics, which quietly exclude cases whose metric errored. A mean
    over four cases is formatted exactly like a mean over five, so without this
    an autorater flake is indistinguishable from a real quality change — and a
    before/after comparison measures the dropout rather than the prompt.

    Pure and list-returning so the wording is testable without an eval run,
    matching ``wrangler/tools/traffic.py:summarize_run``.
    """
    if not num_cases:
        return []
    short = {m: n for m, n in coverage.items() if n < num_cases}
    if not short:
        return []
    lines = [
        "  ** UNEVEN METRIC COVERAGE — means are over different denominators",
    ]
    lines += [f"     {metric:<28} {n}/{num_cases} cases" for metric, n in sorted(short.items())]
    lines.append(
        "     Autorater errors dropped cases; see docs/notes/silent-failures.md #7. "
        "Do not compare these means against a run with different coverage."
    )
    return lines


def _read_raw_result(client, gcs_uri: str) -> dict:
    """Read an evaluation result file from GCS as plain JSON.

    Reuses the SDK's own GCS reader rather than taking a direct dependency on
    google-cloud-storage, so credentials and path parsing behave identically to
    the code path this is standing in for. Kept as a separate function so tests
    can substitute it without patching the SDK internals.
    """
    from vertexai._genai import _gcs_utils

    gcs = _gcs_utils.GcsUtils(api_client=client._api_client)
    return json.loads(gcs.read_file_contents(gcs_uri))


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

        for position, item_name in enumerate(eval_set.evaluation_items):
            # The eval set lists items in case order, so position is the index.
            case_scores: dict[str, float] = {CASE_INDEX_KEY: position}
            errored: list[str] = []
            # One unreadable item must not drop the scores for every other case;
            # it just contributes an empty dict.
            with contextlib.suppress(Exception):
                item = client.evals.get_evaluation_item(name=item_name)
                response = getattr(item, "evaluation_response", None)
                candidates = getattr(response, "candidate_results", None) if response else None
                for candidate in candidates or []:
                    metric = getattr(candidate, "metric", "")
                    score = getattr(candidate, "score", None)
                    if metric and score is not None:
                        short = metric.split("/")[-1] if "/" in metric else metric
                        case_scores[short] = float(score)

                # An empty response with a gcs_uri is the signature of defect #7:
                # the SDK could not parse the file and handed back a blank
                # EvaluationItemResult instead of raising, so `response` looks
                # legitimately empty. The file itself is still readable and
                # still holds every metric that did score. Only reached when the
                # SDK gave us nothing, so the happy path costs no extra request.
                # Test for *metrics*, not for an empty dict: the row already
                # holds its case index, so `not case_scores` would never be
                # true and this fallback would be dead code.
                if not case_metrics(case_scores) and getattr(item, "gcs_uri", None):
                    with contextlib.suppress(Exception):
                        recovered, errored = _scores_from_raw_result(
                            _read_raw_result(client, item.gcs_uri)
                        )
                        # Merge rather than replace, so the case keeps its index.
                        case_scores.update(recovered)
                        if errored:
                            print(
                                f"  Recovered {len(recovered)} metric(s) from GCS for a case "
                                f"the SDK dropped; these metrics errored: {', '.join(errored)}"
                            )
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
        def _patched_retry(*args, max_retries=EVAL_MAX_RETRIES, **kwargs):
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


def _is_failed_response(response) -> bool:
    """Is this inference row a failure dressed up as a response?

    The obvious cases are empty/NaN, and a dict carrying an "error" key. The
    non-obvious one, and the reason this is a shared helper: when GEAP returns
    an empty event stream the SDK cannot parse it and stores its complaint as
    the response *text* --

        '{"error": "Failed to parse agent run response [] to agent data: ...'

    -- a perfectly non-empty string that is not a dict, so a dict-only check
    waves it through. It then reaches scoring with no agent_data, the custom
    tool_use_quality metric fails to render its template, and that single
    per-metric error makes the whole EvaluationItemResult unparseable
    (`extra='forbid'`), dropping the case from *every* metric. One empty
    stream silently costs a whole case. See docs/notes/silent-failures.md.
    """
    if response is None or (not isinstance(response, dict) and pd.isna(response)):
        return True
    if isinstance(response, dict):
        return "error" in response
    if isinstance(response, str):
        text = response.strip()
        if not text:
            return True
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                return False
            return isinstance(parsed, dict) and "error" in parsed
    return False


def _assert_scorable(result_df: pd.DataFrame, tag: str = "") -> None:
    """Refuse to submit an inference set with nothing left to score.

    `create_evaluation_run` answers `500 INTERNAL` when every row is a failed
    response — an error that names no cause and reads like a service outage. It
    is not: it is the cold-worker defect having eaten the whole run
    (silent-failures.md #5), which is diagnosable here and nowhere downstream.

    Raises rather than returning a sentinel: there is no useful eval to
    continue with, and a caller that swallowed this would produce a score over
    zero cases, which is the exact class of failure this file exists to stop.
    """
    if result_df.empty or result_df["response"].apply(_is_failed_response).all():
        total = len(result_df)
        raise RuntimeError(
            f"{tag}Refusing to submit an evaluation run: 0 of {total} cases produced a "
            f"usable response, so there is nothing to score. Submitting anyway returns "
            f"a bare '500 INTERNAL' from create_evaluation_run. The cause is almost "
            f"certainly GEAP dropping requests on booting workers — see "
            f"docs/notes/silent-failures.md #5. Re-run when the engine is warm, or "
            f"raise _INFERENCE_MAX_ATTEMPTS."
        )


def _retry_failed_cases(
    client: Client,
    agent_resource: str,
    eval_df: pd.DataFrame,
    inference_result: types.EvaluationDataset,
    model: str,
    tag: str = "",
) -> types.EvaluationDataset:
    """Detect failed inference cases and re-run them until they land or the
    attempt budget runs out.

    One retry is not enough and never was. GEAP drops roughly three of every
    four requests on a booting worker (silent-failures.md #5, measured
    unfixable from the client across session identity, pacing and warm-up), so
    a single extra pass recovers ~25% of failures in expectation. The PR #11
    verification run logged ``Recovered 0/4`` then ``Recovered 0/5`` and then
    died on a 500 — which is the expected outcome of one retry, not bad luck.

    This is the same conclusion ``wrangler/tools/traffic.py`` reached: you
    cannot dodge the failure, so spend attempts on it. Each pass retries only
    the cases still failing, so the work shrinks as cases land.
    """
    result_df = inference_result.eval_dataset_df
    records = result_df.to_dict("records")

    def _still_failing() -> list[int]:
        return [i for i, row in enumerate(records) if _is_failed_response(row.get("response"))]

    failed_indices = _still_failing()
    if not failed_indices:
        return inference_result

    n_failed = len(failed_indices)
    print(
        f"  {tag}Detected {n_failed}/{len(records)} failed cases — waiting 30s for rate limit cooldown...",
        flush=True,
    )
    # Cooldown once, not once per pass: the first failure may be rate limiting,
    # but by the second pass we are just paying for the cold-worker lottery.
    time.sleep(30)

    recovered = 0
    attempts = 0
    for attempt in range(1, _INFERENCE_MAX_ATTEMPTS):
        pending = _still_failing()
        if not pending:
            break
        attempts = attempt

        failed_eval_df = eval_df.iloc[pending].reset_index(drop=True)
        retry_result = _run_batched_inference(
            client,
            agent_resource,
            failed_eval_df,
            batch_size=2,
            delay=20.0,
            max_workers=2,
            tag=f"{tag}retry {attempt}/{_INFERENCE_MAX_ATTEMPTS - 1} ",
        )

        # Splice recovered rows in as records rather than assigning a Series
        # into `result_df.iloc[i]`. These frames carry ADK objects
        # (`SessionInput` and friends) in object columns, and pandas' row
        # setitem tries to broadcast the value -- `TypeError: object of type
        # 'SessionInput' has no len()`. That crash was unreachable until the
        # detector learned to spot an error payload stored as a string,
        # because nothing was ever recovered.
        retry_records = retry_result.eval_dataset_df.to_dict("records")
        for retry_idx, original_idx in enumerate(pending):
            if retry_idx < len(retry_records):
                retry_row = retry_records[retry_idx]
                if not _is_failed_response(retry_row.get("response")):
                    # Merge so a column the retry frame lacks keeps its
                    # original value instead of becoming NaN.
                    records[original_idx] = {**records[original_idx], **retry_row}
                    recovered += 1

    still_failed = len(_still_failing())
    print(
        f"  {tag}Recovered {recovered}/{n_failed} failed cases "
        f"over {attempts} retry attempt(s); {still_failed} still failing",
        flush=True,
    )
    merged_df = pd.DataFrame(records, columns=result_df.columns)
    return types.EvaluationDataset(eval_dataset_df=merged_df)


def _drop_unscorable_rows(result_df: "pd.DataFrame", tag: str = "") -> "pd.DataFrame":
    """Remove rows the eval service cannot score, and say how many.

    Rows with NaN/float in ``response`` or ``agent_data`` make the SDK raise a
    ValidationError. A row whose response is an *error payload* goes too: left
    in, it takes every other metric on that case down with it via the
    extra='forbid' cascade (see ``_is_failed_response``).

    Shared by ``run_batch_eval`` and ``capture_inference`` so a capture holds
    exactly the rows a live run would have scored — otherwise the two paths
    would score different sets and could not be compared.
    """

    def _is_invalid(val):
        return val is None or (isinstance(val, float) and pd.isna(val)) or val == ""

    invalid_mask = result_df["response"].apply(_is_failed_response)
    if "agent_data" in result_df.columns:
        invalid_mask = invalid_mask | result_df["agent_data"].apply(_is_invalid)
    n_invalid = invalid_mask.sum()
    if n_invalid > 0:
        print(
            f"  {tag}Dropped {n_invalid}/{len(result_df)} rows with invalid agent_data", flush=True
        )
        return result_df[~invalid_mask].reset_index(drop=True)
    return result_df


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

    inference_result = types.EvaluationDataset(
        eval_dataset_df=_drop_unscorable_rows(inference_result.eval_dataset_df, tag)
    )
    _assert_scorable(inference_result.eval_dataset_df, tag)

    return _score_dataset(
        client,
        inference_result,
        metrics=metrics,
        agent_resource=agent_resource,
        tag=tag,
        t0=t0,
    )


def _score_dataset(
    client: "Client",
    inference_result: "types.EvaluationDataset",
    metrics: list,
    agent_resource: str | None = None,
    tag: str = "",
    t0: float | None = None,
) -> EvalResult:
    """Score an already-inferred dataset. Makes no agent calls.

    This is the second half of ``run_batch_eval``, split out so the same
    responses can be scored more than once. Every judge question — how much a
    judge disagrees with itself, whether a different judge model shifts the
    result, what a metric prompt change does — is a comparison that is only
    valid if the responses underneath are identical, and re-running inference
    guarantees they are not. It is also the cheap half: inference against an
    engine that drops a third of its requests is what costs.
    """
    if t0 is None:
        t0 = time.time()

    print(f"  {tag}Scoring: creating evaluation run ({len(metrics)} metrics)...", flush=True)
    eval_t0 = time.time()
    evaluation_run = client.evals.create_evaluation_run(
        dataset=inference_result,
        # Optional: a capture carries its own responses, so scoring one does not
        # need the engine that produced them — and must not require it to still
        # exist. Engine ids are not pinned anywhere in this repo.
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

    # `expected` is the number of rows actually submitted for scoring, so a
    # case dropped between submission and result is detected rather than
    # silently reducing the denominator.
    submitted = len(inference_result.eval_dataset_df)
    per_case, scoring_source = _extract_per_case_scores(evaluation_run, expected=submitted)
    for case_scores in per_case:
        # Same single-tool-use-metric clobber assumption as above.
        if _TOOL_USE_METRIC_NAME in case_scores:
            case_scores[_alias_tool_use_key(_TOOL_USE_METRIC_NAME)] = case_scores.pop(
                _TOOL_USE_METRIC_NAME
            )
    token_usage = _estimate_token_usage(inference_result.eval_dataset_df)

    coverage = _metric_coverage(per_case)

    print(
        f"  {tag}Eval complete — total: {_fmt_elapsed(t0)}, {len(scores)} metrics, {len(per_case)} cases, "
        f"~{token_usage['input_tokens']:,} in / ~{token_usage['output_tokens']:,} out tokens (est.)",
        flush=True,
    )
    for line in _coverage_warning(coverage, len(per_case)):
        print(line, flush=True)
    return EvalResult(
        scores=scores,
        per_case=per_case,
        token_usage=token_usage,
        coverage=coverage,
        scoring={"submitted": submitted, "scored": len(per_case), "source": scoring_source},
    )


def average_per_case(runs: list[list[dict]]) -> list[dict[str, float]]:
    """Average per-case rows across runs, matching cases by index.

    This used to match by *list position*: ``r.per_case[k]`` for each run. Runs
    drop different cases, so position k is a different case in each one — a run
    scoring cases [0, 2, 5] averaged against a run scoring [0, 1, 5] merged
    case 2's score with case 1's. It also averaged ``case_index`` itself as
    though it were a metric, yielding fractional indices that nothing
    downstream could pair on.

    The consequence is not cosmetic: the ~0.034 noise floor measured at
    ``num_runs: 3`` — the figure CLAUDE.md tells every sweep to clear — was
    computed through it, so that number has to be re-measured.

    A case only one run scored is kept with that run's value rather than
    dropped. Metrics are averaged independently, so a metric missing from one
    run does not drag the others.
    """
    by_case: dict[int, dict[str, list[float]]] = {}
    unindexed = 0
    for run in runs:
        for row in run:
            idx = row.get(CASE_INDEX_KEY)
            if idx is None:
                # Unpairable by construction. Dropped rather than guessed at,
                # and counted rather than dropped quietly -- the guess is what
                # this function is being fixed for.
                unindexed += 1
                continue
            bucket = by_case.setdefault(int(idx), {})
            for metric, value in case_metrics(row).items():
                bucket.setdefault(metric, []).append(value)

    if unindexed:
        print(
            f"  Warning: {unindexed} per-case row(s) had no {CASE_INDEX_KEY} and could not be "
            f"paired across runs; excluded from the average.",
            flush=True,
        )

    return [
        {CASE_INDEX_KEY: idx, **{m: statistics.mean(v) for m, v in sorted(metrics.items())}}
        for idx, metrics in sorted(by_case.items())
    ]


CAPTURE_DIR = "outputs/captures"


def save_capture(
    frame: "pd.DataFrame",
    out_dir: str = CAPTURE_DIR,
    label: str = "capture",
    engine_id: str = "",
    model: str = "",
    submitted: int | None = None,
) -> str:
    """Persist an inference frame plus a human-readable sidecar. Returns the path.

    Pickle for the frame: it holds SDK pydantic objects in ``session_inputs``
    and ``agent_data``, which no columnar format round-trips. That makes a
    capture **scratch, not archive** — an SDK bump can render an old one
    unloadable.

    Which is why the sidecar exists and is JSON. It records what the capture
    *was* — engine, model, row count, and how many rows the inference dropout
    cost — so a capture whose frame no longer loads is still identifiable
    rather than an anonymous file.
    """
    import pickle
    from datetime import UTC, datetime
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    path = out / f"{label}_{stamp}.pkl"

    with path.open("wb") as f:
        pickle.dump(frame, f)

    rows = len(frame)
    meta = {
        "label": label,
        "engine_id": engine_id,
        "model": model,
        "rows": rows,
        "submitted": submitted if submitted is not None else rows,
        "dropped": (submitted - rows) if submitted is not None else 0,
        "columns": list(frame.columns),
        "captured_at": datetime.now(tz=UTC).isoformat(),
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
    return str(path)


def load_capture(path: str) -> "pd.DataFrame":
    """Load a captured inference frame."""
    import pickle
    from pathlib import Path

    with Path(path).open("rb") as f:
        return pickle.load(f)  # noqa: S301 -- our own file, written by save_capture


def capture_inference(
    engine_id: str,
    eval_cases: list[dict],
    label: str = "capture",
    model: str = "",
    agent_name: str = "",
    retry_failed: bool = True,
    out_dir: str = CAPTURE_DIR,
) -> str:
    """Run inference only, and save the responses. Returns the capture path.

    The expensive half on its own. Everything downstream — every judge, every
    metric variant, every repeat — reads this file and never touches the engine
    again, which also puts it out of reach of the empty-stream defect
    (docs/notes/silent-failures.md #5).
    """
    tag = f"[{agent_name}] " if agent_name else ""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )
    client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)
    agent_resource = _resolve_resource_name(engine_id)

    eval_df = _build_eval_dataset(eval_cases)
    batch_size, delay, max_workers = get_batch_config(model)

    t0 = time.time()
    inference_result = _run_batched_inference(
        client, agent_resource, eval_df, batch_size, delay, max_workers, tag
    )
    if retry_failed:
        inference_result = _retry_failed_cases(
            client, agent_resource, eval_df, inference_result, model, tag
        )
    print(f"  {tag}Inference complete ({_fmt_elapsed(t0)})", flush=True)

    clean_df = _drop_unscorable_rows(inference_result.eval_dataset_df, tag)
    path = save_capture(
        clean_df,
        out_dir=out_dir,
        label=label,
        engine_id=engine_id,
        model=model,
        submitted=len(eval_cases),
    )
    print(f"  {tag}Captured {len(clean_df)}/{len(eval_cases)} cases -> {path}", flush=True)
    return path


def score_captured(
    capture_path: str,
    metrics: list | None = None,
    agent_name: str = "",
    expected: int | None = None,
) -> EvalResult:
    """Score a capture. Makes no agent calls."""
    tag = f"[{agent_name}] " if agent_name else ""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )
    client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)
    frame = load_capture(capture_path)
    dataset = types.EvaluationDataset(eval_dataset_df=frame)
    return _score_dataset(
        client,
        dataset,
        metrics=metrics if metrics is not None else DEFAULT_METRICS,
        tag=tag,
    )


def summarize_repeats(results: list[EvalResult]) -> dict:
    """Mean, spread and range per metric across repeated scorings.

    The spread is the point. Scoring the same responses five times and
    reporting only the mean hides exactly the quantity being measured —
    how much the judge disagrees with itself.
    """
    scored = [r for r in results if r.scores]
    if not scored:
        return {"n": 0, "mean": {}, "std": {}, "min": {}, "max": {}}

    keys: set[str] = set()
    for r in scored:
        keys.update(r.scores)

    mean, std, lo, hi = {}, {}, {}, {}
    for key in sorted(keys):
        vals = [r.scores[key] for r in scored if key in r.scores]
        mean[key] = statistics.mean(vals)
        std[key] = statistics.stdev(vals) if len(vals) > 1 else 0.0
        lo[key], hi[key] = min(vals), max(vals)
    return {"n": len(scored), "mean": mean, "std": std, "min": lo, "max": hi}


def score_captured_repeated(
    capture_path: str,
    repeat: int = 1,
    metrics: list | None = None,
    agent_name: str = "",
) -> dict:
    """Score one capture ``repeat`` times and summarize the spread."""
    results = []
    for i in range(repeat):
        print(f"  Scoring pass {i + 1}/{repeat}...", flush=True)
        results.append(score_captured(capture_path, metrics=metrics, agent_name=agent_name))
    summary = summarize_repeats(results)
    summary["results"] = results
    return summary


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

    avg_per_case = average_per_case([r.per_case for r in all_results if r.per_case])

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
    # Averaging N runs does not repair a metric that errored in all of them —
    # it just hides the gap behind one more layer of arithmetic.
    avg_coverage = _metric_coverage(avg_per_case)
    for line in _coverage_warning(avg_coverage, len(avg_per_case)):
        print(line, flush=True)

    return EvalResult(
        scores=avg_scores,
        per_case=avg_per_case,
        scores_std=std_scores,
        num_runs=num_runs,
        token_usage=agg_tokens,
        coverage=avg_coverage,
    )


def save_eval_results(
    agent_name: str,
    scores: dict[str, float],
    phase: str = "baseline",
    output_dir: str | None = None,
    per_case: list[dict] | None = None,
    coverage: dict[str, int] | None = None,
    scoring: dict[str, object] | None = None,
) -> str:
    """Save eval results to JSON. Returns the file path.

    ``per_case`` is optional but matters: without it a standalone eval keeps
    only aggregate means, and two such runs can be compared solely by
    subtracting numbers computed over different case subsets. That is how the
    control arm on 2026-08-22 produced an apparent +0.180 spread on an
    unchanged prompt with no way to tell how much was sampling. With the rows
    kept, `paired_deltas()` can compare the cases both runs actually scored.

    ``coverage`` and ``scoring`` are the same argument one level up: without
    them the file says how each case scored but not how many cases were
    submitted, how many each metric reached, or which extraction path
    produced the rows — so a run cannot be read back as evidence about the
    scoring stage itself.
    """
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
        "per_case": per_case or [],
        "coverage": coverage or {},
        "scoring": scoring or {},
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return str(path)
