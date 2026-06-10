"""KFP v2 pipeline components for GEPA prompt optimization.

Each component operates on a single agent-prompt pair (except archive and analysis).
Heavy components use code injection from GCS following the NovaStorm pattern.

IMPORTANT: KFP serializes each @dsl.component function in isolation. All helper
logic (code injection, env setup, GCS I/O) must be defined INLINE within each
component body — module-level functions are NOT available at runtime.
"""

from kfp import dsl
from kfp.dsl import Output, Metrics, Markdown


# ── Component 1: Archive agent code ──────────────────────────────


@dsl.component(
    base_image="python:3.11",
    packages_to_install=["google-cloud-storage>=3.0.0"],
)
def archive_agent_code(
    project_id: str,
    bucket_name: str,
    run_id: str,
    manifest_json: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Verify the pre-uploaded code tarball exists on GCS.

    The actual packaging and upload happens in deploy_pipeline.py before
    pipeline submission — this component just validates the artifact exists
    and logs metadata for the DAG.
    """
    import json
    import logging

    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    code_blob = f"pipeline-runs/{run_id}/code.tar.gz"
    blob = bucket.blob(code_blob)

    if not blob.exists():
        raise RuntimeError(f"Code tarball not found: gs://{bucket_name}/{code_blob}")

    blob.reload()
    tarball_size = blob.size / 1024 if blob.size else 0
    code_uri = f"gs://{bucket_name}/{code_blob}"

    metrics.log_metric("tarball_size_kb", round(tarball_size, 1))

    manifest = json.loads(manifest_json)
    n_pairs = len(manifest.get("pairs", []))
    with open(summary.path, "w") as f:
        f.write(f"## Archive\n\n")
        f.write(f"- **Code URI**: `{code_uri}`\n")
        f.write(f"- **Size**: {tarball_size:.0f} KB\n")
        f.write(f"- **Pairs**: {n_pairs}\n")

    logging.info(f"Verified code tarball: {code_uri} ({tarball_size:.0f} KB)")
    return code_uri


# ── Component 2: Deploy single agent ─────────────────────────────


@dsl.component(base_image="python:3.11")
def deploy_single_agent(
    project_id: str,
    location: str,
    bucket_name: str,
    run_id: str,
    pair_json: str,
    agent_module: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Deploy a single agent-prompt pair to GEAP."""
    import json
    import logging
    import os
    import sys
    import tarfile
    import time

    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    pair = json.loads(pair_json)
    pair_id = pair["id"]
    model = pair["model"]

    # -- Code injection --
    logging.info(f"Injecting code from gs://{bucket_name}/pipeline-runs/{run_id}/code.tar.gz")
    gcs = storage.Client(project=project_id)
    gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/code.tar.gz").download_to_filename("/tmp/code.tar.gz")
    with tarfile.open("/tmp/code.tar.gz", "r:gz") as tar:
        tar.extractall(path="/app")
    sys.path.insert(0, "/app")

    os.environ["GCP_PROJECT_ID"] = project_id
    os.environ["GCP_REGION"] = location
    os.environ["GCP_STAGING_BUCKET"] = bucket_name
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

    from wrangler.core.config import resolve_model
    from wrangler.core import deploy as deployer

    existing_engine_id = pair.get("engine_id", "")
    if existing_engine_id:
        logging.info(f"[{pair_id}] Reusing existing engine: {existing_engine_id}")
        result = {
            "pair_id": pair_id, "engine_id": existing_engine_id,
            "model": model, "source": "existing", "elapsed": 0,
        }
    else:
        from pathlib import Path
        agent_path = Path(f"/app/{agent_module}")
        sys.path.insert(0, str(agent_path.parent))
        module_name = agent_path.name
        mod = __import__(module_name)

        if hasattr(mod, "create_agent"):
            agent = mod.create_agent(model, pair["system_prompt"])
        else:
            agent = getattr(mod, "root_agent", None) or mod.agent.root_agent
            agent.model = resolve_model(model)
            agent.instruction = pair["system_prompt"]

        t0 = time.time()
        engine_id = deployer.deploy_agent(agent, display_name=f"gepa-{pair_id}")
        elapsed = time.time() - t0

        result = {
            "pair_id": pair_id, "engine_id": engine_id,
            "model": model, "original_prompt": pair["system_prompt"],
            "source": "deployed", "elapsed": elapsed,
        }
        logging.info(f"[{pair_id}] Deployed in {elapsed:.0f}s: {engine_id}")

    # Upload stage result to GCS
    blob_path = f"pipeline-runs/{run_id}/stages/deploy/{pair_id}.json"
    gcs.bucket(bucket_name).blob(blob_path).upload_from_string(
        json.dumps(result, indent=2, default=str), content_type="application/json")

    metrics.log_metric("elapsed_seconds", result.get("elapsed", 0))
    with open(summary.path, "w") as f:
        f.write(f"## Deploy: {pair_id}\n\n")
        f.write(f"- **Model**: {model}\n")
        f.write(f"- **Engine ID**: `{result['engine_id']}`\n")
        f.write(f"- **Source**: {result['source']}\n")
        f.write(f"- **Elapsed**: {result.get('elapsed', 0):.0f}s\n")

    return json.dumps(result)


# ── Component 3: Evaluate single agent ────────────────────────────


@dsl.component(base_image="python:3.11")
def eval_single_agent(
    project_id: str,
    location: str,
    bucket_name: str,
    run_id: str,
    pair_json: str,
    eval_data_path: str,
    phase: str,
    num_runs: int,
    judge_model: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Evaluate a single deployed agent (before or after optimization)."""
    import json
    import logging
    import os
    import sys
    import tarfile
    import time

    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    pair = json.loads(pair_json)
    pair_id = pair["id"]
    model = pair["model"]

    # -- Code injection --
    gcs = storage.Client(project=project_id)
    gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/code.tar.gz").download_to_filename("/tmp/code.tar.gz")
    with tarfile.open("/tmp/code.tar.gz", "r:gz") as tar:
        tar.extractall(path="/app")
    sys.path.insert(0, "/app")

    os.environ["GCP_PROJECT_ID"] = project_id
    os.environ["GCP_REGION"] = location
    os.environ["GCP_STAGING_BUCKET"] = bucket_name
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

    from wrangler.eval.evaluator import run_batch_eval_averaged
    from wrangler.core.converter import load_eval_file
    from wrangler.core.config import MODEL_COSTS

    # Read deploy result from GCS
    deploy_blob = gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/stages/deploy/{pair_id}.json")
    deploy_data = json.loads(deploy_blob.download_as_text())
    engine_id = deploy_data["engine_id"]

    eval_cases = load_eval_file(f"/app/{eval_data_path}")
    logging.info(f"[{pair_id}] {phase} eval: {len(eval_cases)} cases, {num_runs} runs")

    t0 = time.time()
    result = run_batch_eval_averaged(
        engine_id, eval_cases, num_runs=num_runs,
        agent_name=pair_id, model=model,
    )
    elapsed = time.time() - t0

    costs = pair.get("costs") or MODEL_COSTS.get(model, {"input": 0, "output": 0})
    input_tokens = result.token_usage.get("input_tokens", 0)
    output_tokens = result.token_usage.get("output_tokens", 0)
    input_cost = input_tokens * costs["input"] / 1_000_000
    output_cost = output_tokens * costs["output"] / 1_000_000

    stage_data = {
        "scores": result.scores,
        "per_case": result.per_case,
        "scores_std": result.scores_std,
        "num_runs": result.num_runs,
        "elapsed": elapsed,
        "token_usage": result.token_usage,
        "costs": {"input_usd": input_cost, "output_usd": output_cost},
    }
    stage_name = f"eval_{phase}"
    stage_blob = f"pipeline-runs/{run_id}/stages/{stage_name}/{pair_id}.json"
    gcs.bucket(bucket_name).blob(stage_blob).upload_from_string(
        json.dumps(stage_data, indent=2, default=str), content_type="application/json")

    avg_score = sum(result.scores.values()) / max(len(result.scores), 1) if result.scores else 0
    metrics.log_metric("avg_score", round(avg_score, 4))
    metrics.log_metric("elapsed_seconds", round(elapsed, 1))
    metrics.log_metric("input_tokens", input_tokens)
    metrics.log_metric("output_tokens", output_tokens)
    metrics.log_metric("input_cost_usd", round(input_cost, 4))
    metrics.log_metric("output_cost_usd", round(output_cost, 4))
    for metric_name, score in result.scores.items():
        metrics.log_metric(metric_name, round(score, 4))

    with open(summary.path, "w") as f:
        f.write(f"## Eval ({phase}): {pair_id}\n\n")
        f.write("| Metric | Score |\n|--------|-------|\n")
        for m, s in sorted(result.scores.items()):
            std = result.scores_std.get(m)
            std_str = f" +/- {std:.3f}" if std else ""
            f.write(f"| {m} | {s:.4f}{std_str} |\n")
        f.write(f"| **Average** | **{avg_score:.4f}** |\n\n")
        f.write("| Resource | Value |\n|----------|-------|\n")
        m, s = divmod(int(elapsed), 60)
        f.write(f"| Processing time | {m}m {s:02d}s |\n")
        f.write(f"| Input tokens | ~{input_tokens:,} |\n")
        f.write(f"| Output tokens | ~{output_tokens:,} |\n")
        f.write(f"| Input cost | ${input_cost:.4f} |\n")
        f.write(f"| Output cost | ${output_cost:.4f} |\n")
        f.write(f"| **Total cost** | **${input_cost + output_cost:.4f}** |\n")

    logging.info(f"[{pair_id}] {phase} eval done: avg={avg_score:.3f}, {elapsed:.0f}s")
    return json.dumps(stage_data)


# ── Component 4: Optimize single agent ────────────────────────────


@dsl.component(base_image="python:3.11")
def optimize_single_agent(
    project_id: str,
    location: str,
    bucket_name: str,
    run_id: str,
    pair_json: str,
    eval_data_path: str,
    agent_module: str,
    judge_model: str,
    eval_thresholds_json: str,
    secret_id: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Run GEPA optimization for a single agent-prompt pair."""
    import io
    import json
    import logging
    import os
    import sys
    import tarfile
    import time

    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    pair = json.loads(pair_json)
    pair_id = pair["id"]
    model = pair["model"]

    # -- Code injection --
    gcs = storage.Client(project=project_id)
    gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/code.tar.gz").download_to_filename("/tmp/code.tar.gz")
    with tarfile.open("/tmp/code.tar.gz", "r:gz") as tar:
        tar.extractall(path="/app")
    sys.path.insert(0, "/app")

    os.environ["GCP_PROJECT_ID"] = project_id
    os.environ["GCP_REGION"] = location
    os.environ["GCP_STAGING_BUCKET"] = bucket_name
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

    # -- Load agent env vars from Secret Manager --
    if secret_id:
        from google.cloud import secretmanager
        from dotenv import load_dotenv
        logging.info(f"Loading secrets from {secret_id}")
        sm = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        payload = sm.access_secret_version(name=secret_name).payload.data.decode("UTF-8")
        load_dotenv(stream=io.StringIO(payload), override=True)
        # Force Vertex AI ADC — API keys don't work with Evaluation Service
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)

    from pathlib import Path

    # -- Start local MCP servers for reliable tool connections --
    import subprocess
    mcp_servers_dir = Path("/app/examples/multi_model_agents/mcp_servers")
    mcp_procs = []
    if mcp_servers_dir.exists():
        servers = [
            ("search", 8001, "SEARCH_MCP_URL"),
            ("booking", 8002, "BOOKING_MCP_URL"),
            ("expense", 8003, "EXPENSE_MCP_URL"),
        ]
        for name, port, env_key in servers:
            server_py = mcp_servers_dir / name / "server.py"
            if server_py.exists():
                proc = subprocess.Popen(
                    [sys.executable, str(server_py)],
                    cwd=str(mcp_servers_dir / name),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                mcp_procs.append(proc)
                os.environ[env_key] = f"http://localhost:{port}/mcp"
                logging.info(f"Started local MCP server: {name} on port {port}")
        time.sleep(5)
        # Verify servers are alive
        alive = sum(1 for p in mcp_procs if p.poll() is None)
        logging.info(f"Started {alive}/{len(mcp_procs)} local MCP server(s)")
    else:
        logging.warning("MCP servers dir not found — using remote URLs from secrets")

    from wrangler.optimize.optimizer import optimize
    from wrangler.core.config import MODEL_COSTS

    opt_module = pair.get("agent_module") or agent_module
    agent_path = Path(f"/app/{opt_module}")
    # Add agent's project root to sys.path so config.py, registry.py are importable
    sys.path.insert(0, str(agent_path.parent.parent))
    stem = agent_path.stem.replace("_agent", "")
    opt_dir = agent_path.parent / f"{stem}_opt"
    if opt_dir.is_dir() and (opt_dir / "__init__.py").exists():
        agent_path = opt_dir

    sampler_cfg = agent_path / "sampler_config.json"
    eval_thresholds = json.loads(eval_thresholds_json) if eval_thresholds_json else None

    original_prompt = pair.get("system_prompt", "")
    t0 = time.time()
    optimized_prompt = optimize(
        str(agent_path),
        eval_data_path=f"/app/{eval_data_path}",
        sampler_config_path=str(sampler_cfg) if sampler_cfg.exists() else None,
        agent_name=pair_id,
        eval_thresholds=eval_thresholds,
        judge_model=judge_model,
    )
    elapsed = time.time() - t0

    # Clean up local MCP servers
    for proc in mcp_procs:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    if mcp_procs:
        logging.info(f"Stopped {len(mcp_procs)} local MCP server(s)")

    # Clean up MCP sessions
    import asyncio
    from google.adk.tools.base_toolset import BaseToolset
    async def _cleanup_sessions():
        for mod_name in list(sys.modules):
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, 'root_agent'):
                agent = getattr(mod, 'root_agent', None)
                if agent and hasattr(agent, 'tools'):
                    for t in agent.tools:
                        if isinstance(t, BaseToolset):
                            try:
                                await t.close()
                            except Exception:
                                pass
    try:
        asyncio.run(_cleanup_sessions())
    except RuntimeError:
        pass

    judge_costs = MODEL_COSTS.get(judge_model, {"input": 0, "output": 0})
    est_input = len(original_prompt) * 50
    est_output = len(optimized_prompt) * 10
    input_cost = est_input * judge_costs["input"] / 1_000_000
    output_cost = est_output * judge_costs["output"] / 1_000_000

    stage_data = {
        "optimized_prompt": optimized_prompt,
        "elapsed": elapsed,
        "original_chars": len(original_prompt),
        "optimized_chars": len(optimized_prompt),
        "token_usage": {
            "input_tokens": est_input,
            "output_tokens": est_output,
            "is_estimate": True,
        },
        "costs": {"input_usd": input_cost, "output_usd": output_cost},
    }
    stage_blob = f"pipeline-runs/{run_id}/stages/optimize/{pair_id}.json"
    gcs.bucket(bucket_name).blob(stage_blob).upload_from_string(
        json.dumps(stage_data, indent=2, default=str), content_type="application/json")

    metrics.log_metric("elapsed_seconds", round(elapsed, 1))
    metrics.log_metric("original_chars", len(original_prompt))
    metrics.log_metric("optimized_chars", len(optimized_prompt))
    metrics.log_metric("input_tokens", est_input)
    metrics.log_metric("output_tokens", est_output)
    metrics.log_metric("input_cost_usd", round(input_cost, 4))
    metrics.log_metric("output_cost_usd", round(output_cost, 4))

    m, s = divmod(int(elapsed), 60)
    pct = ((len(optimized_prompt) - len(original_prompt)) / max(len(original_prompt), 1)) * 100
    with open(summary.path, "w") as f:
        f.write(f"## Optimization: {pair_id}\n\n")
        f.write(f"- **Elapsed**: {m}m {s:02d}s\n")
        f.write(f"- **Prompt**: {len(original_prompt)} → {len(optimized_prompt)} chars ({pct:+.0f}%)\n")
        f.write(f"- **Input tokens**: ~{est_input:,} | **Output tokens**: ~{est_output:,}\n")
        f.write(f"- **Input cost**: ${input_cost:.4f} | **Output cost**: ${output_cost:.4f} | **Total**: ${input_cost + output_cost:.4f}\n")

    logging.info(f"[{pair_id}] Optimized in {m}m {s:02d}s: {len(original_prompt)} → {len(optimized_prompt)} chars")
    return json.dumps(stage_data)


# ── Component 5: Redeploy single agent ────────────────────────────


@dsl.component(base_image="python:3.11")
def redeploy_single_agent(
    project_id: str,
    location: str,
    bucket_name: str,
    run_id: str,
    pair_json: str,
    agent_module: str,
    secret_id: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Redeploy a single agent with its optimized prompt.

    Downloads the existing pickle from GCS, modifies the instruction
    inside it, re-uploads, and triggers a redeploy. Requires code
    injection + secrets because cloudpickle needs the agent's module
    dependencies (registry, config, etc.) to deserialize.
    """
    import io
    import json
    import logging
    import os
    import sys
    import tarfile
    import time
    from datetime import datetime

    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    pair = json.loads(pair_json)
    pair_id = pair["id"]

    # -- Code injection --
    gcs = storage.Client(project=project_id)
    gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/code.tar.gz").download_to_filename("/tmp/code.tar.gz")
    with tarfile.open("/tmp/code.tar.gz", "r:gz") as tar:
        tar.extractall(path="/app")
    sys.path.insert(0, "/app")

    os.environ["GCP_PROJECT_ID"] = project_id
    os.environ["GCP_REGION"] = location
    os.environ["GCP_STAGING_BUCKET"] = bucket_name
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"

    # -- Load agent env vars from Secret Manager --
    if secret_id:
        from google.cloud import secretmanager
        from dotenv import load_dotenv
        logging.info(f"Loading secrets from {secret_id}")
        sm = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        payload = sm.access_secret_version(name=secret_name).payload.data.decode("UTF-8")
        load_dotenv(stream=io.StringIO(payload), override=True)
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)

    # Add agent module paths for cloudpickle deserialization
    from pathlib import Path
    agent_path = Path(f"/app/{agent_module}")
    sys.path.insert(0, str(agent_path.parent))
    sys.path.insert(0, str(agent_path.parent.parent))

    # Read deploy + optimize results from GCS
    deploy_data = json.loads(
        gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/stages/deploy/{pair_id}.json").download_as_text())
    optimize_data = json.loads(
        gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/stages/optimize/{pair_id}.json").download_as_text())

    engine_id = deploy_data["engine_id"]
    optimized_prompt = optimize_data["optimized_prompt"]

    # Get the pickle URI from the deployed agent
    import vertexai
    vertexai.init(project=project_id, location=location, staging_bucket=f"gs://{bucket_name}")
    client = vertexai.Client(project=project_id, location=location)

    if not engine_id.startswith("projects/"):
        full_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}"
    else:
        full_name = engine_id

    agent_engine = client.agent_engines.get(name=full_name)
    pickle_uri = agent_engine.api_resource.spec.package_spec.pickle_object_gcs_uri
    logging.info(f"[{pair_id}] Pickle URI: {pickle_uri}")

    # Download, modify instruction, re-upload
    import cloudpickle
    pickle_bucket = pickle_uri.replace("gs://", "").split("/")[0]
    pickle_blob_path = "/".join(pickle_uri.replace("gs://", "").split("/")[1:])
    blob = gcs.bucket(pickle_bucket).blob(pickle_blob_path)
    pickle_data = blob.download_as_bytes()

    agent_obj = cloudpickle.loads(pickle_data)
    old_instruction = agent_obj._tmpl_attrs["agent"].instruction
    agent_obj._tmpl_attrs["agent"].instruction = optimized_prompt
    blob.upload_from_string(cloudpickle.dumps(agent_obj))
    logging.info(f"[{pair_id}] Updated instruction: {len(old_instruction)} → {len(optimized_prompt)} chars")

    # Trigger redeploy
    t0 = time.time()
    client.agent_engines.update(
        name=full_name,
        config={"display_name": f"gepa-{pair_id}", "labels": {"solution": "promp-wrangler"}},
    )
    elapsed = time.time() - t0

    result = {
        "pair_id": pair_id, "engine_id": engine_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed": elapsed,
    }
    stage_blob = f"pipeline-runs/{run_id}/stages/redeploy/{pair_id}.json"
    gcs.bucket(bucket_name).blob(stage_blob).upload_from_string(
        json.dumps(result, indent=2, default=str), content_type="application/json")

    metrics.log_metric("elapsed_seconds", round(elapsed, 1))
    with open(summary.path, "w") as f:
        f.write(f"## Redeploy: {pair_id}\n\n")
        f.write(f"- **Engine ID**: `{engine_id}`\n")
        f.write(f"- **Prompt length**: {len(optimized_prompt)} chars\n")
        f.write(f"- **Elapsed**: {elapsed:.0f}s\n")

    logging.info(f"[{pair_id}] Redeployed in {elapsed:.0f}s")
    return json.dumps(result)


# ── Component 6: Generate analysis ────────────────────────────────


@dsl.component(base_image="python:3.11")
def generate_analysis(
    project_id: str,
    location: str,
    bucket_name: str,
    run_id: str,
    manifest_json: str,
    metrics: Output[Metrics],
    summary: Output[Markdown],
) -> str:
    """Aggregate all per-pair results and generate the final analysis report."""
    import json
    import logging
    import os
    import sys
    import tarfile
    import time
    from pathlib import Path

    from google.cloud import storage as gcs_storage

    logging.basicConfig(level=logging.INFO)

    # -- Code injection --
    gcs = gcs_storage.Client(project=project_id)
    gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/code.tar.gz").download_to_filename("/tmp/code.tar.gz")
    with tarfile.open("/tmp/code.tar.gz", "r:gz") as tar:
        tar.extractall(path="/app")
    sys.path.insert(0, "/app")

    os.environ["GCP_PROJECT_ID"] = project_id
    os.environ["GCP_REGION"] = location
    os.environ["GCP_STAGING_BUCKET"] = bucket_name
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
    os.environ["VLM_MODEL"] = os.getenv("VLM_MODEL", "gemini-3.5-flash")
    os.environ["IMAGE_MODEL"] = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image")

    import matplotlib
    matplotlib.use("Agg")

    from wrangler.reporting.reporter import generate_report
    from wrangler.core.converter import load_eval_file
    from wrangler.core.config import MODEL_COSTS

    def _read_stage(stage, pair_id):
        blob = gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/stages/{stage}/{pair_id}.json")
        return json.loads(blob.download_as_text())

    manifest = json.loads(manifest_json)
    pairs = manifest.get("pairs", [])
    eval_data_path = manifest.get("eval_data", "")

    results = {}
    total_input_cost = 0
    total_output_cost = 0
    total_elapsed = 0

    for pair in pairs:
        pair_id = pair["id"]
        model = pair["model"]

        deploy_data = _read_stage("deploy", pair_id)
        eval_before = _read_stage("eval_before", pair_id)
        optimize_data = _read_stage("optimize", pair_id)
        eval_after = _read_stage("eval_after", pair_id)

        results[pair_id] = {
            "model": model,
            "description": pair.get("description", ""),
            "original_prompt": deploy_data.get("original_prompt", pair.get("system_prompt", "")),
            "before": eval_before.get("scores", {}),
            "before_per_case": eval_before.get("per_case", []),
            "before_std": eval_before.get("scores_std", {}),
            "after": eval_after.get("scores", {}),
            "after_per_case": eval_after.get("per_case", []),
            "after_std": eval_after.get("scores_std", {}),
            "num_runs": eval_before.get("num_runs", 1),
            "optimized_prompt": optimize_data.get("optimized_prompt", ""),
        }

        for stage_data in [eval_before, optimize_data, eval_after]:
            costs = stage_data.get("costs", {})
            total_input_cost += costs.get("input_usd", 0)
            total_output_cost += costs.get("output_usd", 0)
            total_elapsed += stage_data.get("elapsed", 0)

    if eval_data_path:
        try:
            eval_cases = load_eval_file(f"/app/{eval_data_path}")
            results["_eval_metadata"] = {
                "cases": [
                    {"tier": c.get("tier", ""), "category": c.get("category", ""),
                     "prompt": c.get("prompt", ""), "index": i}
                    for i, c in enumerate(eval_cases)
                ]
            }
        except Exception:
            pass

    reports_dir = Path("/tmp/reports")
    charts_dir = Path("/tmp/reports/charts")
    reports_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    from wrangler.reporting import reporter
    original_reports = reporter.REPORTS_DIR
    original_charts = reporter.CHARTS_DIR
    try:
        reporter.REPORTS_DIR = str(reports_dir)
        reporter.CHARTS_DIR = str(charts_dir)
        experiment_name = manifest.get("name", run_id)
        generate_report(results, experiment_name, use_paperbanana=True)
    finally:
        reporter.REPORTS_DIR = original_reports
        reporter.CHARTS_DIR = original_charts

    summary_data = {
        "run_id": run_id,
        "experiment": manifest.get("name", ""),
        "pairs": {},
        "totals": {
            "input_cost_usd": total_input_cost,
            "output_cost_usd": total_output_cost,
            "total_cost_usd": total_input_cost + total_output_cost,
            "total_elapsed_seconds": total_elapsed,
        },
    }
    for pair_id, data in results.items():
        if pair_id.startswith("_"):
            continue
        before_avg = sum(data["before"].values()) / max(len(data["before"]), 1) if data["before"] else 0
        after_avg = sum(data["after"].values()) / max(len(data["after"]), 1) if data["after"] else 0
        summary_data["pairs"][pair_id] = {
            "model": data["model"],
            "before_avg": round(before_avg, 4),
            "after_avg": round(after_avg, 4),
            "delta": round(after_avg - before_avg, 4),
        }

    gcs_bucket = gcs.bucket(bucket_name)
    for local_file in reports_dir.rglob("*"):
        if local_file.is_file():
            rel = local_file.relative_to(reports_dir)
            blob_path = f"pipeline-runs/{run_id}/reports/{rel}"
            gcs_bucket.blob(blob_path).upload_from_filename(str(local_file))

    summary_blob = f"pipeline-runs/{run_id}/reports/summary.json"
    gcs_bucket.blob(summary_blob).upload_from_string(
        json.dumps(summary_data, indent=2), content_type="application/json",
    )

    for pair_id, pair_summary in summary_data["pairs"].items():
        metrics.log_metric(f"{pair_id}_before_avg", pair_summary["before_avg"])
        metrics.log_metric(f"{pair_id}_after_avg", pair_summary["after_avg"])
        metrics.log_metric(f"{pair_id}_delta", pair_summary["delta"])
    metrics.log_metric("total_cost_usd", round(total_input_cost + total_output_cost, 4))

    m, s = divmod(int(total_elapsed), 60)
    with open(summary.path, "w") as f:
        f.write("## Analysis Summary\n\n")
        f.write("| Pair | Before Avg | After Avg | Delta | Cost (in/out) |\n")
        f.write("|------|-----------|----------|-------|---------------|\n")
        for pair_id, ps in summary_data["pairs"].items():
            eval_b = _read_stage("eval_before", pair_id)
            eval_a = _read_stage("eval_after", pair_id)
            opt = _read_stage("optimize", pair_id)
            pair_in = sum(d.get("costs", {}).get("input_usd", 0) for d in [eval_b, opt, eval_a])
            pair_out = sum(d.get("costs", {}).get("output_usd", 0) for d in [eval_b, opt, eval_a])
            f.write(f"| {pair_id} | {ps['before_avg']:.4f} | {ps['after_avg']:.4f} | {ps['delta']:+.4f} | ${pair_in:.3f} / ${pair_out:.3f} |\n")
        f.write(f"\n**Total cost**: ${total_input_cost + total_output_cost:.4f} | ")
        f.write(f"**Total time**: {m}m {s:02d}s\n")
        f.write(f"\n**Reports**: `gs://{bucket_name}/pipeline-runs/{run_id}/reports/`\n")

    logging.info(f"Analysis complete. Reports at gs://{bucket_name}/pipeline-runs/{run_id}/reports/")
    return json.dumps(summary_data)
