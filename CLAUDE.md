# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
uv sync                          # Install dependencies
uv run pytest tests/ -v           # Run all 284 tests
uv run pytest tests/test_config.py -v  # Run single test file
uv run pytest tests/test_config.py::TestResolveModel -v  # Run single test class
uv run wrangler --help            # CLI entry point
```

## Project Overview

GEPA Prompt Wrangler optimizes ADK agent system prompts using Google's GEPA (Genetic Evolutionary Prompt Algorithm). It deploys agents to GEAP (Gemini Enterprise Agent Platform / Agent Engine), evaluates them against eval datasets, runs GEPA optimization, redeploys with optimized prompts, and generates comparative reports.

## Architecture

### Package Structure (`wrangler/`)

Six subpackages organized by domain:

- **`core/`** — Config, manifest parsing, eval format conversion, agent deployment. Everything else depends on these.
- **`eval/`** — Batch evaluation via Vertex AI Evaluation Service, online evaluators (OTel trace scoring), online monitors (health checks).
- **`optimize/`** — GEPA optimizer wrapper with ADK patches, multi-judge ensemble.
- **`reporting/`** — Chart generation (matplotlib + PaperBanana), markdown reports, per-pair analysis.
- **`orchestration/`** — Experiment management (DOE campaigns), stage functions, legacy pipeline runner.
- **`tools/`** — Agent introspection, prompt versioning, synthetic traffic generation.
- **`pipeline/`** — Vertex AI Pipeline (KFP v2) components, DAG definition, Cloud Build + submission.

### Key Data Flow

```
Manifest YAML → Deploy → Eval Before → Optimize (GEPA) → Redeploy → Eval After → Report
```

Local workflow: `wrangler run manifest.yaml` (orchestration/stages.py)
Pipeline workflow: `wrangler pipeline run manifest.yaml` (pipeline/deploy_pipeline.py)

### Model Resolution

`core/config.py:resolve_model()` routes models to the correct ADK class:
- Gemini 2.x → plain string (regional endpoint)
- Gemini 3.x → `Gemini()` from `google.adk.models.google_llm`
- Claude → `Claude()` from `google.adk.models.anthropic_llm`

All non-2.x models use `GOOGLE_CLOUD_LOCATION=global`.

### ADK Patches

`optimize/optimizer.py:_patch_adk()` applies 5 monkey-patches to ADK internals required for GEPA to work. These compensate for ADK bugs (tracked in github.com/google/adk-python issues #5906, #6071, #6072). Do NOT remove patches without testing each one individually — ADK 2.2.0 still requires all of them.

### Pipeline Architecture

KFP v2 components in `pipeline/components.py` are self-contained (KFP serializes each function in isolation). Code is injected via GCS tarball, env vars from Secret Manager. Local MCP servers start inside the optimize container for reliable tool connections. The DAG in `pipeline/dag.py` uses `dsl.ParallelFor` with `parallelism=1` for rate-limited stages.

Pre-built Docker image via Cloud Build, cached by `pyproject.toml` hash. Image tag = `md5(pyproject.toml)[:12]`.

### GEPA Metrics (ADK 2.x)

Registered in ADK metric evaluator registry (usable by GEPA optimizer):
- `hallucinations_v1` (plural), `safety_v1`, `rubric_based_final_response_quality_v1`, `rubric_based_tool_use_quality_v1`

NOT registered (will cause NotFoundError if used in sampler_config.json):
- `instruction_following_v1`, `hallucination_v1` (singular), `final_response_match_v2`

Batch eval metrics (server-side, usable in eval_before/eval_after):
- `final_response_quality`, `hallucination`, `safety`, `tool_use_quality`, `instruction_following`

### GCP Labels

All GCP resources (agents, eval runs, pipelines, Artifact Registry) use label `{"solution": "promp-wrangler"}`.

## Environment Variables

Required in `.env`:
- `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_STAGING_BUCKET`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`
- `GOOGLE_GENAI_USE_VERTEXAI=1`

For multi-model agents: `SEARCH_MCP_SERVER`, `BOOKING_MCP_SERVER`, `EXPENSE_MCP_SERVER` (+ corresponding `_URL` variants for direct Cloud Run access).

## Important Conventions

- **Use `uv`, never bare `pip`** for all package management.
- **Use PaperBanana** for charts/visualizations, not raw matplotlib.
- **Run evals sequentially** (one pair at a time) to avoid 429 rate limit errors.
- **Sampler configs** in `agents/*_opt/sampler_config.json` override experiment thresholds — keep them in sync with `_build_criteria()` in optimizer.py. GEPA loads sampler_config.json directly, bypassing code defaults.
- Agent `__init__.py` files must use absolute imports (e.g., `from agents.example_agent.agent import ...`) for cloudpickle compatibility with GEAP deployment.

