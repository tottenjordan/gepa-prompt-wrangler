# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code Standards — Read First

**Always refer to [CODE_STANDARDS.md](CODE_STANDARDS.md) before writing code or making
environment changes.** It is the authoritative source for tooling (`uv`, `ruff`, `ty`,
`pytest`), commit conventions, dependency management, and secret handling. This file
(CLAUDE.md) covers architecture and domain specifics; CODE_STANDARDS.md covers *how* we
write and ship the code.

Session notes and known traps live in [docs/notes/README.md](docs/notes/README.md).

## Build & Test Commands

```bash
uv sync                          # Install dependencies
uv run pytest tests/ -v           # Run the full suite (count intentionally not quoted here — it goes stale)
uv run pytest tests/test_config.py -v  # Run single test file
uv run pytest tests/test_config.py::TestResolveModel -v  # Run single test class
uv run wrangler --help            # CLI entry point
uv run wrangler preflight         # Resolve both dependency sets before a campaign
uv run wrangler engines list      # Engine inventory with per-engine disposition
uv run wrangler evaluators --help # Online (trace-scoring) evaluators — 7 commands
```

`wrangler evaluators trace-health` is the diagnostic for the OTel span-drop
failure in [docs/notes/silent-failures.md](docs/notes/silent-failures.md) #8 and
exits non-zero when an engine drops batches, so it can gate a run.

## Project Overview

GEPA Prompt Wrangler optimizes ADK agent system prompts using Google's GEPA (Genetic Evolutionary Prompt Algorithm). It deploys agents to GEAP (Gemini Enterprise Agent Platform / Agent Engine), evaluates them against eval datasets, runs GEPA optimization, redeploys with optimized prompts, and generates comparative reports.

## Architecture

### Package Structure (`wrangler/`)

Seven subpackages organized by domain:

- **`core/`** — Model registry, config, manifest parsing, eval format conversion, agent deployment. Everything else depends on these.
- **`eval/`** — Batch evaluation via Vertex AI Evaluation Service, online evaluators (OTel trace scoring), online monitors (health checks).
- **`optimize/`** — GEPA optimizer wrapper with ADK patches, multi-judge ensemble.
- **`reporting/`** — Chart generation (matplotlib + PaperBanana), markdown reports, per-pair analysis.
- **`orchestration/`** — Experiment management (DOE campaigns), stage functions, legacy pipeline runner.
- **`tools/`** — Agent introspection, prompt versioning, synthetic traffic generation,
  engine inventory/reaping (`engines.py`), deploy-health probing (`boot_probe.py`),
  and the dependency-resolution pre-flight (`preflight.py`).
- **`pipeline/`** — Vertex AI Pipeline (KFP v2) components, DAG definition, Cloud Build + submission.

### Key Data Flow

```
Manifest YAML → Deploy → Eval Before → Optimize (GEPA) → Redeploy → Eval After → Report
```

Local workflow: `wrangler run manifest.yaml` (orchestration/stages.py)
Pipeline workflow: `wrangler pipeline run manifest.yaml` (pipeline/deploy_pipeline.py)

### Model Registry

**`wrangler/core/models.py` is the single source of truth for every model id.** It holds
a `ModelSpec` per model — provider, cost per 1M input/output tokens, requests-per-minute
limit, retirement date, whether the model accepts sampling parameters, and a short alias
— plus the named-role defaults (`DEFAULT_JUDGE_MODEL`, `DEFAULT_AGENT_MODEL`,
`DEFAULT_JUDGE_ENSEMBLE`, and the two scaffold judges). `PROVIDERS`, `MODEL_MAP`, and
`AGENT_ORDER` are derived from it, not hand-written.

Rules the test suite enforces:

- **No model id literals anywhere in `wrangler/` outside the registry.** `tests/test_models.py`
  walks the AST of every module — docstrings and comments are exempt, so prose may name a
  model but code may not. `LITERAL_EXCEPTIONS` is **empty, and worth keeping empty**.
  It used to exempt `pipeline/components.py` on the grounds that KFP serialization stops a
  component importing the registry at runtime; that was checked on 2026-09-01 and is false.
  Every component extracts the tarball and calls `sys.path.insert(0, "/app")` first, then
  imports from `wrangler` freely. The real isolation rule is narrower — a component cannot
  call a *module-level helper* defined in `components.py`, because only the function body
  is serialized — and it has no bearing on importing an unpacked package.
