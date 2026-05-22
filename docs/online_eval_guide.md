# Online Evaluation Guide — Evaluators vs Monitors

## Overview

The wrangler provides two complementary online evaluation mechanisms for deployed agents:

| | Online Evaluators | Online Monitors |
|---|---|---|
| **What** | Automatically score OTel traces from live traffic | Run on-demand evaluations against deployed agents |
| **When** | Every 10 minutes (automatic) | On-demand (you trigger it) |
| **Input** | OTel traces from any traffic (playground, API, traffic generator) | Predefined eval cases sent directly to the agent |
| **Output** | Scores in Observability tab, Cloud Logging, Cloud Monitoring | Scores saved to JSON + GCS |
| **Persistence** | Cloud Logging (queryable via Log Explorer) | Local JSON + GCS eval results |
| **Use case** | Continuous quality monitoring of production traffic | Spot-check agent health, regression testing |

## Online Evaluators

Online Evaluators are **always-on scoring engines** that process OTel traces from your deployed agents. They run server-side every 10 minutes.

### How they work

1. Agent receives traffic (playground, API calls, traffic generator)
2. Agent Engine emits OTel traces to Cloud Trace
3. Online Evaluator picks up traces every 10 minutes
4. Each trace is scored against predefined + custom metrics
5. Results appear in the agent's **Observability tab** in the console

### Metrics

**Predefined metrics** (built-in):
- `final_response_quality_v1` — overall response quality
- `hallucination_v1` — factual accuracy
- `safety_v1` — content safety
- `tool_use_quality_v1` — correct tool selection and parameters

**Custom LLM metrics** (registered via Metric Registry):
- `Wrangler Task Quality` — domain-specific scoring rubric

### Commands

```bash
# List all online evaluators
uv run python -m wrangler.online_evaluators list

# Create evaluators for all deployed wrangler agents
uv run python -m wrangler.online_evaluators create

# Verify evaluators are active and check results
uv run python -m wrangler.online_evaluators verify

# Delete a specific evaluator
uv run python -m wrangler.online_evaluators delete <evaluator_id>

# Remove all wrangler evaluators and custom metrics
uv run python -m wrangler.online_evaluators cleanup
```

### Prerequisites

- Agents must be deployed to Agent Engine
- Engine IDs must be set in `.env` (`LITE_ENGINE_ID`, `FLASH_ENGINE_ID`, etc.)
- `PROJECT_NUMBER` must be set in `.env`
- Traffic must be flowing to generate traces for the evaluator to score

## Online Monitors

Online Monitors are **on-demand evaluation runs** that send a set of test queries to a deployed agent and score the responses.

### How they work

1. You trigger a monitor run with an engine ID
2. Monitor sends predefined queries to the deployed agent
3. Agent processes queries using its tools (MCP servers)
4. Responses are evaluated server-side via `create_evaluation_run`
5. Results saved locally and to GCS

### When to use

- **Health checks**: Quick spot-check that an agent is responding correctly
- **Before/after comparison**: Run before and after a prompt change
- **Regression testing**: Verify a redeployment didn't break anything
- **CI/CD**: Automated quality gate in deployment pipelines

### Commands

```bash
# Run monitor against a specific agent
uv run python -m wrangler.online_monitors <engine-id>

# Run with fewer cases (faster)
uv run python -m wrangler.online_monitors <engine-id> --cases 3
```

### Output

Results are saved to `outputs/monitors/monitor_<timestamp>.json`:

```json
{
  "agent_id": "4981388556929859584",
  "run_id": "monitor_20260522_140000",
  "timestamp": "2026-05-22T14:00:00",
  "num_cases": 8,
  "scores": {
    "final_response_quality_v1": 0.85,
    "safety_v1": 0.92,
    "tool_use_quality_v1": 0.40
  }
}
```

## Recommended Setup

For a complete monitoring strategy:

1. **Create online evaluators** for all agents → continuous scoring of live traffic
2. **Generate traffic** periodically → ensures evaluators have traces to score
3. **Run monitors** before/after prompt changes → regression detection
4. **Check the Observability tab** → visual dashboard of agent quality over time

```bash
# One-time setup
uv run python -m wrangler.online_evaluators create

# Periodic health check
uv run python -m wrangler.online_monitors $LITE_ENGINE_ID
uv run python -m wrangler.online_monitors $FLASH_ENGINE_ID

# Verify evaluators are working
uv run python -m wrangler.online_evaluators verify
```
