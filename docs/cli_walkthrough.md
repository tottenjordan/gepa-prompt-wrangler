# CLI Walkthrough

Step-by-step walkthrough of each CLI command, explaining what it does, why it matters, and what to expect.

---

## Test 1: `wrangler inspect` — Tool Name Discovery

**What it does:** Loads an agent module, imports it, discovers all tools via reflection, and prints their names, types, and the exact names to use in eval cases.

**Why it matters:** This is the first thing a BYOA user runs to understand what their agent exposes. If tool names are wrong here, every downstream step (eval cases, evalset, optimization) will fail silently.

**What we expect:** For `agents/example_agent`, it should find 5 function tools (`search_flights`, `search_hotels`, `check_policy`, `create_booking`, `generate_expense_report`), print their parameter signatures, and show a "Tool names for eval cases" section at the bottom.

```bash
wrangler inspect agents/example_agent
```

**Example output:**

```
agent:
  name: travel_agent
  model: vertex_ai/gemini-3.5-flash
  instruction: You are a helpful corporate travel assistant...
  tools:
  - name: search_flights
    description: Search for available flights between two airports on a given date.
    type: function
    parameters:
      origin:   {type: string, required: true}
      destination: {type: string, required: true}
      date:     {type: string, required: true}
  - name: search_hotels
    ...
  - name: check_policy
    ...
  - name: create_booking
    ...
  - name: generate_expense_report
    ...

Discovered 5 tools for agent 'travel_agent'

Tool names for eval cases:
  search_flights                           [function]
  search_hotels                            [function]
  check_policy                             [function]
  create_booking                           [function]
  generate_expense_report                  [function]
```

**Key takeaway:** The "Tool names for eval cases" section at the bottom gives you the exact strings to use in your `eval_cases.yaml` `expected_tools` entries. For function tools, the name is the Python function name. For MCP tools, it would show the prefixed form (e.g., `my_server_search_items`).

---

## Test 2: `wrangler init --agent-dir` — Auto-Detect and Scaffold

**What it does:** Inspects an agent module, then generates two files:
1. A `manifest.yaml` pre-populated with the agent's real name, model, and instruction
2. An `eval_cases.yaml` skeleton with one eval case per tool, including correct tool names and parameter hints

**Why it matters:** Without this, a new user has to manually write a manifest from scratch and guess at tool names. This eliminates the cold-start problem — the user just replaces TODO placeholders with real queries.

**What we expect:** Running against `agents/example_agent` should generate a manifest with `travel_agent` as the name, `vertex_ai/gemini-3.5-flash` as the model, and an eval skeleton with 5 cases (one per tool), each with the correct function name and first 2 parameter names as hints.

```bash
wrangler init --agent-dir agents/example_agent -o /tmp/byoa_test/manifest.yaml
```

**Example output:**

```
Inspecting agent at agents/example_agent...
  Agent: travel_agent
  Model: vertex_ai/gemini-3.5-flash
  Tools: 5
  Generated eval skeleton: /tmp/byoa_test/eval_cases.yaml (5 cases)
  Edit the TODO placeholders with real queries and expected responses.

  Tool names for eval cases:
    - search_flights [function]
    - search_hotels [function]
    - check_policy [function]
    - create_booking [function]
    - generate_expense_report [function]
Created /tmp/byoa_test/manifest.yaml — edit it with your agent and eval data paths.
```

**Generated `manifest.yaml`:**

```yaml
name: travel_agent-optimization
description: Prompt optimization for travel_agent
agent_module: agents/example_agent
eval_data: eval_cases.yaml
pairs:
- id: travel_agent
  model: vertex_ai/gemini-3.5-flash
  system_prompt: You are a helpful corporate travel assistant. Use the available tools
    to help employees with travel planning, booking, and expense management.
eval_config:
  judge_model: gemini-3.5-flash
  response_match_threshold: 0.5
  safety_threshold: 0.8
```

**Generated `eval_cases.yaml`** (skeleton — replace TODOs with real queries):

```yaml
eval_cases:
- prompt: 'TODO: Write a query that triggers search_flights(origin=..., destination=...)'
  expected_response: 'TODO: Expected agent response'
  expected_tools:
  - name: search_flights
    args: {origin: TODO, destination: TODO}
- prompt: 'TODO: Write a query that triggers search_hotels(location=..., check_in=...)'
  ...
```

**Key takeaway:** The user gets a ready-to-edit manifest and eval skeleton. No guessing at tool names or manifest structure. Just replace the TODOs with real queries and expected responses.

---

## Test 3: `wrangler generate-evalset` — GEPA Evalset from Simplified YAML

**What it does:** Takes a simplified `eval_cases.yaml` and converts it into the ADK GEPA evalset format — a JSON file with conversation structure (user_content, final_response, intermediate_data with tool_uses) plus a `sampler_config.json` that tells GEPA which metrics to use and which evalset to load.

**Why it matters:** Manually creating GEPA evalset JSON is the most error-prone step in the optimization pipeline. The format requires nested conversation objects, session_input, and exact tool_use structures. This command automates the entire conversion, including balanced sampling across complexity levels.

**What we expect:** Given our 64-case `eval_cases.yaml` from the multi_model example, it should produce a 15-case balanced evalset JSON and a sampler config, ready for GEPA optimization.

```bash
wrangler generate-evalset \
  --from examples/multi_model_agents/eval_data/eval_cases.yaml \
  --output /tmp/byoa_test/agent_opt/ \
  -n 15
```

