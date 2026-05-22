# Multi-Model Agent Comparison Example

End-to-end example comparing 5 model tiers on the same corporate travel/expense agent.

## Models Tested

| Tier | Model | Provider | Output $/M |
|------|-------|----------|-----------|
| 1 | gemini-3.1-flash-lite | Google | $0.30 |
| 2 | gemini-3.5-flash | Google | $0.60 |
| 3 | gemini-3.1-pro-preview | Google | $10.00 |
| 4 | claude-sonnet-4-6 | Anthropic | $15.00 |
| 5 | claude-opus-4-6 | Anthropic | $75.00 |

## What's Included

```
multi_model_agents/
├── manifest.yaml              # 5-pair experiment config
├── deploy_agents.py           # Deploy script for all agents
├── .env.example               # Environment template
├── config.py                  # Shared GCP config (resolve_model, etc.)
├── registry.py                # Agent Registry MCP tool discovery
├── agents/                    # Standalone ADK agents (one per model tier)
│   ├── lite_agent.py
│   ├── flash_agent.py
│   ├── pro_agent.py
│   ├── sonnet_agent.py
│   └── opus_agent.py
├── mcp_servers/               # MCP tool servers (Cloud Run)
│   ├── search/                # Flight + hotel search
│   ├── booking/               # Flight + hotel booking
│   └── expense/               # Expense management
├── eval_data/                 # Eval cases (30 cases across 3 complexity tiers)
│   ├── eval_cases.yaml
│   ├── agent_eval_configs.py
│   └── tier_eval_cases.py
└── scripts/
    ├── deploy_mcp_servers.sh  # Deploy MCP servers to Cloud Run
    ├── deploy_agents.py       # Deploy agents to Agent Engine
    ├── deploy_all.sh          # Full infrastructure deployment
    └── setup_apphub.sh        # App Hub topology registration
```

## Quick Start

### Step 0: Configure Environment

```bash
cd examples/multi_model_agents
cp .env.example .env
# Edit .env with your GCP project ID, region, and project number
```

### Step 1: Deploy MCP Tool Servers

Deploys 3 Cloud Run services with `wrangler-` prefix and OTel instrumentation:

```bash
bash scripts/deploy_mcp_servers.sh
```

This creates:
- `wrangler-search-mcp` — flight + hotel search tools
- `wrangler-booking-mcp` — flight + hotel booking tools
- `wrangler-expense-mcp` — expense submission, policy checks, history

The script auto-updates `.env` with the deployed service URLs.

### Step 2: Create Staging Bucket

The deploy script creates the staging bucket automatically. If you need to create it manually:

```bash
gsutil mb -p $GCP_PROJECT_ID -l $GCP_REGION gs://$GCP_STAGING_BUCKET
```

### Step 3: Deploy All 5 Agents

Deploy all agents to Vertex AI Agent Engine:

```bash
# From the repo root
uv run python examples/multi_model_agents/deploy_agents.py
```

Or deploy specific agents:

```bash
uv run python examples/multi_model_agents/deploy_agents.py lite flash
uv run python examples/multi_model_agents/deploy_agents.py pro sonnet opus
```

Each agent is deployed with:
- `wrangler-{name}-agent` display name
- GEPA-optimized instructions
- OTel telemetry enabled
- Engine ID auto-written to `.env`

### Step 4: Run the Experiment

```bash
# Full pipeline: eval → optimize → redeploy → eval → report
wrangler run examples/multi_model_agents/manifest.yaml
```

Or step by step:

```bash
# Eval a single deployed agent
wrangler eval manifest.yaml --engine-id <ENGINE_ID>

# Optimize prompts
wrangler optimize manifest.yaml

# Generate report
wrangler report outputs/
```

## Agents

All agents share the same 3 MCP tool servers:
- **search-mcp**: `search_flights`, `search_hotels`
- **booking-mcp**: `book_flight`, `book_hotel`, `cancel_booking`, `get_booking_details`, `list_all_bookings`
- **expense-mcp**: `submit_expense`, `check_expense_policy`, `get_user_expenses`

Each agent uses GEPA-optimized instructions tailored for its model's capability level. The instructions are defined in each agent file and imported by the router sub-agents (single source of truth).

## Eval Dataset

30 eval cases across 3 complexity tiers:

| Tier | Cases | Description |
|------|-------|-------------|
| **Low** | 14 | Single tool call — flight search, hotel search, policy checks, booking, expense history, edge cases (invalid codes, unknown categories) |
| **Medium** | 9 | 2 tools — submit with policy check, flight comparison, multi-category policy, search + policy cross-check, book + verify, savings analysis |
| **High** | 7 | 3+ tools — book + policy + expense pipeline, multi-route comparison with hotels, budget-constrained planning, multi-user audit, end-to-end trip booking |

All tool names use MCP-prefixed format (e.g., `search_mcp_search_flights`) matching what the deployed agents actually call.

## Telemetry

Both agents and MCP servers are instrumented with OpenTelemetry:

**Agents** (via deploy config):
- `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY`
- `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`

**MCP Servers** (via `otel_setup.py`):
- Exports traces to `telemetry.googleapis.com` via authenticated gRPC
- Each server initializes OTel at startup with its service name

Traces flow to Cloud Trace and enable the topology tab in the Agent Engine console.

## Cloud Resources Created

| Resource | Name | Type |
|----------|------|------|
| Cloud Run | `wrangler-search-mcp` | MCP tool server |
| Cloud Run | `wrangler-booking-mcp` | MCP tool server |
| Cloud Run | `wrangler-expense-mcp` | MCP tool server |
| GCS Bucket | `jts-wrangler-staging` | Agent staging |
| Agent Engine | `wrangler-lite-agent` | Reasoning Engine |
| Agent Engine | `wrangler-flash-agent` | Reasoning Engine |
| Agent Engine | `wrangler-pro-agent` | Reasoning Engine |
| Agent Engine | `wrangler-sonnet-agent` | Reasoning Engine |
| Agent Engine | `wrangler-opus-agent` | Reasoning Engine |

All resources are tagged with `wrangler-` prefix to distinguish from other deployments in the same project.
