"""Online Evaluators — create and manage evaluators that score OTel traces every 10 min.

Online Evaluators automatically score traces from deployed agents using predefined
and custom LLM metrics. Results appear in the Observability tab, Cloud Logging,
and Cloud Monitoring.

Usage:
    uv run python -m wrangler.online_evaluators list
    uv run python -m wrangler.online_evaluators create
    uv run python -m wrangler.online_evaluators verify
    uv run python -m wrangler.online_evaluators delete <evaluator_id>
    uv run python -m wrangler.online_evaluators cleanup
"""

import json
import os
import sys
import textwrap

import google.auth
import google.auth.transport.requests
import requests as http_requests

from .config import GCP_PROJECT_ID, GCP_REGION

PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
API_BASE = f"https://{GCP_REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_NUMBER}/locations/{GCP_REGION}"

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
    resp = http_requests.get(f"{API_BASE}/evaluationMetrics", headers=headers)
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
            f"{API_BASE}/evaluationMetrics", headers=headers, json=metric_def,
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
    metric_sources = [
        {"metric": {"predefinedMetricSpec": {"metricSpecName": m}}}
        for m in PREDEFINED_METRICS
    ]
    for name in custom_metric_names:
        metric_sources.append({"metricResourceName": name})

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
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers)
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

        resp = http_requests.post(f"{API_BASE}/onlineEvaluators", headers=headers, json=config)
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
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers)
    resp.raise_for_status()
    evaluators = resp.json().get("onlineEvaluators", [])

    agent_ids = set(agents.values())
    matching = [
        e for e in evaluators if e.get("agentResource", "").split("/")[-1] in agent_ids
    ]

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
    print(f"\n=== Evaluation Results in Cloud Logging ===")
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
            headers=headers, json=body,
        )
        entries = resp.json().get("entries", [])
        print(f"\n  {label} ({engine_id}): {len(entries)} eval result(s)")


def delete_evaluator(evaluator_id: str):
    headers = _get_headers()
    print(f"Deleting evaluator {evaluator_id}...")
    resp = http_requests.delete(
        f"{API_BASE}/onlineEvaluators/{evaluator_id}", headers=headers,
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
    resp = http_requests.get(f"{API_BASE}/onlineEvaluators", headers=headers)
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
            resp = http_requests.delete(f"{API_BASE}/evaluationMetrics/{mid}", headers=headers)
            print(f"  {'Deleted' if resp.status_code == 200 else f'Error {resp.status_code}'}")

    print("\nCleanup complete.")


COMMANDS = {
    "list": lambda args: list_evaluators(),
    "create": lambda args: create_evaluators(),
    "verify": lambda args: verify_evaluators(),
    "delete": lambda args: delete_evaluator(args[0]) if args else print("Usage: delete <evaluator_id>"),
    "cleanup": lambda args: cleanup(),
}

if __name__ == "__main__":
    from dotenv import load_dotenv
    from pathlib import Path

    # Load .env from repo root, then example dir (example overrides root)
    load_dotenv()
    example_env = Path(__file__).parent.parent / "examples" / "multi_model_agents" / ".env"
    if example_env.exists():
        load_dotenv(str(example_env), override=True)

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m wrangler.online_evaluators <command>")
        print(f"Commands: {', '.join(COMMANDS)}")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
