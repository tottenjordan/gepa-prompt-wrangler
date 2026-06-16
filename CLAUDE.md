# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
uv sync                          # Install dependencies
uv run pytest tests/ -v           # Run all 316 tests
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

**Deploy/redeploy components** use source-based deployment (`deploy_agent_from_source` / `update_agent_from_source`). The pipeline container assembles a build package at `/app/_geap_build_pkg/`, the SDK tarballs and uploads it, and GEAP builds the agent container from source. No cloudpickle involved.

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
- Agent `__init__.py` files must use absolute imports (e.g., `from agents.example_agent.agent import ...`) for GEAP deployment compatibility.

## Source-Based GEAP Deployment

Agents deploy via `source_packages` — no cloudpickle serialization. This replaced the pickle-based approach which failed because cloudpickle captures module references (`registry.py`, `config.py`, `prompts/`) that don't exist on the GEAP server.

### How it works

`deploy_agent_from_source()` in `wrangler/core/deploy.py`:
1. Calls `build_source_package()` to assemble a self-contained build directory (`_geap_build_pkg/`) containing:
   - `app.py` — generated entrypoint that creates an `LlmAgent` + wraps in `AdkApp`
   - `config.py` — copied from agent's parent dir (model resolution, env vars)
   - `registry.py` — generated (not copied); uses direct Cloud Run URLs with GoogleAuth for invoker auth
   - `prompts/` — copied from agent's parent dir
   - `instruction.txt` — the system prompt (swapped during redeploy)
   - `requirements.txt` — pip deps for the GEAP server
   - `__init__.py` — makes it a Python package
2. Passes `source_packages=["_geap_build_pkg"]` (relative path) to the SDK
3. SDK creates a base64-encoded tarball and sends it to the Agent Engine API
4. GEAP extracts to `/code/`, installs requirements, imports `_geap_build_pkg.app`, starts serving

For redeploy: `update_agent_from_source()` rebuilds the package with the new instruction and calls `agent_engines.update()`. No pickle manipulation needed.

### Critical constraints (learned the hard way)

1. **Build dir must be at project root** — the SDK's `_create_base64_encoded_tarball` validates paths are under `os.getcwd()`. Using `/tmp/` fails. The build dir is created at `Path.cwd() / "_geap_build_pkg"`.

2. **`source_packages` must use relative paths** — `tar.add(file)` preserves the path structure. An absolute path like `/app/_geap_build_pkg` creates a broken archive. Convert with `Path(build_dir).relative_to(Path.cwd())`.

3. **`requirements_file` must be explicit** — GEAP defaults to `requirements.txt` at the tarball root, not inside a package subdirectory. Set `requirements_file: "_geap_build_pkg/requirements.txt"` to point into the package.

4. **`config.py` lives alongside `agents/`, not inside it** — the agent files are at `multi_model_agents/agents/opus_agent.py` but `config.py` and `registry.py` are at `multi_model_agents/`. `build_source_package()` walks up the directory tree to find the dir containing `config.py`.

5. **Deployed agents use direct Cloud Run URLs with GoogleAuth** — the build package gets a generated `registry.py` that uses `McpToolset` with `httpx.AsyncClient(auth=GoogleAuth())`. The GEAP service account provides ADC credentials for Cloud Run invoker auth. Timeouts set to 60s connect / 180s read for cold starts. Requires both `*_MCP_SERVER` and `*_MCP_URL` env vars.

6. **`config.py` MCP vars rewritten to safe defaults** — the original has `os.environ["SEARCH_MCP_SERVER"]` (crashes if unset). `build_source_package()` rewrites these to `os.environ.get("SEARCH_MCP_SERVER", "")` so the module loads cleanly. Actual values come via the `env_vars` config dict.

7. **Cloud Run MCP services need IAM invoker for GEAP** — the GEAP service account (`service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) must have `roles/run.invoker` on each Cloud Run MCP service, or the deployed agent's tool calls will timeout silently. Grant with:
   ```bash
   SA="service-934903580331@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
   for SVC in wrangler-search-mcp wrangler-booking-mcp wrangler-expense-mcp; do
     gcloud run services add-iam-policy-binding $SVC \
       --region=us-central1 --project=hybrid-vertex \
       --member="serviceAccount:$SA" --role="roles/run.invoker" --quiet
   done
   ```

### Config dict shape

```python
config = {
    "source_packages": ["_geap_build_pkg"],              # relative path
    "requirements_file": "_geap_build_pkg/requirements.txt",
    "entrypoint_module": "_geap_build_pkg.app",           # package.module
    "entrypoint_object": "app",                           # AdkApp instance
    "class_methods": _ADK_CLASS_METHODS,                  # 13 standard ADK operations
    "agent_framework": "google-adk",
    "display_name": "gepa-opus47",
    "env_vars": {"SEARCH_MCP_SERVER": "...", ...},
}
```

### Legacy functions

`deploy_agent()` and `update_agent()` (pickle-based) still exist in `deploy.py` but are not used by the pipeline or local workflow. They remain for backward compatibility only.

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
KFP caches each component independently based on: **(1) component function body hash** and **(2) input parameter values**. Both must match for a cache hit.

**What this means in practice:**
- `run_id` is deterministic (hash of manifest name + agent module + eval data + pair IDs). Same manifest → same `run_id` → same input values.
- `job_id` gets a timestamp suffix so Vertex AI accepts resubmissions.
- **Changing component code** (even one line in `components.py`) invalidates the cache for THAT component — but OTHER unchanged components still cache. KFP hashes each function body independently.
- **Changing input parameters** (new `run_id`, different manifest) invalidates cache for all components that receive the changed parameter.
- If you only change the `generate_analysis` component, the earlier steps (archive, deploy, eval_before, optimize, redeploy, eval_after) will all cache and the pipeline skips straight to analysis.

**Verified behavior:** After changing optimize + analysis component code but not deploy/eval code, archive/deploy/eval_before cached correctly and the pipeline started directly at optimize.

### Tarball Packaging
`deploy_pipeline.py` packages the **full project tree** using an exclude-list (`.venv`, `.git`, `__pycache__`, `outputs`, `experiments`, `_geap_build_pkg`). Missing directories have caused multiple pipeline failures. If you add new directories the agents depend on, they'll be included automatically. The `_geap_build_pkg` directory is excluded because it's a transient build artifact created during deployment.

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

### deploy.py Requirements Lists
`wrangler/core/deploy.py` has two requirements lists:
- `REQUIREMENTS` — legacy pickle-based deployment (includes `cloudpickle`). Not used by current workflow.
- `_SOURCE_REQUIREMENTS` — source-based deployment. Written into `_geap_build_pkg/requirements.txt` and installed by GEAP. Must match the ADK version in `pyproject.toml` or agents will fail to start with import errors.

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
