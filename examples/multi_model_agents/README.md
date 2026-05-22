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
├── manifest.yaml           # 5-pair experiment config
├── agents/                 # Standalone ADK agents (one per model tier)
│   ├── lite_agent.py
│   ├── flash_agent.py
│   ├── pro_agent.py
│   ├── sonnet_agent.py
│   └── opus_agent.py
├── mcp_servers/            # MCP tool servers (Cloud Run)
│   ├── search/             # Flight + hotel search
│   ├── booking/            # Flight + hotel booking
│   └── expense/            # Expense management
├── eval_data/              # Eval cases (10 cases: low/medium/high complexity)
├── scripts/                # Deployment scripts
└── config.py               # Shared GCP config
```

## Quick Start

### 1. Deploy MCP Servers

```bash
# Deploy all 3 MCP servers to Cloud Run
bash examples/multi_model_agents/scripts/deploy_all.sh
```

### 2. Run the Experiment

```bash
# Full pipeline: deploy 5 agents → eval → optimize → redeploy → eval → report
wrangler run examples/multi_model_agents/manifest.yaml
```

### 3. Or Step by Step

```bash
# Deploy just one pair
wrangler deploy examples/multi_model_agents/manifest.yaml --pair flash-gemini-3.5-flash

# Eval a deployed agent
wrangler eval examples/multi_model_agents/manifest.yaml --engine-id <ID>

# Optimize a pair
wrangler optimize examples/multi_model_agents/manifest.yaml --pair flash-gemini-3.5-flash

# Generate report
wrangler report outputs/
```

## Agents

All agents share the same 3 MCP tool servers:
- **search-mcp**: `search_flights`, `search_hotels`
- **booking-mcp**: `book_flight`, `book_hotel`, `cancel_booking`
- **expense-mcp**: `submit_expense`, `check_expense_policy`, `get_user_expenses`

Each agent uses GEPA-optimized instructions tailored for its model's capability level.

## Eval Cases

10 eval cases across 3 complexity tiers:
- **Low (5 cases)**: Single tool call — flight search, hotel search, policy check, booking, expense history
- **Medium (3 cases)**: 2 tools — submit with policy check, flight comparison, search + policy cross-check
- **High (2 cases)**: 3+ tools — book + policy + expense pipeline, multi-route comparison with hotels
