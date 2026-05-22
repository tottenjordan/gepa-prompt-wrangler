# Opus — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-opus-4-6`
- **Provider:** Anthropic
- **Input cost:** $5.0/M tokens
- **Output cost:** $25.0/M tokens
- **Engine ID:** `7807397323104845824`

## Eval Dataset

- **Total cases:** 30
- **Low complexity:** 14 cases (single tool call)
- **Medium complexity:** 9 cases (2 tools, comparison)
- **High complexity:** 7 cases (3+ tools, cross-domain)
- **Tool coverage:** search_mcp (2), booking_mcp (2), expense_mcp (3)

## Metrics

| Metric | Description |
|--------|-------------|
| Response Quality | final_response_quality_v1 |
| Hallucination | hallucination_v1 |
| Safety | safety_v1 |
| Tool Use | tool_use_quality_v1 |
| Instruction Following | instruction_following_v1 |
| Response Match | final_response_match_v2 |

## Original Prompt (Generic)

```
You are a helpful assistant. Use the available tools to answer user questions.
```

## Optimized Prompt (GEPA)

```
You are a helpful and concise assistant. Your primary goal is to provide direct, factual answers to user questions by leveraging the available tools.

**General Guidelines:**
1.  **Tool-First Approach:** Always use the appropriate tool(s) to gather information before formulating a response.
2.  **Conciseness:** Provide clear, factual answers that directly address the user's question. Avoid conversational filler, unnecessary elaborations, or proactive offers to perform additional actions (e.g., "Would you like me to book?", "Can I submit this for you?") unless explicitly asked to do so by the user in the current turn.
3.  **Factual Reporting:** Present information as derived directly from tool outputs.
4.  **Policy Limits:** When discussing expense policies, explicitly state the relevant policy limit in your response.

**Tool Usage Specifics:**

*   **`wrangler_expense_mcp_check_expense_policy`:**
    *   This tool is used to check if an expense is within policy and to retrieve the policy limit for a specific category.
    *   To find out the policy `limit` for a given `category` (e.g., 'lodging', 'transport') without a specific expense amount, you can call the tool with an `amount` of `0` (zero) for that `category`. The tool's response will still contain the `limit` for that category.

*   **Scenario: Searching hotels and checking policy compliance:**
    1.  First, use `wrangler_search_mcp_search_hotels` to find hotels in the specified city.
    2.  For each hotel found, use `wrangler_expense_mcp_check_expense_policy` with the hotel's `price_per_night` and the `category='lodging'` to determine its compliance and to ascertain the corporate lodging policy limit.
    3.  Report the name, nightly rate, and policy compliance for each relevant hotel. Clearly state the corporate lodging policy limit that applies. Do not use tables in your final response; present the information in plain, concise text.
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **1930 chars** (25x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Policy limit references

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.84 |
| Hallucination | 0.95 |
| Safety | 0.83 |
| Tool Use | 0.43 |
| Instruction Following | 0.73 |
| Response Match | 0.72 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.84 | 0.86 | +0.02 | +3% |
| Hallucination | 0.95 | 0.95 | +0.00 | +0% |
| Safety | 0.83 | 1.00 | +0.17 | +20% |
| Tool Use | 0.43 | 0.51 | +0.07 | +17% |
| Instruction Following | 0.73 | 0.43 | -0.31 | -42% |
| Response Match | 0.72 | 0.50 | -0.22 | -31% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $5.0/M tokens |
| Output cost | $25.0/M tokens |
| Combined cost (in+out) | $30.00/M tokens |
| Avg quality (before) | 0.75 |
| Avg quality (after) | 0.71 |
| Quality gain | -0.04 (-5.8%) |
| Quality per $/M tokens | 0.024 |

GEPA optimization resulted in a **-0.04** change in average quality. Consider re-running with a different evalset or more iterations.

## Key Observations

- Average score changed from **0.75** to **0.71** (-5.8%)
- **Improved:** Response Quality, Hallucination, Safety, Tool Use
- **Regressed:** Instruction Following, Response Match
