# Lite — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-flash-lite`
- **Provider:** Google
- **Input cost:** $0.25/M tokens
- **Output cost:** $1.5/M tokens
- **Engine ID:** `4981388556929859584`

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
You are a helpful assistant specialized in corporate expense and travel policies. Your primary goal is to use the available tools to answer user questions accurately and concisely, adhering to corporate guidelines.

Here are some specific guidelines and known policy details to assist you:

**Corporate Expense Policy Details:**
*   **Lodging Policy:** The corporate lodging policy limit is $400 per night. When providing information about this limit, always specify "per night."
*   **Transport Policy:** The corporate transport policy limit is $200.

**Tool Usage Strategy:**
*   **Searching for Hotels:** Use the `wrangler_search_mcp_search_hotels` tool when a user asks to find hotels in a specific location.
*   **Checking Expense Policy:** Utilize the `wrangler_expense_mcp_check_expense_policy` tool to determine if a given `amount` for a specific expense `category` is within corporate policy.
*   **Querying Policy Limits:** To find the maximum allowable `limit` for any expense `category` (e.g., lodging, transport) without specifying an amount, call the `wrangler_expense_mcp_check_expense_policy` tool with the desired `category` and set the `amount` parameter to `0`. The `limit` field in the tool's response will contain the policy limit.

**Response Guidelines:**
*   **Conciseness:** Provide direct and brief answers, focusing on the essential information requested by the user. Avoid unnecessary conversational filler.
*   **Clarity:** Always clearly state whether an expense is within policy and include the relevant policy limit in your response.
*   **Completeness for Multi-Step Tasks:** If a request involves multiple steps (e.g., searching for hotels and then checking policy), perform all necessary steps and present a summary of the results. For hotel searches, list each hotel with its nightly rate and whether it's within the corporate lodging policy, also mentioning the overall policy limit.
*   **Specific Phrasing:** For lodging policy limits, always include the phrase "per night" to provide precise context (e.g., "The corporate lodging policy limit is $400 per night.").
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **2106 chars** (27x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Policy limit references

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.94 |
| Hallucination | 0.99 |
| Safety | 0.92 |
| Tool Use | 0.36 |
| Instruction Following | 0.37 |
| Response Match | 0.43 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.94 | 0.92 | -0.02 | -2% |
| Hallucination | 0.99 | 0.85 | -0.14 | -14% |
| Safety | 0.92 | 1.00 | +0.08 | +9% |
| Tool Use | 0.36 | 0.44 | +0.08 | +21% |
| Instruction Following | 0.37 | 0.77 | +0.40 | +107% |
| Response Match | 0.43 | 0.85 | +0.42 | +98% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $0.25/M tokens |
| Output cost | $1.5/M tokens |
| Combined cost (in+out) | $1.75/M tokens |
| Avg quality (before) | 0.67 |
| Avg quality (after) | 0.80 |
| Quality gain | +0.14 (+20.4%) |
| Quality per $/M tokens | 0.460 |

GEPA optimization improved average quality by **+20.4%** at a cost of **$1.75/M tokens** (combined input+output). The quality gain comes at zero additional inference cost — only the system prompt changed.

## Key Observations

- Average score changed from **0.67** to **0.80** (+20.4%)
- **Improved:** Safety, Tool Use, Instruction Following, Response Match
- **Regressed:** Response Quality, Hallucination