## Cloudpickle & GEAP Deployment

GEAP uses cloudpickle to serialize agents. The pickle captures module paths — if the agent is imported as `agents.example_agent`, GEAP needs that exact module path on its server. Solutions:
- Add `agents/` parent dir to `sys.path` and import as top-level module: `sys.path.insert(0, "agents"); mod = __import__("example_agent")`
- For redeploy: download the existing pickle from GCS, modify `agent._tmpl_attrs['agent'].instruction` in-place, re-upload, and trigger redeploy with `agent_engines.update(agent=None, config={...})`. This avoids re-pickling entirely.

## Pipeline Pitfalls (Learned the Hard Way)

### KFP Component Isolation
`@dsl.component` functions are serialized in isolation. **No module-level helper functions** — everything must be defined inline within the component body. Module-level functions defined in `components.py` are NOT available at runtime.

### Secret Manager & API Keys
The Secret Manager payload may contain `GOOGLE_API_KEY` which overrides Vertex AI ADC. After loading secrets with `load_dotenv(override=True)`, ALWAYS:
```python
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
```

### MCP Tools in Pipeline Containers
Cloud Run MCP servers drop idle HTTP connections within ~2 minutes — too short for GEPA's inter-generation gaps. The optimize component starts **local FastMCP servers** on localhost (ports 8001-8003) from the code in `examples/multi_model_agents/mcp_servers/`. MCP URLs are overridden to `http://localhost:{port}/mcp`. Per-generation session refresh closes and re-warms sessions between GEPA generations (~0.1s overhead).

### Pipeline Caching
`run_id` is deterministic (hash of manifest name + agent module + eval data + pair IDs). `job_id` gets a timestamp suffix for uniqueness. Same manifest → same `run_id` → cached steps reused. Changing the manifest busts the cache (correct behavior). If you need to force a fresh run, pass `--run-id` explicitly.

### Tarball Packaging
`deploy_pipeline.py` packages the **full project tree** using an exclude-list (`.venv`, `.git`, `__pycache__`, `outputs`, `experiments`). Missing directories have caused multiple pipeline failures. If you add new directories the agents depend on, they'll be included automatically.

### Docker Image
Pre-built via Cloud Build, tagged by `md5(pyproject.toml)[:12]`. Adding any dependency to `pyproject.toml` or `Dockerfile.pipeline` triggers a rebuild (~3 min). The image must include `fastmcp>=2.0.0` for local MCP servers.

### reporter.REPORTS_DIR / CHARTS_DIR
Must be `Path` objects, not strings. The reporter calls `.mkdir()` on them. When overriding in pipeline components, pass `Path(...)` not `str(...)`.

### Eval Data Cleaning
Rows with NaN/None/empty responses must be dropped from inference results before calling `create_evaluation_run()`. The SDK validates `agent_data` types and throws `ValueError` on invalid rows.

### HTTPX Client Factory
The MCP session manager passes `headers`, `timeout`, and other kwargs to `httpx_client_factory`. The factory must accept `**kwargs` and pop conflicting keys (`timeout`, `limits`) before passing to `httpx.AsyncClient()`.

### Two config.py Files
`wrangler/core/config.py` and `examples/multi_model_agents/config.py` both have `resolve_model()`. Keep them in sync — the multi-model agents import from their local config, not wrangler's.

### deploy.py REQUIREMENTS
The `REQUIREMENTS` list in `wrangler/core/deploy.py` is sent to GEAP for agent deployment. It must match the ADK version in `pyproject.toml` or agents will fail to start with import errors.

## Testing Manifests

- **Smoke test** (`manifests/pipeline_smoke_manifest.yaml`): 5 eval cases, ~25-30 min total. Use for pipeline infrastructure validation.
- **Full test** (`manifests/pipeline_test_manifest.yaml`): 64 eval cases, ~2-3 hours. Use for production optimization runs.

## Pipeline Debugging

Use the `inspect-vai-pipes` skill for systematic debugging. Key commands:
```python
# Get task-level status
job = aiplatform.PipelineJob.get(resource_name='JOB_ID')
for task in job.gca_resource.job_detail.task_details: ...

# Get worker logs (job_id from error URL)
gcloud logging read 'resource.type="ml_job" AND resource.labels.job_id="JOB_ID"' --project=PROJECT

# Check GEAP Reasoning Engine logs
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.reasoning_engine_id="ENGINE_ID"'
```

Known failure patterns are cataloged in `~/.claude/skills/inspect-vai-pipes/references/known-failures.md`.
