# GEPA Prompt Wrangler

![GEPA Prompt Wrangler](docs/gepa_prompt_wrangler_banner.png)

A prompt optimization harness for [Google ADK](https://google.github.io/adk-docs/) agents. Define multiple model + system-prompt pairs in a YAML manifest, evaluate them against a shared eval set, optimize with GEPA, and deploy the winners to the Gemini Enterprise Agent Platform (GEAP).

---

## Default Workflow

```
1. Deploy      Deploy agent(s) to GEAP
2. Eval        Run batch eval against deployed agent (baseline)
3. Optimize    Run local GEPA optimization with eval dataset
4. Redeploy    Update agent with optimized prompt
5. Re-eval     Run batch eval again (measure improvement)
6. Report      Generate comparative analysis report
```

Run the full pipeline in one command:

```bash
wrangler run manifest.yaml
```

Or step by step:

```bash
wrangler deploy manifest.yaml
wrangler eval manifest.yaml --engine-id <ID>
wrangler optimize manifest.yaml
wrangler deploy manifest.yaml --pair gemini-flash
wrangler report outputs/
```

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/tottenjordan/gepa-prompt-wrangler.git
cd gepa-prompt-wrangler

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your GCP project, region, and staging bucket

# Generate a starter manifest
wrangler init

# Or auto-detect from your agent
wrangler init --agent-dir my_agent/

# Inspect an agent's tools (shows eval-ready tool names)
wrangler inspect agents/example_agent

# Generate GEPA evalset from eval cases
wrangler generate-evalset --from eval_cases.yaml --output my_agent_opt/ -n 15

# Run the full pipeline
wrangler run manifests/example_manifest.yaml

# Or evaluate an already-deployed agent (no manifest needed)
wrangler eval --engine-id <ENGINE_ID> --eval-data eval_cases.yaml
```

### Bring Your Own Agent

Three ways to get started with your existing ADK agent:

| Path | Starting Point | Command |
|------|---------------|---------|
| **Function tools** | Agent with Python functions | `wrangler init --agent-dir my_agent/` |
| **MCP tools** | Agent with MCP servers | `wrangler init --agent-dir my_agent/` + use prefixed tool names |
| **Already deployed** | Agent on Agent Engine | `wrangler eval --engine-id <ID> --eval-data eval.yaml` |

Templates are available at `templates/function_tools/` and `templates/mcp_tools/`. See the full guide at [docs/bring_your_own_agent.md](docs/bring_your_own_agent.md).

### Multi-Model Example (End-to-End)

```bash
# Step 1: Configure environment
cd examples/multi_model_agents
cp .env.example .env
# Edit .env with your GCP project ID, region, and project number

# Step 2: Deploy MCP tool servers (wrangler-prefixed Cloud Run services)
bash scripts/deploy_mcp_servers.sh

# Step 3: Deploy all 5 agents to Agent Engine
cd ../..
uv run python examples/multi_model_agents/deploy_agents.py

# Step 4: Setup monitoring (logging sink + online evaluators)
bash examples/multi_model_agents/scripts/setup_monitoring.sh

# Step 5: Run the full optimization experiment
wrangler run examples/multi_model_agents/manifest.yaml
```

See [examples/multi_model_agents/README.md](examples/multi_model_agents/README.md) for detailed walkthrough.
See [docs/online_eval_guide.md](docs/online_eval_guide.md) for evaluators vs monitors.

---

## Repository Structure

```
gepa-prompt-wrangler/
├── pyproject.toml
├── .env.example
│
├── wrangler/                        # Core library
│   ├── cli.py                       # Click CLI (init, inspect, run, eval, optimize, report, deploy)
│   ├── config.py                    # GCP config, resolve_model(), MODEL_COSTS, disable_pyopenssl()
│   ├── analysis.py                  # Per-agent markdown report generator
│   ├── factory.py                   # Manifest parser, AgentPromptPair dataclass
│   ├── converter.py                 # YAML ↔ ADK evalset format auto-conversion
│   ├── evaluator.py                 # Batch eval against deployed GEAP agents (6 metrics)
│   ├── optimizer.py                 # GEPA wrapper with ADK patches
│   ├── runner.py                    # Full 6-phase pipeline orchestrator
│   ├── reporter.py                  # Matplotlib charts + markdown report generation
│   ├── deploy.py                    # Deploy/update agents on GEAP
│   └── inspector.py                 # Auto-discover agent tools via introspection
│
├── agents/                          # User-defined agent modules
│   └── example_agent/               # Example travel agent with mock tools
│
├── manifests/                       # Experiment configs
│   └── example_manifest.yaml        # 2-pair example (Gemini Flash vs Claude Sonnet)
│
├── eval_data/                       # Eval datasets
│   └── example_eval.yaml            # 5 simplified eval cases
│
├── scripts/                         # Analysis & visualization
│   ├── generate_analysis.py         # Matplotlib charts + per-agent reports
│   ├── generate_diagrams.py         # PaperBanana architecture diagrams
│   └── run_full_analysis.py         # Master script chaining all analysis
│
├── examples/                        # Reference implementations
│   └── multi_model_agents/          # Full 5-model comparison (lite → opus)
│       ├── manifest.yaml
│       ├── run_demo.py              # E2E demo: generic → eval → optimize → eval
│       ├── deploy_agents.py         # Deploy all agents to GEAP
│       ├── generic_prompts.py       # Intentionally weak starting prompts
│       ├── agents/                  # 5 standalone agents
│       ├── mcp_servers/             # 3 MCP tool servers (Cloud Run + OTel)
│       ├── eval_data/               # 30 eval cases (low/medium/high)
│       └── scripts/                 # Infrastructure deployment
│
├── outputs/                         # Generated artifacts (gitignored)
│   ├── baselines/                   # Pre-optimization eval results
│   ├── optimized/                   # Post-optimization eval results
│   ├── prompts/                     # Before/after prompt snapshots
│   └── reports/                     # Markdown reports + charts
│
├── templates/                       # BYOA starter templates
│   ├── function_tools/              # Agent with Python function tools
│   └── mcp_tools/                   # Agent with MCP tool servers
│
├── docs/
│   ├── bring_your_own_agent.md      # Full BYOA guide (3 paths + troubleshooting)
│   ├── gepa_prompt_wrangler_banner.png
│   └── diagram_sources/             # PaperBanana diagram descriptions
│
└── tests/
    ├── conftest.py                  # Shared test fixtures
    ├── test_analysis.py             # Report generation + cost-benefit analysis
    ├── test_cli.py                  # CLI command tests (Click CliRunner)
    ├── test_config.py               # Model resolution + constants
    ├── test_converter.py            # Eval format conversion
    ├── test_evaluator.py            # Batch eval helpers + result saving
    ├── test_factory.py              # Manifest parsing
    ├── test_inspector.py            # Agent introspection + tool discovery
    ├── test_online_evaluators.py    # Online evaluator config helpers
    ├── test_online_monitors.py      # Online monitor helpers
    ├── test_optimizer.py            # GEPA wrapper module creation
    ├── test_prompt_registry.py      # Prompt versioning + registry
    ├── test_reporter.py             # Chart generation + markdown reports
    └── test_traffic.py              # Traffic generator helpers
```

---

## Manifest Format

```yaml
name: travel-agent-model-comparison
description: Compare Gemini Flash vs Claude Sonnet on travel agent tasks

agent_module: agents/example_agent
eval_data: eval_data/example_eval.yaml

pairs:
  - id: gemini-flash
    model: gemini-3.5-flash
    system_prompt: |
      You are a corporate travel assistant.
      Use tools to search flights and book hotels.

  - id: claude-sonnet
    model: claude-sonnet-4-6
    system_prompt: |
      You are a corporate travel assistant.
      Use tools to search flights and book hotels.

eval_config:
  judge_model: gemini-2.5-pro
  response_match_threshold: 0.5
  safety_threshold: 0.8

deploy:
  project: my-gcp-project
  region: us-central1
  staging_bucket: my-staging-bucket
```

### Required Fields

| Field | Description |
|-------|-------------|
| `name` | Unique name for this experiment |
| `agent_module` | Path to the agent module directory |
| `pairs` | List of model + system-prompt pairs |
| `pairs[].id` | Unique identifier for the pair |
| `pairs[].model` | Model string (see Supported Models) |
| `pairs[].system_prompt` | System prompt text |

---

## Eval Data Formats

### Simplified YAML (recommended)

```yaml
eval_cases:
  - prompt: "Find flights from SFO to JFK"
    expected_response: "Flights from SFO to JFK: United FL001 at $450."
    expected_tools:
      - name: search_flights
        args: {origin: SFO, destination: JFK}

  - prompt: "Book flight FL001 for Alice Johnson"
    expected_response: "Flight FL001 booked and confirmed."
    expected_tools:
      - name: book_flight
        args: {flight_id: FL001, passenger_name: "Alice Johnson"}
```

### ADK Evalset JSON

```json
{
  "eval_set_id": "my_eval",
  "eval_cases": [
    {
      "eval_id": "flight_search",
      "conversation": [
        {
          "user_content": {"parts": [{"text": "Find flights from SFO to JFK"}], "role": "user"},
          "final_response": {"parts": [{"text": "Flights found."}], "role": "model"},
          "intermediate_data": {"tool_uses": [{"name": "search_flights", "args": {"origin": "SFO"}}]}
        }
      ]
    }
  ]
}
```

### Format Auto-Detection

The converter auto-detects the format based on file extension and structure:
- `.yaml` / `.yml` → simplified YAML
- `.evalset.json` → ADK evalset JSON
- Dict with `eval_cases` key → simplified YAML (even in `.yaml` files)
- Dict with `eval_set_id` key → ADK evalset JSON

### Converting Between Formats

```python
from wrangler.converter import load_eval_file, to_adk_evalset, save_adk_evalset

# Load from either format (auto-detected)
cases = load_eval_file("eval_data/example_eval.yaml")

# Convert to ADK evalset JSON (required for GEPA optimization)
save_adk_evalset(cases, "eval_data/example_eval.evalset.json", eval_set_id="my_eval")
```

### When Each Format is Used

| Format | Used By | Purpose |
|--------|---------|---------|
| Simplified YAML | `wrangler eval`, `wrangler run`, batch eval, traffic generator | Human-authored eval cases |
| ADK Evalset JSON | GEPA optimizer (`wrangler optimize`) | Machine-consumed eval cases for local optimization |

### Tool Name Convention

Tool names in eval datasets must match what the deployed agent actually calls. ADK prefixes MCP tool names with the server name:

```
FastMCP server name: "search-mcp"
Tool function name:  "search_flights"
Agent Registry name: "wrangler-search-mcp"

→ Actual tool name in traces: "wrangler_search_mcp_search_flights"
```

Use the full prefixed name in eval datasets:
```yaml
expected_tools:
  - name: wrangler_search_mcp_search_flights  # ✓ correct
  # - name: search_flights                     # ✗ wrong — won't match traces
```

### GEPA Evalset vs Batch Eval Dataset

| | GEPA Evalset | Batch Eval Dataset |
|---|---|---|
| Format | ADK JSON (`.evalset.json`) | Simplified YAML |
| Location | `agents/{name}_opt/{name}_eval_set.evalset.json` | `eval_data/example_eval.yaml` |
| Cases | 15 (balanced: 5 low + 5 medium + 5 high) | 30 (14 low + 9 medium + 7 high) |
| Used by | Local GEPA optimizer | Vertex AI Evaluation Service |
| Purpose | Train — optimize prompt candidates | Test — measure deployed agent quality |

The GEPA evalset is a balanced subset of the batch eval dataset. This train/test split ensures GEPA-optimized prompts generalize to unseen cases.

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `wrangler init` | Create a starter manifest.yaml |
| `wrangler inspect <agent_path>` | Auto-discover agent tools, generate YAML scaffold |
| `wrangler run <manifest>` | Full pipeline: deploy → eval → optimize → redeploy → eval → report |
| `wrangler eval <manifest>` | Run batch eval against a deployed agent |
| `wrangler optimize <manifest>` | Run GEPA optimization for each pair |
| `wrangler report <outputs_dir>` | Generate analysis report from results |
| `wrangler deploy <manifest>` | Deploy agent pairs to GEAP |

### Options

```bash
wrangler run manifest.yaml --dry-run       # Validate without executing
wrangler deploy manifest.yaml --pair flash  # Deploy specific pair
wrangler eval manifest.yaml --engine-id ID  # Eval against specific engine
```

---

## Running Evaluations

The wrangler provides 4 evaluation options, each serving a different purpose:

### Option 1: Batch Eval (Server-Side)

Run the full 30-case eval dataset against a deployed agent. Scores are computed server-side by the Vertex AI Evaluation Service.

```bash
# Eval a single agent by engine ID
uv run python -m wrangler.evaluator <engine-id> eval_data/example_eval.yaml

# Or via CLI
wrangler eval manifest.yaml --engine-id <ENGINE_ID>
```

**When to use:** Before/after prompt optimization to measure improvement. Results saved to `outputs/baselines/` or `outputs/optimized/`.

### Option 2: Online Evaluators (Automatic)

Always-on evaluators that score OTel traces from live traffic every 10 minutes.

```bash
# Create evaluators for all deployed agents
uv run python -m wrangler.online_evaluators create

# Check status
uv run python -m wrangler.online_evaluators verify

# List all evaluators
uv run python -m wrangler.online_evaluators list

# Delete a specific evaluator
uv run python -m wrangler.online_evaluators delete <evaluator_id>

# Remove all wrangler evaluators
uv run python -m wrangler.online_evaluators cleanup
```

**When to use:** Continuous monitoring of production traffic quality. Results appear in the Agent Engine Observability tab.

### Option 3: Online Monitors (On-Demand)

Quick spot-check against a deployed agent with a small set of test queries.

```bash
# Run 8 default queries against an agent
uv run python -m wrangler.online_monitors <engine-id>

# Run fewer queries for a faster check
uv run python -m wrangler.online_monitors <engine-id> --cases 3
```

**When to use:** Health checks, regression testing, pre/post deployment validation. Results saved to `outputs/monitors/`.

### Option 4: GEPA Optimization (Local)

Run the GEPA evolutionary optimizer locally against an agent's eval dataset. This is not just evaluation — it iteratively improves the prompt.

```bash
# Optimize via CLI
wrangler optimize manifest.yaml --pair lite

# Or directly
uv run python -c "
from wrangler.optimizer import optimize
result = optimize(
    'examples/multi_model_agents/agents/lite_opt',
    sampler_config_path='examples/multi_model_agents/agents/lite_opt/sampler_config.json',
)
print(result)
"
```

**When to use:** After baseline eval shows room for improvement. Uses 15 balanced eval cases (5 low + 5 medium + 5 high) with a `gemini-2.5-pro` judge.

### Generating Traffic

Send test queries to populate OTel traces for online evaluators:

```bash
# Send 15 default queries to one agent (1 query/sec)
uv run python -m wrangler.traffic --agent-id <ENGINE_ID>

# Round-robin across multiple agents
uv run python -m wrangler.traffic --agent-id <ID1> <ID2> <ID3> --count 30 --interval 2

# Use the eval dataset as queries
uv run python -m wrangler.traffic --agent-id <ENGINE_ID> \
  --eval-data examples/multi_model_agents/eval_data/eval_cases.yaml
```

Each query creates a new session with a unique user ID for clean, independent traces.

### Evaluation Flow Summary

```
                    ┌─────────────────────────┐
                    │   Deploy Agent to GEAP   │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  Batch Eval (baseline)   │ ← Option 1
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   GEPA Optimize (local)  │ ← Option 4
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  Redeploy with new prompt│
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │  Batch Eval (optimized)  │ ← Option 1
                    └───────────┬─────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
  ┌─────────▼────────┐ ┌───────▼──────┐ ┌──────────▼─────────┐
  │ Online Evaluators │ │   Traffic    │ │  Online Monitors   │
  │  (continuous)     │ │  Generator   │ │  (on-demand)       │
  │  ← Option 2      │ │              │ │  ← Option 3        │
  └──────────────────┘ └──────────────┘ └────────────────────┘
```

See [docs/online_eval_guide.md](docs/online_eval_guide.md) for details on evaluators vs monitors.

---

## Evaluation Metrics

### Default metrics (6)

| Metric | What it measures |
|--------|-----------------|
| `final_response_quality` | Overall response quality |
| `hallucination` | Factual accuracy |
| `safety` | Content safety |
| `tool_use_quality` | Correct tool selection and parameters |
| `instruction_following` | Adherence to system prompt |
| `final_response_match` | Match against reference response |

### GEPA optimization metrics

| Metric | Purpose |
|--------|---------|
| `response_match_score` | Primary optimization target |
| `final_response_match_v2` | LLM-judged similarity (uses judge model) |
| `safety_v1` | Constraint — don't sacrifice safety for quality |

---

## Supported Models

| Model | Provider | Endpoint | Input $/M | Output $/M |
|-------|----------|----------|----------|-----------|
| `gemini-2.5-flash` | Google | Regional | — | — |
| `gemini-3.1-flash-lite` | Google | Global (LiteLLM) | $0.25 | $1.50 |
| `gemini-3.5-flash` | Google | Global (LiteLLM) | $1.50 | $1.65 |
| `gemini-3.1-pro-preview` | Google | Global (LiteLLM) | $4.00 | $18.00 |
| `claude-sonnet-4-6` | Anthropic | Global (LiteLLM) | $3.00 | $15.00 |
| `claude-opus-4-6` | Anthropic | Global (LiteLLM) | $5.00 | $25.00 |

*Source: [GEAP Model Pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)*

Gemini 2.x uses regional Vertex AI endpoints (passed as strings). All other models route through `LiteLlm` with `vertex_location="global"`.

---

## Examples

### Multi-Model Comparison (5 tiers)

Full end-to-end example comparing lite → flash → pro → sonnet → opus on the same travel/expense agent with shared MCP tools.

```bash
# Deploy wrangler-prefixed MCP servers
bash examples/multi_model_agents/scripts/deploy_mcp_servers.sh

# Run the experiment
wrangler run examples/multi_model_agents/manifest.yaml
```

See [examples/multi_model_agents/README.md](examples/multi_model_agents/README.md) for details.

### Agent Inspection

```bash
# Auto-discover tools from an existing agent
wrangler inspect agents/example_agent

# Output:
# agent:
#   name: example_travel_agent
#   tools:
#     - name: search_flights
#       description: Search available flights
#       parameters:
#         origin: {type: string, required: true}
#         destination: {type: string, required: true}
```

### Python API

```python
from wrangler.factory import PairFactory
from wrangler.converter import load_eval_file
from wrangler.evaluator import run_batch_eval

# Parse manifest
manifest = PairFactory.load("manifest.yaml")

# Load eval cases (auto-detects format)
cases = load_eval_file("eval_data/my_eval.yaml")

# Run eval against deployed agent
scores = run_batch_eval(engine_id="1234567890", eval_cases=cases)
print(scores)
# {'final_response_quality_v1': 0.85, 'hallucination_v1': 0.94, ...}
```

---

## Contributing

1. Fork the repository and create a feature branch.
2. Install dev dependencies: `uv sync`.
3. Write tests: `tests/test_*.py` (107 tests across all 15 modules).
4. Run tests:
   ```bash
   # All tests
   uv run pytest tests/ -v

   # By tier
   uv run pytest tests/test_analysis.py tests/test_config.py tests/test_prompt_registry.py -v  # Tier 1: pure logic
   uv run pytest tests/test_inspector.py tests/test_reporter.py tests/test_cli.py -v            # Tier 2: file I/O
   uv run pytest tests/test_traffic.py tests/test_evaluator.py tests/test_optimizer.py tests/test_online_evaluators.py tests/test_online_monitors.py -v  # Tier 3: API helpers
   ```
5. Submit a pull request.

---

## FAQ

**Q: Do I need a GCP project?**
A: Yes. The harness deploys agents to Vertex AI Agent Engine and uses Vertex AI models for evaluation and GEPA optimization.

**Q: Can I use non-Vertex AI models?**
A: Currently supports Gemini and Claude via Vertex AI. Extend `resolve_model()` in `config.py` to add other LiteLLM-supported providers.

**Q: What is GEPA?**
A: Gemini Evolutionary Prompt Algorithm — an evolutionary optimization algorithm that iteratively improves agent system prompts by generating variants, evaluating them against your eval dataset, and selecting the best performers across generations.

**Q: How do I use my own agent?**
A: Create a directory under `agents/` with an `__init__.py` that exports `agent.root_agent` (an ADK `LlmAgent`), then set `agent_module` in your manifest. Or implement a `create_agent(model, instruction)` factory function for dynamic model/prompt injection.

**Q: What's the difference between `run` and `deploy`?**
A: `run` executes the full 6-phase pipeline (deploy → eval → optimize → redeploy → eval → report). `deploy` just deploys agents without evaluation or optimization.