**Example output:**

```
Loaded 64 eval cases from examples/multi_model_agents/eval_data/eval_cases.yaml
  Evalset: /tmp/byoa_test/agent_opt/agent_opt_eval_set.evalset.json (15 cases)
  Sampler config: /tmp/byoa_test/agent_opt/sampler_config.json

Ready for optimization:
  wrangler optimize --agent-dir <agent_path> --evalset-dir /tmp/byoa_test/agent_opt/
```

**Generated files:**

```
agent_opt/
├── agent_opt_eval_set.evalset.json   # 15 GEPA-formatted eval cases
└── sampler_config.json               # Metrics + evalset reference for GEPA
```

**`sampler_config.json`:**

```json
{
  "eval_config": {
    "criteria": {
      "response_match_score": 0.1,
      "final_response_match_v2": {
        "threshold": 0.5,
        "judge_model_options": {"judge_model": "gemini-3.5-flash"}
      },
      "safety_v1": 0.8
    }
  },
  "app_name": "agent_opt",
  "train_eval_set": "agent_opt_eval_set"
}
```

**Key takeaway:** The `app_name` in the sampler config must match the optimizer directory name, and `train_eval_set` must match the evalset JSON filename stem (without `.evalset.json`). The `generate-evalset` command handles this automatically. The `--balanced` flag (on by default) samples proportionally across complexity levels so GEPA sees a representative mix of easy and hard cases.

---

## Test 4: `wrangler eval --engine-id --eval-data` — Standalone Eval

**What it does:** Runs a batch evaluation against an already-deployed agent on Agent Engine, without requiring a manifest file. Sends all eval cases to the agent via the Vertex AI Evaluation Service, scores responses against 6 rubric metrics, and prints the results.

**Why it matters:** This is the fastest path for users who already have agents deployed. No manifest, no agent module — just point at an engine ID and eval data. It's also how you do quick health checks.

**What we expect:** Running against the lite agent (engine `4981388556929859584`) with the 64-case eval dataset should produce scores for all 6 metrics (response quality, hallucination, safety, tool use, instruction following, response match). The agent is currently running the GEPA-optimized `wrangler_v2` prompt, so we expect scores similar to our earlier post-optimization results.

```bash
wrangler eval \
  --engine-id 4981388556929859584 \
  --eval-data examples/multi_model_agents/eval_data/eval_cases.yaml
```

**Note:** This command takes 3-5 minutes because it runs inference on all 30 cases and then waits for server-side evaluation scoring to complete.

```bash
wrangler eval \
  --engine-id 4981388556929859584 \
  --eval-data examples/multi_model_agents/eval_data/eval_cases.yaml
```

**Example output:**

```
  Running inference (64 cases)... 159s
  Creating evaluation run... EvaluationRunState.SUCCEEDED

Results:
  final_response_match_v2                  0.67
  final_response_quality_v1                0.88
  hallucination_v1                         0.81
  instruction_following_v1                 0.62
  safety_v1                                1.00
  tool_use_quality_v1                      0.48
```

**Key takeaway:** No manifest, no agent module, no deployment step. Just an engine ID and eval data. This is the fastest way to health-check a deployed agent or compare before/after prompt changes. Scores will vary slightly between runs since each eval creates new sessions with the live agent.

---

## Test 5: `wrangler pipeline run` — Submit to Vertex AI Pipeline

**What it does:** Compiles the GEPA optimization workflow as a KFP v2 pipeline and submits it to Vertex AI Pipelines. Each model/agent pair in the manifest gets its own deploy, eval, optimize, and redeploy step in the pipeline DAG.

**Why it matters:** Running experiments locally ties up your machine for hours. The pipeline runs on managed infrastructure with per-step metrics, fault isolation, and GCS artifact persistence. If one pair fails, others continue.

```bash
wrangler pipeline run manifests/example_manifest.yaml
```

**What happens:**
1. Agent code + eval data are packaged as a tarball and uploaded to GCS
2. The manifest is serialized as a pipeline parameter
3. The KFP pipeline YAML is compiled and submitted to Vertex AI
4. You get a dashboard URL to monitor progress

**Example output:**

```
2026-06-09 14:30:22 - INFO - Packaging code and uploading to GCS...
2026-06-09 14:30:25 - INFO - Compiling pipeline to /tmp/gepa_pipeline_run-20260609-143022.yaml...
2026-06-09 14:30:26 - INFO - ============================================================
2026-06-09 14:30:26 - INFO - DEPLOYMENT SUMMARY
2026-06-09 14:30:26 - INFO -   Experiment:     travel-agent-prompt-comparison
2026-06-09 14:30:26 - INFO -   Run ID:         run-20260609-143022
2026-06-09 14:30:26 - INFO -   Pairs:          2
2026-06-09 14:30:26 - INFO -   Judge Model:    gemini-2.5-pro
2026-06-09 14:30:26 - INFO - ============================================================
2026-06-09 14:30:30 - INFO - Pipeline submitted! Dashboard: https://console.cloud.google.com/...

Pipeline submitted:
  Run ID:    run-20260609-143022
  Job ID:    gepa-run-20260609-143022
  Dashboard: https://console.cloud.google.com/vertex-ai/locations/us-central1/pipelines/runs/...
```

**Key takeaway:** One command turns a local experiment into a managed cloud pipeline. Check status with `wrangler pipeline status <job-id>`. Results and reports are saved to `gs://{bucket}/pipeline-runs/{run_id}/`.

---