- **Every named-role default must be a registered model**, must be listed in the guard's
  own `DEFAULT_ROLES` table, and must be more than 30 days from its retirement date. The
  last one turns a vendor shutdown into a red build instead of a 404 mid-run.
  `DEFAULT_FIGURE_IMAGE_MODEL` is the one role exempt from *registration* (PaperBanana
  draws with it; nothing infers against it, so it has no cost, RPM or retirement date). It
  is listed in `ROLES_EXEMPT_FROM_REGISTRATION` with a reason, stays in `DEFAULT_ROLES` so
  the completeness check still sees it, and a test fails if it ever gains a `ModelSpec`
  without the exemption being removed.

Retirement dates are the *earliest announced* shutdown. Anthropic's are "not sooner than"
and apply to Anthropic-operated platforms — Google Cloud sets its own schedule for partner
models, so treat them as an early-warning floor.

`supports_sampling_params=False` marks Claude Opus 4.7 and later (plus Sonnet 5 and
Fable 5), which return a 400 for a non-default `temperature`/`top_p`/`top_k`. The cutoff
is the model generation, not the Opus tier. `PairFactory.load()` rejects a manifest that
sets a temperature on one of these, naming the pair — steer those models through the
system prompt instead.

### Model Resolution

`core/models.py:resolve_model()` (re-exported from `core/config.py`) routes models to the
correct ADK class:
- Gemini 2.x → plain string (regional endpoint)
- Gemini 3.x → `Gemini()` from `google.adk.models.google_llm`
- Claude → `Claude()` from `google.adk.models.anthropic_llm`

**The location rule.** `core/models.py:model_location()` is the single source of truth:

| Model family | Location | Endpoint host |
| --- | --- | --- |
| Gemini 2.x (and `models/…`) | `GCP_REGION`, e.g. `us-central1` | `us-central1-aiplatform.googleapis.com` |
| Gemini 3.x | `global` | `aiplatform.googleapis.com` |
| Anthropic / Claude (all versions) | `global` | `aiplatform.googleapis.com` |

Gemini 3.x and Claude are **not servable from a region**. Asking for one fails with
`Publisher Model .../locations/us-central1/publishers/anthropic/models/claude-sonnet-4-6
is not servable in region us-central1`.

**Do not drive this off `GOOGLE_CLOUD_LOCATION`.** That variable is process-wide, but one
process routes across five tiers at once (lite/flash/pro are Gemini 3.x, sonnet/opus are
Claude), so no single value is right for all of them — and GEAP treats it as a restricted
env var and can serve it back regionally regardless of the deployment config. Instead
`resolve_model()` pins the location *into each model object*:

- Claude gets a full resource name, `projects/{p}/locations/global/publishers/anthropic/models/{id}`.
  ADK's `Claude._anthropic_client` parses project and location out of that path and ignores
  the env var entirely.
- Gemini 3.x gets `client_kwargs={"vertexai": True, "project": …, "location": "global"}`,
  forwarded verbatim to `google.genai.Client`, which derives its endpoint host from it.

`GOOGLE_CLOUD_LOCATION=global` stays in `.env` as the fallback for code paths that bypass
`resolve_model()`. Setting it to `${GCP_REGION}` breaks every Claude and Gemini 3.x agent.

Both `wrangler/core/models.py` and `examples/multi_model_agents/config.py` implement this —
keep them in sync (see "Two config.py Files" below).

Note `resolve_model()` returns an ADK model *object* for everything except Gemini 2.x.
Anywhere a plain id string is needed — `deploy_agent_from_source(model=...)`, the build
package — read it from config or the registry, not off a constructed agent.

### The Vertex SDK surface: `agentplatform`, not `vertexai.Client`

