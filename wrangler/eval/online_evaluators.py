"""Online Evaluators — create and manage evaluators that score OTel traces every 10 min.

Online Evaluators automatically score traces from deployed agents using predefined
and custom LLM metrics. Results appear in the Observability tab, Cloud Logging,
and Cloud Monitoring.

Usage:
    uv run python -m wrangler.eval.online_evaluators list
    uv run python -m wrangler.eval.online_evaluators create
    uv run python -m wrangler.eval.online_evaluators verify
    uv run python -m wrangler.eval.online_evaluators delete <evaluator_id>
    uv run python -m wrangler.eval.online_evaluators cleanup
    uv run python -m wrangler.eval.online_evaluators trace-health [minutes]

`trace-health` answers the question these evaluators cannot answer for
themselves: did the traces they scored actually all arrive? It exits non-zero
when spans were dropped, so it can gate a run. See docs/notes/silent-failures.md #8.
"""

import datetime as dt
import os
import sys
import textwrap

import google.auth
import google.auth.transport.requests
import requests as http_requests

from ..core.config import GCP_PROJECT_ID, GCP_REGION

PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
API_BASE = f"https://{GCP_REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_NUMBER}/locations/{GCP_REGION}"

# Seconds. Without it these calls can hang forever against a wedged endpoint.
HTTP_TIMEOUT = 60

CUSTOM_METRICS = [
    {
        "displayName": "Wrangler Task Quality",
        "metric": {
            "llmBasedMetricSpec": {
                "metricPromptTemplate": textwrap.dedent("""\
                    # Instruction
                    You are evaluating a corporate travel and expense AI agent.

                    Score the agent's response on how well it completed the user's task.

                    # Criteria
                    Tool Selection: The agent chose the right tool(s) for the request.
                    Parameter Accuracy: Tool arguments matched what the user specified.
                    Actionable Output: The response gives the user what they need to act.

                    # Rating Scores
                    5: All tools correct, parameters exact, output immediately actionable.
                    4: Right tools, minor parameter issue, clear output.
                    3: Correct result but required extra user effort to parse or verify.
                    2: Wrong tool selected, or critical parameter missing/invented.
                    1: No tool called, hallucinated data, or completely wrong answer.

                    # User Inputs and AI-generated Response
                    ## User Prompt
                    <prompt>{prompt}</prompt>

                    ## AI-generated Response
                    <response>{response}</response>"""),
            },
            "metadata": {
                "title": "Wrangler Task Quality",
                "scoreRange": {"min": 1.0, "max": 5.0},
            },
        },
    },
]

PREDEFINED_METRICS = [
    "final_response_quality_v1",
    "hallucination_v1",
    "safety_v1",
    "tool_use_quality_v1",
]


def _get_agent_engine_ids() -> dict[str, str]:
    """Load agent engine IDs from environment."""
    agents = {}
    for name in ["lite", "flash", "pro", "sonnet", "opus"]:
        eid = os.environ.get(f"{name.upper()}_ENGINE_ID", "")
        if eid:
            agents[name] = eid
    return agents


def _get_headers():
    credentials, _ = google.auth.default()
    credentials.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }


def _agent_resource(engine_id: str) -> str:
    return f"projects/{PROJECT_NUMBER}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"


def _list_registered_metrics(headers) -> dict[str, str]:
    resp = http_requests.get(f"{API_BASE}/evaluationMetrics", headers=headers, timeout=HTTP_TIMEOUT)
    if resp.status_code != 200:
        return {}
    result = {}
    for m in resp.json().get("evaluationMetrics", []):
        display = m.get("displayName", "")
        name = m.get("name", "")
        if display:
            result[display] = name
    return result


