"""Throwaway diagnostic: settle the tool_use_quality floor.

Runs inference against the live opus48 engine on a few tool-requiring prompts,
inspects whether the agent's tool calls are CAPTURED in the inference dataframe
(agent_data / intermediate events), then scores TOOL_USE_QUALITY on those rows.

Verdict branches:
  A) trajectory captured + correct tools called + low score  -> agent behavior
  B) trajectory captured + NO tool calls                     -> prompt issue
  C) trajectory NOT captured                                 -> inference->eval handoff bug
"""

import json
import os
import sys
import time

import pandas as pd
import vertexai
from vertexai import Client, types

from wrangler.core.config import (
    GCP_PROJECT_ID,
    GCP_REGION,
    GCP_STAGING_BUCKET,
    disable_pyopenssl,
)

# --- Diagnostic flag ---------------------------------------------------------
# Pass `--fixed` to score with the NEW explicit-rubric tool_use metric from
# wrangler.eval.evaluator (built_tool_use_metric) instead of the bare predefined
# TOOL_USE_QUALITY that auto-generates inverted rubrics. Default = bare (before).
USE_FIXED = "--fixed" in sys.argv

disable_pyopenssl()  # pyopenssl 26.x context-reuse guard breaks GCS upload

# Whichever engine you are diagnosing today — `ENGINE_ID=... uv run python
# scripts/diagnose_tooluse.py`. Not hardcoded: the id this was first written against
# is long gone, and a stale default would point the diagnostic at someone else's agent.
ENGINE_ID = os.environ.get("ENGINE_ID", "")
GCS_EVAL_DEST = f"gs://{GCP_STAGING_BUCKET}/eval-results"

# Representative tool-requiring cases (single-tool, should be trivially toolable).
CASES = [
    {
        "prompt": "Show expenses for user EMP001",
        "expected_tool": "wrangler_expense_mcp_get_user_expenses",
    },
    {
        "prompt": "Find flights from SFO to Denver",
        "expected_tool": "wrangler_search_mcp_search_flights",
    },
    {
        "prompt": "What is the corporate meal expense limit?",
        "expected_tool": "wrangler_expense_mcp_check_expense_policy",
    },
]

TOOL_HINTS = [
    "function_call",
    "functionCall",
    "function_response",
    "functionResponse",
    "tool_use",
    "search_flights",
    "get_user_expenses",
    "check_expense_policy",
    "wrangler_",
    "tool_uses",
    "intermediate",
]


def _resource(engine_id: str) -> str:
    return f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"


def _scan_for_tools(val) -> list[str]:
    s = json.dumps(val, default=str) if not isinstance(val, str) else val
    return [h for h in TOOL_HINTS if h in s]


def main() -> None:
    if not ENGINE_ID:
        sys.exit("Set ENGINE_ID to the Agent Engine you want to diagnose.")
    print(f"PROJECT={GCP_PROJECT_ID} REGION={GCP_REGION} ENGINE={ENGINE_ID}", flush=True)
    vertexai.init(
        project=GCP_PROJECT_ID, location=GCP_REGION, staging_bucket=f"gs://{GCP_STAGING_BUCKET}"
    )
    client = Client(project=GCP_PROJECT_ID, location=GCP_REGION)
    agent = _resource(ENGINE_ID)

    session_inputs = types.evals.SessionInput(user_id="tooluse-diag", state={})
    df = pd.DataFrame(
        [
            {
                "prompt": c["prompt"],
                "session_inputs": session_inputs,
                "expected_tool": c["expected_tool"],
                "reference": "",
            }
            for c in CASES
        ]
    )

    print(f"\n=== run_inference on {len(df)} cases ===", flush=True)
    t0 = time.time()
    res = client.evals.run_inference(agent=agent, src=df)
    rdf = res.eval_dataset_df
    print(f"inference done in {int(time.time() - t0)}s", flush=True)
    print("columns:", list(rdf.columns), flush=True)

    print("\n=== per-row trajectory capture ===", flush=True)
    for i, row in rdf.iterrows():
        print(
            f"\n--- case {i}: {CASES[i]['prompt']!r} (expect {CASES[i]['expected_tool']}) ---",
            flush=True,
        )
        resp = row.get("response")
        rtext = resp if isinstance(resp, str) else json.dumps(resp, default=str)
        print(
            "  response:",
            (rtext[:300] + "...") if rtext and len(rtext) > 300 else rtext,
            flush=True,
        )
        for col in (
            "agent_data",
            "intermediate_events",
            "intermediate_data",
            "predicted_trajectory",
        ):
            if col in rdf.columns:
                hits = _scan_for_tools(row.get(col))
                blob = json.dumps(row.get(col), default=str)
                print(f"  [{col}] len={len(blob)} tool_hits={hits}", flush=True)
        whole = json.dumps(row.to_dict(), default=str)
        print("  WHOLE-ROW tool_hits:", _scan_for_tools(whole), flush=True)

    # Clean invalid rows (mirror evaluator.py)
    def _bad(v):
        return v is None or (isinstance(v, float) and pd.isna(v)) or v == ""

    mask = rdf["response"].apply(_bad)
    if "agent_data" in rdf.columns:
        mask = mask | rdf["agent_data"].apply(_bad)
    clean = rdf[~mask].reset_index(drop=True)
    print(f"\nclean rows for scoring: {len(clean)}/{len(rdf)}", flush=True)
    if len(clean) == 0:
        print("No scorable rows — aborting eval.", flush=True)
        return

    if USE_FIXED:
        from wrangler.eval.evaluator import _tool_use_metric

        metric = _tool_use_metric()
        print("\n=== create_evaluation_run FIXED tool_use (explicit LLM judge) ===", flush=True)
    else:
        metric = types.RubricMetric.TOOL_USE_QUALITY
        print("\n=== create_evaluation_run TOOL_USE_QUALITY (bare predefined) ===", flush=True)
    run = client.evals.create_evaluation_run(
        dataset=types.EvaluationDataset(eval_dataset_df=clean),
        agent=agent,
        metrics=[metric],
        dest=GCS_EVAL_DEST,
        labels={"solution": "promp-wrangler"},
    )
    print("eval run:", run.name, flush=True)
    et0 = time.time()
    state = ""
    while time.time() - et0 < 900:
        run = client.evals.get_evaluation_run(name=run.name)
        state = str(getattr(run, "state", ""))
        if any(s in state for s in ("SUCCEEDED", "FAILED", "CANCELLED")):
            break
        time.sleep(15)
    print(f"eval state: {state} ({int(time.time() - et0)}s)", flush=True)
    if "SUCCEEDED" not in state:
        print("error:", getattr(run, "error", None), flush=True)
        return

    run = client.evals.get_evaluation_run(name=run.name, include_evaluation_items=True)
    rr = getattr(run, "evaluation_run_results", None)
    if rr and getattr(rr, "summary_metrics", None):
        nested = getattr(rr.summary_metrics, "metrics", None)
        print("\nSUMMARY METRICS:", dict(nested) if nested else None, flush=True)

    # Per-case
    item_results = getattr(run, "evaluation_item_results", None)
    if item_results and getattr(item_results, "eval_case_results", None):
        for j, cr in enumerate(item_results.eval_case_results):
            cands = getattr(cr, "response_candidate_results", [])
            if cands:
                mr = getattr(cands[0], "metric_results", None)
                print(f"  case {j} metric_results: {dict(mr) if mr else None}", flush=True)
    print("\nDONE.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