`google-cloud-aiplatform` 2.1.0 deprecates `vertexai.Client`. **All client-side
code goes through `wrangler/core/clients.py:agent_client()`**, which returns an
`agentplatform.Client`; `tests/test_clients.py` fails (AST-based) if any module
constructs `vertexai.Client` again, in either spelling.

`agentplatform` is **not a separate PyPI package** — it ships vendored inside
`google-cloud-aiplatform`, so `importlib.metadata.version("agentplatform")`
raises while `import agentplatform` succeeds. Do not use the former to test for it.

- **Agent Engine CRUD is `client.runtimes`**, not `client.agent_engines`.
  `AgentRuntimeConfig` is field-identical to `AgentEngineConfig` (measured), and
  `runtimes.get()` binds the same ADK methods, so the move was a rename in
  practice. `get`/`delete` are **keyword-only** (`name=...`).
- **The module-level `vertexai.agent_engines.create()` changed shape** at 2.1.0
  and no longer accepts `source_packages`, `class_methods`, `labels` and four
  others. Nothing here uses it; do not reintroduce it.
- **Mixing the two packages' objects type-checks fine and fails at runtime.**
  Twice on 2026-09-08: patching `vertexai._genai._evals_common` while the client
  called `agentplatform`'s made `EVAL_MAX_RETRIES` a silent no-op, and building
  `types.evals.SessionInput` from `vertexai` made `run_inference` reject every
  case. **The mocked unit suite stayed green through both**, so the guards in
  `tests/test_sdk_private_surface.py` are structural.
- **Still on `vertexai`, deliberately:** the generated build package's
  `from vertexai.agent_engines import AdkApp`. It runs on the GEAP builder, emits
  no warning, and is where a mistake costs a campaign.

Full detail: [docs/notes/vertex-sdk-surfaces.md](docs/notes/vertex-sdk-surfaces.md).

**ADK pins `mcp>=1.24,<2`** on its `mcp` extra — the extra that provides
`McpToolset`. That cap is why `fastmcp` is held at 3.4.7 (4.x moves to the mcp
2.x protocol) and why `google-cloud-aiplatform` 2.x was reachable at all: its
`<2` pin lives on the `gcp`/`all`/`test` extras we do not install. Revisit
fastmcp when ADK ships mcp 2.x support, not before.

### ADK Patches

