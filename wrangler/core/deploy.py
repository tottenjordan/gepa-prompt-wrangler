"""Deploy/update agents on the Gemini Enterprise Agent Platform (GEAP)."""

import os

import vertexai

from .config import GCP_PROJECT_ID, GCP_REGION, GCP_STAGING_BUCKET

REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent-engines,evaluation]>=1.154.0",
    "google-genai>=1.66.0",
    "google-auth>=2.52.0",
    "google-adk[a2a,agent-identity,eval]>=1.34.1",
    "litellm>=1.83.14",
    "python-dotenv>=1.0.0",
    "pydantic>=2.12.5",
    "cloudpickle>=3.0,<4.0",
]


def _get_client():
    return vertexai.Client(project=GCP_PROJECT_ID, location=GCP_REGION)


def deploy_agent(
    agent,
    display_name: str | None = None,
    env_vars: dict | None = None,
    extra_packages: list[str] | None = None,
) -> str:
    """Deploy a new agent to GEAP. Returns the resource name.

    Args:
        agent: The ADK agent object to deploy.
        display_name: Display name for the agent in GEAP.
        env_vars: Additional environment variables.
        extra_packages: Local directories to upload alongside the agent.
            Required when the agent's tool functions are defined in local
            modules that aren't pip-installable (e.g., ``["agents/example_agent"]``).
    """
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    config = {
        "staging_bucket": f"gs://{GCP_STAGING_BUCKET}",
        "requirements": REQUIREMENTS,
        "display_name": display_name or agent.name,
        "labels": {"solution": "promp-wrangler"},
        "env_vars": {
            "GCP_PROJECT_ID": GCP_PROJECT_ID,
            "GCP_REGION": GCP_REGION,
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
            **(env_vars or {}),
        },
    }
    if extra_packages:
        config["extra_packages"] = extra_packages

    print(f"  Deploying {agent.name}...")
    remote = _get_client().agent_engines.create(agent=agent, config=config)
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    engine_id = resource_name.split("/")[-1]
    print(f"  Deployed: {engine_id}")
    return engine_id


def update_agent(
    agent,
    engine_id: str,
    display_name: str | None = None,
    env_vars: dict | None = None,
    extra_packages: list[str] | None = None,
) -> str:
    """Update an existing agent on GEAP. Returns the resource name."""
    vertexai.init(
        project=GCP_PROJECT_ID,
        location=GCP_REGION,
        staging_bucket=f"gs://{GCP_STAGING_BUCKET}",
    )

    if not engine_id.startswith("projects/"):
        engine_id = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/reasoningEngines/{engine_id}"

    config = {
        "staging_bucket": f"gs://{GCP_STAGING_BUCKET}",
        "requirements": REQUIREMENTS,
        "display_name": display_name or agent.name,
        "labels": {"solution": "promp-wrangler"},
        "env_vars": {
            "GCP_PROJECT_ID": GCP_PROJECT_ID,
            "GCP_REGION": GCP_REGION,
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
            **(env_vars or {}),
        },
    }
    if extra_packages:
        config["extra_packages"] = extra_packages

    print(f"  Updating {agent.name} ({engine_id.split('/')[-1]})...")
    remote = _get_client().agent_engines.update(
        name=engine_id, agent=agent, config=config,
    )
    resource_name = getattr(remote, "resource_name", None) or remote.api_resource.name
    print(f"  Updated: {resource_name.split('/')[-1]}")
    return resource_name.split("/")[-1]