def register_custom_metrics() -> list[str]:
    """Register custom LLM metrics. Returns resource names."""
    headers = _get_headers()
    existing = _list_registered_metrics(headers)
    resource_names = []

    for metric_def in CUSTOM_METRICS:
        display_name = metric_def["displayName"]
        if display_name in existing:
            print(f"  '{display_name}' already registered: {existing[display_name]}")
            resource_names.append(existing[display_name])
            continue

        print(f"  Registering '{display_name}'...")
        resp = http_requests.post(
            f"{API_BASE}/evaluationMetrics",
            headers=headers,
            json=metric_def,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            result = resp.json()
            rn = result.get("response", result).get("name", result.get("name", ""))
            print(f"  Registered: {rn}")
            resource_names.append(rn)
        else:
            print(f"  Error {resp.status_code}: {resp.text[:200]}")

    return resource_names


def _build_evaluator_config(label: str, engine_id: str, custom_metric_names: list[str]) -> dict:
    metric_sources: list[dict] = [
        {"metric": {"predefinedMetricSpec": {"metricSpecName": m}}} for m in PREDEFINED_METRICS
    ]
    metric_sources.extend({"metricResourceName": name} for name in custom_metric_names)

    return {
        "displayName": f"Wrangler {label.title()} Online Evaluator",
        "agentResource": _agent_resource(engine_id),
        "metricSources": metric_sources,
        "config": {"randomSampling": {"percentage": 100}},
        "cloudObservability": {
            "traceScope": {},
            "openTelemetry": {"semconvVersion": "1.39.0"},
        },
    }


def list_evaluators():
    headers = _get_headers()
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    evaluators = resp.json().get("onlineEvaluators", [])

    agent_ids = set(_get_agent_engine_ids().values())

    print(f"Found {len(evaluators)} online evaluator(s)\n")
    for ev in evaluators:
        eid = ev["name"].split("/")[-1]
        agent = ev.get("agentResource", "").split("/")[-1]
        is_ours = agent in agent_ids
        metrics = []
        for ms in ev.get("metricSources", []):
            if "metric" in ms:
                spec = ms["metric"].get("predefinedMetricSpec", {})
                metrics.append(spec.get("metricSpecName", "unknown"))
            elif "metricResourceName" in ms:
                metrics.append(ms["metricResourceName"].split("/")[-1])

        print(f"  ID:      {eid}")
        print(f"  State:   {ev.get('state', 'UNKNOWN')}")
        print(f"  Agent:   {agent} {'(wrangler)' if is_ours else ''}")
        print(f"  Metrics: {metrics}")
        print()
    return evaluators


def create_evaluators():
    print("=== Step 1: Register Custom Metrics ===")
    custom_metric_names = register_custom_metrics()

    print("\n=== Step 2: Check Existing Evaluators ===")
    existing = list_evaluators()
    existing_agents = {e.get("agentResource", "") for e in existing}

    print("=== Step 3: Create Online Evaluators ===")
    headers = _get_headers()
    agents = _get_agent_engine_ids()

    for label, engine_id in agents.items():
        agent_res = _agent_resource(engine_id)
        if agent_res in existing_agents:
            print(f"  {label}: evaluator already exists, skipping")
            continue

        config = _build_evaluator_config(label, engine_id, custom_metric_names)
        n_metrics = len(config["metricSources"])
        print(f"  Creating '{config['displayName']}' with {n_metrics} metrics...")

        resp = http_requests.post(
            f"{API_BASE}/onlineEvaluators", headers=headers, json=config, timeout=HTTP_TIMEOUT
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"  Operation: {result.get('name', '')}")
        else:
            print(f"  Error {resp.status_code}: {resp.text[:300]}")

    print("\n=== Final State ===")
    list_evaluators()


def verify_evaluators():
    headers = _get_headers()
    agents = _get_agent_engine_ids()

    print("=== Online Evaluator Status ===")
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    evaluators = resp.json().get("onlineEvaluators", [])

    agent_ids = set(agents.values())
    matching = [e for e in evaluators if e.get("agentResource", "").split("/")[-1] in agent_ids]

    if not matching:
        print("  No evaluators found for wrangler agents")
        return

    for ev in matching:
        state = ev.get("state", "UNKNOWN")
        eid = ev["name"].split("/")[-1]
        agent = ev.get("agentResource", "").split("/")[-1]
        print(f"  {eid}: state={state}, agent={agent}")

    all_active = all(e.get("state") == "ACTIVE" for e in matching)
    print(f"\n  {'PASS' if all_active else 'WARN'}: {len(matching)} evaluator(s)")

    # Check Cloud Logging for eval results
    print("\n=== Evaluation Results in Cloud Logging ===")
    for label, engine_id in agents.items():
        body = {
            "resourceNames": [f"projects/{GCP_PROJECT_ID}"],
            "filter": (
                f'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
                f'resource.labels.reasoning_engine_id="{engine_id}" '
                f'labels."event.name"="gen_ai.evaluation.result"'
            ),
            "orderBy": "timestamp desc",
            "pageSize": 20,
        }
        resp = http_requests.post(
            "https://logging.googleapis.com/v2/entries:list",
            headers=headers,
            json=body,
            timeout=HTTP_TIMEOUT,
        )
        entries = resp.json().get("entries", [])
        print(f"\n  {label} ({engine_id}): {len(entries)} eval result(s)")


def delete_evaluator(evaluator_id: str):
    headers = _get_headers()
    print(f"Deleting evaluator {evaluator_id}...")
    resp = http_requests.delete(
        f"{API_BASE}/onlineEvaluators/{evaluator_id}",
        headers=headers,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code == 200:
        print("  Deleted")
    else:
        print(f"  Error {resp.status_code}: {resp.text}")


def cleanup():
    """Delete all wrangler online evaluators and custom metrics."""
    headers = _get_headers()
    agents = _get_agent_engine_ids()
    agent_ids = set(agents.values())

    print("=== Cleaning up Online Evaluators ===")
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    for ev in resp.json().get("onlineEvaluators", []):
        agent = ev.get("agentResource", "").split("/")[-1]
        if agent in agent_ids:
            eid = ev["name"].split("/")[-1]
            delete_evaluator(eid)

    print("\n=== Cleaning up Custom Metrics ===")
    metric_names = {m["displayName"] for m in CUSTOM_METRICS}
    existing = _list_registered_metrics(headers)
    for display_name, resource_name in existing.items():
        if display_name in metric_names:
            mid = resource_name.split("/")[-1]
            print(f"Deleting metric '{display_name}' ({mid})...")
            resp = http_requests.delete(
                f"{API_BASE}/evaluationMetrics/{mid}", headers=headers, timeout=HTTP_TIMEOUT
            )
            print(f"  {'Deleted' if resp.status_code == 200 else f'Error {resp.status_code}'}")

    print("\nCleanup complete.")


# The exact string the OTLP HTTP exporter logs when it abandons a batch, from
# the OTLP proto-http trace exporter in the opentelemetry SDK. Matched as text
# because the container logs everything at DEFAULT severity, so a
# severity>=ERROR filter returns nothing and reads as a clean bill of health.
_SPAN_DROP_MARKER = "Failed to export span batch"
_LOGGING_API = "https://logging.googleapis.com/v2/entries:list"


def count_span_export_errors(engine_id: str, minutes: int = 60) -> dict:
    """Count dropped span batches for one engine over the last `minutes`.

    Online evaluators score OTel traces, so a dropped batch is missing *input*
    to the scorer — and the scorer cannot tell "the agent did not do this" from
    "the export never arrived". It fails toward a lower score and correlates
    with load, so the busiest runs lose the most evidence.

    The export timeouts themselves were fixed (see docs/notes/silent-failures.md
    #8) but nothing *detected* them, which is why they survived so long. This is
    the detector: cheap enough to run before trusting an online-eval number.

    Uses the Logging REST API with the credentials this module already holds
    rather than google-cloud-logging, which is only a transitive dependency here.
    """
    body = {
        "resourceNames": [f"projects/{GCP_PROJECT_ID}"],
        "filter": (
            'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
            f'AND resource.labels.reasoning_engine_id="{engine_id}" '
            f'AND textPayload:"{_SPAN_DROP_MARKER}" '
            f'AND timestamp>="{_minutes_ago_rfc3339(minutes)}"'
        ),
        "pageSize": 200,
    }
    resp = http_requests.post(_LOGGING_API, headers=_get_headers(), json=body, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    entries = resp.json().get("entries", [])
    return {
        "engine_id": engine_id,
        "window_minutes": minutes,
        "dropped_batches": len(entries),
        "truncated": len(entries) >= body["pageSize"],
    }


def _minutes_ago_rfc3339(minutes: int) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def trace_health(args: list[str]):
    """Report span-export health for the engines named in the environment.

    Exits non-zero when any engine is dropping batches, so this can gate a run
    rather than merely inform one.
    """
    minutes = int(args[0]) if args else 60
    agents = _get_agent_engine_ids()
    if not agents:
        print("  No *_ENGINE_ID set — nothing to check.")
        print("  Engine ids are supplied per run, never pinned; see CLAUDE.md.")
        return

    print(f"=== Span export health (last {minutes} min) ===")
    degraded = []
    for name, eid in sorted(agents.items()):
        result = count_span_export_errors(eid, minutes)
        n = result["dropped_batches"]
        more = "+" if result["truncated"] else ""
        if n:
            degraded.append(name)
            print(f"  {name:8} {eid}: {n}{more} DROPPED span batches")
        else:
            print(f"  {name:8} {eid}: clean")

    if degraded:
        print(
            f"\n  FAIL: {', '.join(degraded)} lost spans. Online-eval scores for this "
            "window are a lower bound, not a measurement."
        )
        print("  See docs/notes/silent-failures.md #8.")
        sys.exit(1)
    print(f"\n  PASS: {len(agents)} engine(s), no dropped batches")


COMMANDS = {
    "list": lambda args: list_evaluators(),
    "create": lambda args: create_evaluators(),
    "verify": lambda args: verify_evaluators(),
    "trace-health": trace_health,
    "delete": lambda args: (
        delete_evaluator(args[0]) if args else print("Usage: delete <evaluator_id>")
    ),
    "cleanup": lambda args: cleanup(),
}

if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    # Load .env from repo root, then example dir (example overrides root)
    load_dotenv()
    # parents[2] is the repo root: this file is wrangler/eval/online_evaluators.py,
    # so .parent.parent stopped inside wrangler/ and pointed at a path that has
    # never existed — the example .env was silently never loaded. Same bug as
    # traffic.py had (fixed in f1dc67f).
    example_env = Path(__file__).resolve().parents[2] / "examples" / "multi_model_agents" / ".env"
    if example_env.exists():
        load_dotenv(str(example_env), override=True)

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python -m wrangler.eval.online_evaluators <command>")
        print(f"Commands: {', '.join(COMMANDS)}")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