`optimize/optimizer.py:_patch_adk()` applies 5 monkey-patches to ADK internals required for GEPA to work. Patches 1–3 compensate for ADK bugs (github.com/google/adk-python issues #5906, #6071); patch 4 is local instrumentation; patch 6 pins the safety metric version. All the bug workarounds are still required at ADK 2.8.0 even though their issues are closed — the fixes are not in the release. Re-probed 2026-09-08 on the 2.7.1 → 2.8.0 bump: all five unchanged.

**Patch 6 (added 2026-08-20)** — `SafetyEvaluatorV1` hands the eval facade the *unversioned* `PrebuiltMetric.SAFETY`, which the Vertex SDK resolves client-side to `safety_v3`; us-central1 does not serve v3, so every GEPA case returned `400 Unsupported predefined metric: safety_v3`, the score came back `None`, and patch 4 coerced it to `0.0`. GEPA kept running and optimized against a criterion pinned at zero. The version is chosen inside ADK — `sampler_config.json` correctly says `safety_v1` and cannot influence it.

**Patch 5 was removed on 2026-08-20.** It overrode `rubric_based_evaluator._normalize_text` and `convert_auto_rater_response_to_score`. ADK 2.7.1 fixed issue #6072 and went further, adding `rubric_id`-based verdict matching and an empty-response guard; the override, written against ADK 2.2, did text-only matching and silently discarded both, corrupting the rubric scores GEPA optimizes against. A redundant patch is not harmless.

Do NOT remove or add patches without re-running the per-patch probe in [docs/notes/adk-patch-status.md](docs/notes/adk-patch-status.md) against the installed ADK.

**Re-probe when the Vertex SDK moves too, not only ADK.** Patch 6 depends on the
*SDK* resolving an unversioned metric name through
`vertexai._genai._evals_constant.METRIC_LATEST_SPEC_NAME`, so a
`google-cloud-aiplatform` major can invalidate a patch while ADK sits still.
Re-probed on the 1.165.1 → 2.1.0 bump: unchanged, `safety` still maps to
`safety_v3`, all five patches still required.

### Pipeline Architecture

KFP v2 components in `pipeline/components.py` are self-contained (KFP serializes each function in isolation). Code is injected via GCS tarball, env vars from Secret Manager. Local MCP servers start inside the optimize container for reliable tool connections. The DAG in `pipeline/dag.py` uses `dsl.ParallelFor` with `parallelism=1` for rate-limited stages.

Pre-built Docker image via Cloud Build, cached by a dependency hash. Image tag = `md5(pyproject.toml + uv.lock + Dockerfile.pipeline)[:12]`.

**Deploy/redeploy components** use source-based deployment (`deploy_agent_from_source` / `update_agent_from_source`). The pipeline container assembles a build package at `/app/_geap_build_pkg/`, the SDK tarballs and uploads it, and GEAP builds the agent container from source. No cloudpickle involved.

### GEPA Metrics (ADK 2.x)

Registered in ADK metric evaluator registry (usable by GEPA optimizer):
- `hallucinations_v1` (plural), `safety_v1`, `rubric_based_final_response_quality_v1`, `rubric_based_tool_use_quality_v1`

NOT registered (will cause NotFoundError if used in sampler_config.json):
- `instruction_following_v1`, `hallucination_v1` (singular), `final_response_match_v2`

Batch eval metrics (server-side, usable in eval_before/eval_after):
- `final_response_quality`, `hallucination`, `safety`, `tool_use_quality`, `instruction_following`

**`tool_use_quality` floor — DO NOT use the predefined metric for tool-using agents.** The predefined `tool_use_quality_v1` (`types.RubricMetric.TOOL_USE_QUALITY`) is **reference-free** and **auto-generates its rubrics server-side blind to the agent's available tools**. For a correctly tool-using agent it produces *inverted* rubrics (`NO_TOOL_CALL_AS_EXPECTED`, `INFORMS_USER_OF_INABILITY`) that penalize calling tools, so only the INTENT rubric passes and the score caps near ~0.33–0.42 even when the agent calls the right tools with the right args. This is a metric artifact, NOT agent misbehavior and NOT a trajectory-capture bug (live-diagnosed against engine `6075838033171578880`; trajectory IS captured). GEPA optimization is unaffected (sampler configs use explicit rubrics) — only batch-eval reports were floored.

Fix (in `wrangler/eval/evaluator.py:_tool_use_metric()`): use a **custom `types.LLMMetric`** with an explicit `prompt_template` that rewards correct tool selection + correct parameters and explicitly does NOT penalize tool use, requiring strict JSON `{"explanation", "score"}` output. Constraints learned the hard way: (1) the metric name must NOT be `tool_use_quality_v1` — that exact name is hijacked by the SDK's `PredefinedMetricHandler`, ignoring the custom prompt; use `tool_use_quality` (a non-predefined name routes to `LLMMetricHandler`). (2) Omit `judge_model` — a bare model id is rejected as an invalid autorater resource; the default autorater works. (3) The score key is aliased `tool_use_quality` → `tool_use_quality_v1` via `_alias_tool_use_key()` so downstream report consumers are unchanged. After the fix, eval-before `tool_use_quality_v1` went 0.42 → 1.00 on the same engine. Prefilling rubrics or passing `metric_spec_parameters` does NOT work — the predefined handler ignores both.

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
- **Sampler configs** in `agents/*_opt/sampler_config.json` are the **single source of truth** for GEPA criteria and thresholds. When a sampler_config.json exists it is used verbatim — experiment/manifest thresholds do NOT override it. To tune what GEPA optimizes against, edit the sampler_config.json. The `eval_thresholds` flowing from manifests only (a) seed the fallback `_build_criteria()` when no sampler_config.json exists, and (b) drive report pass/fail marking — keep them in sync with the sampler config for accurate reports.
- Agent `__init__.py` files must use absolute imports (e.g., `from agents.example_agent.agent import ...`) for GEAP deployment compatibility.
- **Do not pin Agent Engine deployment ids** — no hardcoded ids in source, and nothing may *require* `*_ENGINE_ID` to be present in `.env`. An id names one deployment; whether a change means update, redeploy, or a brand-new engine is decided ad hoc at the time. Engine ids arrive at the call site (`--engine-id`, manifest `engine_id`, an env var read where it is used) and a missing one should skip or fail clearly, never fall back to a checked-in default. The example scripts write ids into `.env` as scratch space for their own `--update` flow; that is convenience, not configuration.
- **Reap the engines you deploy.** Because ids are never pinned (above), nothing in the
  repo names a deployment and nothing reaps it — the project reached **80 engines** before
  anyone counted, 61 of them holding warm instances. `wrangler engines list` shows the
  inventory with the evidence behind each disposition; `wrangler engines prune` deletes
  only what every signal agrees on and is dry-run by default. Deploy scratch engines with
  `labels={"lifecycle": "ephemeral", "campaign": "<id>"}` so they can be found later, and
  treat teardown as the last step of a campaign rather than a separate chore. The policy,
  and why an age-based sweep would have deleted someone else's live work, is in
  [docs/notes/engine-lifecycle.md](docs/notes/engine-lifecycle.md).

- **A manifest pair can be switched off with `enabled: false`.** Deleting a pair loses its
  model id, agent module and the reason; commenting it out loses the reason and rots. A
  disabled pair stays parsed and carries a `disabled_reason`. A sweep skips it and prints
  why; naming it explicitly (`--pair opus`) still runs it, because that is a deliberate act.
  Read `manifest.enabled_pairs`, never `manifest.pairs`, when choosing what to run — the
  local path filtered and the pipeline path did not, so a disabled pair still ran there.
  **The opus tier is currently disabled** across every manifest: 15 gated deploys across
  three model versions and two prompts produced nothing above 50% reach against a concurrent
  control of four tiers at 93–100%, so evals against it measure dropout rather than the
  prompt ([docs/analysis/2026-09-01-opus-serving-failure.md](docs/analysis/2026-09-01-opus-serving-failure.md)).

- **A fresh deploy is health-gated, and it is on by default.** Roughly four in ten
  deployments come up unable to serve, failing by returning 200 with no inference — so an
  ungated deploy hands the eval an engine that silently drops a third of its cases, and the
  resulting delta measures dropout rather than the prompt. `stage_deploy` probes each new
  engine (~60 one-line requests, ~12 min per pair) and redeploys while it is below 80%
  reach, because redeploying redraws the rate. Tune or disable with a `health_gate:` block
  in the manifest; the verdict is written to the deploy stage under `health`, and the
  recorded engine id is the post-reroll one.

- **Every optimization sweep carries a control arm whose prompt does not change.** Run
  `eval_before` and `eval_after` against the *same* prompt, with no optimize stage between
  them, alongside the real arms and under identical conditions. Whatever that arm's deltas
  come out to **is the noise floor**, and no result from the sweep may be reported as an
  improvement unless it exceeds it.

  This is not a formality. On 2026-08-22 the first real sweep produced +0.039 on response
  quality and +0.035 on safety from an arm whose prompt was **byte-identical** before and
  after — pure measurement noise, driven by the two sides scoring different case subsets
  (see [docs/notes/silent-failures.md](docs/notes/silent-failures.md) #5). Without that
  arm, three-arm agreement on those two metrics would have read as a clean win, and a
  +0.034 gain that is actually indistinguishable from nothing would have been promoted.
  That control existed only by accident: GEPA happened to return the seed for one model.

  **Run the control first, as a gate**, and run it at the same `num_runs` as the real
  arms. **Measured 2026-09-08 by campaign 06** (four arms, both publishers, 100%
  coverage on every side): the floor is **~0.058 at `num_runs: 1` and ~0.011-0.014 at
  the configured default of 3**. Per metric it ranges 0.017 (hallucination) to 0.058
  (safety), a 3.4x spread, which is why `classify_deltas` takes a per-metric mapping —
  holding every metric to the loosest one throws away most of the resolution.

  **Averaging beats sqrt(n).** Claude fell 5.2x from n=1 to n=3 and Gemini 2.7x, against
  the 1.73x sqrt(3) predicts, replicated independently across publishers. So `num_runs`
  is a *stronger* lever than previously documented, not a weaker one. Report the range;
  two levels cannot distinguish sqrt(n) from any other decreasing curve, and the two
  arms disagree on magnitude.

  These supersede the 2026-08-23 figures of ~0.059 and ~0.034. The n=1 end was about
  right; the n=3 end was 2.4-3x too pessimistic, because it was measured through dropout
  that `EVAL_MAX_RETRIES` has since removed.

  **Pairing on case index no longer helps materially.** It was worth ~15% when evals
  dropped cases; at 100% coverage there are no unmatched cases for it to remove, and at
  n=3 paired is sometimes *worse* than unpaired. Pairing was compensating for dropout,
  and the dropout is gone.

  One caveat on all of the above: campaign 06's four engines each drew a perfect health
  gate on the first attempt, which is a ~9% event at the measured 55% healthy rate.
  These floors likely sit at the optimistic end.

  Do not substitute a repeat of the same arm, and do not reuse a floor measured on an
  earlier run — the dropout that generates the noise varies with load and with how many
  arms run at once. See
  [docs/analysis/2026-08-22-first-optimization-sweep.md](docs/analysis/2026-08-22-first-optimization-sweep.md).

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

For redeploy: `update_agent_from_source()` rebuilds the package with the new instruction and calls `runtimes.update()`. No pickle manipulation needed.

### Critical constraints (learned the hard way)

1. **Build dir must be at project root** — the SDK's `_create_base64_encoded_tarball` validates paths are under `os.getcwd()`. Using `/tmp/` fails. The build dir is created at `Path.cwd() / "_geap_build_pkg"`.

2. **`source_packages` must use relative paths** — `tar.add(file)` preserves the path structure. An absolute path like `/app/_geap_build_pkg` creates a broken archive. Convert with `Path(build_dir).relative_to(Path.cwd())`.

3. **`requirements_file` must be explicit** — GEAP defaults to `requirements.txt` at the tarball root, not inside a package subdirectory. Set `requirements_file: "_geap_build_pkg/requirements.txt"` to point into the package.

4. **`config.py` lives alongside `agents/`, not inside it** — the agent files are at `multi_model_agents/agents/opus_agent.py` but `config.py` and `registry.py` are at `multi_model_agents/`. `build_source_package()` walks up the directory tree to find the dir containing `config.py`.

5. **Deployed agents use direct Cloud Run URLs with GoogleAuth** — the build package gets a generated `registry.py` that uses `McpToolset` with `httpx.AsyncClient(auth=GoogleAuth())`. The GEAP service account provides ADC credentials for Cloud Run invoker auth. Timeouts set to 60s connect / 180s read for cold starts. Requires both `*_MCP_SERVER` and `*_MCP_URL` env vars.

6. **`config.py` MCP vars rewritten to safe defaults** — `build_source_package()` rewrites `os.environ["SEARCH_MCP_SERVER"]` to `os.environ.get("SEARCH_MCP_SERVER", "")` so the module loads cleanly when the var is unset. Actual values come via the `env_vars` config dict. As of 2026-08-20 the example config uses `.get()` at the source, so the rewrite is a no-op there; it remains as a safety net for third-party agent configs, which crash the GEAP container on import if they subscript a var the server does not set.

7. **Cloud Run MCP services need IAM invoker + session affinity** — the GEAP service account (`service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) must have `roles/run.invoker` on each Cloud Run MCP service. Session affinity must be enabled so MCP sessions stick to the same instance (without it, follow-up tool calls get 404). Grant and configure with:
   ```bash
   SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
   for SVC in wrangler-search-mcp wrangler-booking-mcp wrangler-expense-mcp; do
     gcloud run services add-iam-policy-binding "$SVC" \
       --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
       --member="serviceAccount:$SA" --role="roles/run.invoker" --quiet
     gcloud run services update "$SVC" \
       --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
       --session-affinity --quiet
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

### There is no legacy path

`deploy_agent()` and `update_agent()` (pickle-based) were deleted. Every caller — the CLI, `WranglerPipeline`, the KFP components, and the example scripts — uses the source-based functions. `tests/test_deploy.py::test_cloudpickle_entrypoints_are_gone` fails if the names reappear on `wrangler.core.deploy` or `wrangler.core`.

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
Cloud Run MCP servers were found to drop idle HTTP connections within ~2 minutes — too short for GEPA's inter-generation gaps. The optimize component starts **local FastMCP servers** on localhost (ports 8001-8003) from the code in `examples/multi_model_agents/mcp_servers/`. MCP URLs are overridden to `http://localhost:{port}/mcp`. Per-generation session refresh closes and re-warms sessions between GEPA generations (~0.1s overhead).

**Caveat (2026-08-20):** the ~2 minute idle drop did **not** reproduce from a local CLI run — a session sat idle 150s against all three services and then listed tools fine. The services now run `minScale=3` with session affinity on, which may be why. Treat the number as "observed once from inside the pipeline container", not as a measured property. The failure that *does* reproduce is a session teardown race under concurrent `get_tools()`; see [docs/notes/silent-failures.md](docs/notes/silent-failures.md) §1b and the `tool_list_cache_ttl_seconds` fix in both `registry.py` files.

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
Pre-built via Cloud Build, tagged by `md5(pyproject.toml + uv.lock + Dockerfile.pipeline)[:12]` (`_compute_image_tag()` in `wrangler/pipeline/deploy_pipeline.py`). Adding a dependency to any of the three triggers a rebuild (~3 min). All three are needed: `pyproject.toml` holds ranges rather than resolved versions, and `Dockerfile.pipeline` installs from its own hardcoded `pip install` list — it copies `pyproject.toml` but does not install from it.

**Every entry in that list is pinned with `==`, and a `>=` floor is a test
failure.** `tests/test_pipeline_image_pins.py` fails on any floor, and on any
pin that disagrees with `uv.lock`. This is not style: on 2026-09-08 a dependabot
lockfile bump moved the image tag, the rebuild resolved the unpinned floors
fresh, and the optimize container came up on ADK 2.8.0 while local, CI and
`deploy.py` were all on 2.7.1 — running the five monkey-patches against an
unverified ADK and silently miscounting tool failures. The image needs `fastmcp`
for the local MCP servers, currently `==3.4.7`; see the `mcp<2` note below
before changing it.

### Python versions: 3.11 everywhere except the MCP images

`wrangler/tools/preflight.py:TARGET_PYTHON` is the single source of truth, and
it is **3.11** — GEAP's runtime, `Dockerfile.pipeline`'s base, and the version
every pin in the repo is resolved for. Import it; do not redefine it.

The three Cloud Run MCP images are on **`python:3.14-slim`**, arrived via
dependabot on 2026-09-08. They are listed in
`tests/test_pipeline_image_pins.py:PYTHON_MISMATCH_ACCEPTED` with the evidence
that their pin set resolves and serves on that base. Two consequences worth
knowing:

- The **same MCP server code** runs on 3.14 (Cloud Run) and 3.11 (inside the
  optimize container, which starts local copies). Both work; nobody chose it.
- Pin-vs-installed comparisons **skip** when the running interpreter is not
  `TARGET_PYTHON`, because `metadata.version()` from a 3.13 venv says nothing
  about a 3.11 container. A further test asserts 3.11 is still in the CI matrix,
  so the skip cannot silently disable everything.

`Dockerfile.pipeline` may never be exempted — it runs the ADK monkey-patches,
and moving the interpreter under them is no different from moving ADK.

### reporter.REPORTS_DIR / CHARTS_DIR
Must be `Path` objects, not strings. The reporter calls `.mkdir()` on them. When overriding in pipeline components, pass `Path(...)` not `str(...)`.

### Eval Data Cleaning
Rows with NaN/None/empty responses must be dropped from inference results before calling `create_evaluation_run()`. The SDK validates `agent_data` types and throws `ValueError` on invalid rows.

### HTTPX Client Factory
The MCP session manager passes `headers`, `timeout`, and other kwargs to `httpx_client_factory`. The factory must accept `**kwargs` and pop conflicting keys (`timeout`, `limits`) before passing to `httpx.AsyncClient()`.

### Two config.py Files
`wrangler/core/models.py` (re-exported via `wrangler/core/config.py`) and
`examples/multi_model_agents/config.py` both define `resolve_model()` / `model_location()`.
Keep them in sync — the multi-model agents import from their **local** config, not
wrangler's, and `build_source_package()` copies that local file into `_geap_build_pkg/`, so
it is the version that actually runs on GEAP. A fix applied only to `wrangler/core/` will
appear to work locally in the CLI and still ship broken to every deployed agent.

**`tests/test_shared_source_drift.py` now enforces this**, so drift is a red build rather
than a deploy-time surprise. It compares the two by **behaviour**, not by source: every
registered id plus a Gemini 2.x, the `models/` form and an unknown id go through both, and
the three fields that decide routing (type, model id, pinned location) must match. Source
comparison was rejected because the two already differ in docstrings, so it would need an
allowlist that eventually gets widened to let a real difference through.

The same file guards two more hand-synced pairs that had only comments: the generated
`_REGISTRY_PY_TEMPLATE` in `deploy.py` against `examples/multi_model_agents/registry.py`
(no `tool_name_prefix` on either, matching cache TTL, read timeout and startup-probe
budget, and every `*_MCP_*` var `config.py` declares must be read by the generated
registry), and that all three templates parse as Python.

Read the environment at **call** time in both files, never into a module constant. The
example config used to bind `GCP_REGION` and `GCP_PROJECT_ID` at import; the pipeline
components set both *inside* the component body, after the tarball is extracted, so the
deployed copy could route on a stale region or build a Claude resource path against a
stale project.

### Optimizer Prompt Flow

The optimizer loads the agent from `*_opt/__init__.py` but overrides the instruction with the manifest's `system_prompt` via the `initial_instruction` parameter in `optimize()`. This ensures GEPA optimizes the same prompt that eval-before tests.

```
manifest system_prompt → deploy (instruction.txt) → eval-before (deployed agent)
                       → optimize(initial_instruction=system_prompt) → GEPA evolves
                       → redeploy (optimized prompt) → eval-after
```

The `_opt/__init__.py` files should NOT override `instruction` — the `initial_instruction` parameter handles prompt selection.

### deploy.py Requirements List
`_SOURCE_REQUIREMENTS` in `wrangler/core/deploy.py` is written into `_geap_build_pkg/requirements.txt` and installed by GEAP. Must match the ADK version in `pyproject.toml` or agents will fail to start with import errors.

These are **floors**, not pins, because GEAP resolves them itself — so the
deployed agent may be newer than what we validated, but must never be older.
Two rules the tests enforce:

- **No floor may exceed the installed version.** A floor above what this repo
  runs is a version nothing has ever tested. Campaign 07 died on exactly that:
  `litellm>=1.96.2` was read off `uv.lock` without noticing that entry sits
  behind a `python>=3.14` marker, while GEAP runs 3.11 and we validate 1.85.7.
- **`litellm` is capped `<1.86`.** Above that it needs `jinja2>=3.1.6`, and
  `google-adk[eval]==2.8.0` resolves `jinja2` to 3.1.5 on the GEAP builder —
  `ResolutionImpossible`, surfaced to the caller only as
  `Build failed ... or other dependencies`. The `<1.86` that
  `google-cloud-aiplatform[evaluation]` applies locally does **not** reach here,
  because this list omits the `evaluation` extra.

Run **`uv run wrangler preflight`** before a campaign: it resolves this list and
the image's pins at 3.11, which is the check whose absence cost two launches.
`scripts/validate_then_run.py` calls it automatically.

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
