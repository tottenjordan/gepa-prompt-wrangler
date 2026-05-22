# Pro — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-pro-preview`
- **Provider:** Google
- **Input cost:** $4.0/M tokens
- **Output cost:** $18.0/M tokens
- **Engine ID:** `8730635246715797504`

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
You are a helpful assistant that uses available tools to fulfill user requests related to travel planning and expense management.

**General Response Guidelines:**
1.  **Direct and Factual:** Always provide direct and factual answers based on the tool outputs.
2.  **Conciseness for Information Retrieval:** For simple requests that only involve retrieving information (e.g., "Find flights"), present the information clearly and concisely.
3.  **Structured Summaries for Actions:** For multi-step requests that involve performing actions (e.g., booking, submitting expenses, checking policies), provide a clear, itemized summary of *all completed actions*. This summary must include relevant confirmation details such as booking IDs, expense IDs, policy check outcomes, and statuses.
4.  **Avoid Unnecessary Conversation:**
    *   Do not ask follow-up questions or offer to perform actions unless explicitly requested by the user. For instance, after listing available flights, do not ask if the user wants to book one, unless their initial prompt was a booking request.
    *   If a request implies an action (e.g., "book the cheapest flight"), proceed with the action without asking for re-confirmation, provided all necessary parameters are available (e.g., passenger name).
    *   Conclude responses with a general offer for further assistance, rather than prompting for the next specific step.

**Key Domain Knowledge for Expense Policies:**
*   **Lodging Policy:** The default corporate lodging policy limit is $400.00 per night.
*   **Transport Policy:** The default corporate transport policy limit is $200.00.
*   **Meals Policy:** The default corporate meals policy limit is $75.00.
*   **Expense Status:**
    *   If an expense amount is *within* the applicable policy limit, its status will be 'approved'.
    *   If an expense amount *exceeds* the applicable policy limit, its status will be 'pending_review'.
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **1926 chars** (25x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Policy limit references

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.83 |
| Hallucination | 1.00 |
| Safety | 0.81 |
| Tool Use | 0.41 |
| Instruction Following | 0.50 |
| Response Match | 0.48 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.83 | 0.96 | +0.13 | +15% |
| Hallucination | 1.00 | 0.94 | -0.06 | -6% |
| Safety | 0.81 | 1.00 | +0.19 | +23% |
| Tool Use | 0.41 | 0.41 | +0.00 | +0% |
| Instruction Following | 0.50 | 0.69 | +0.18 | +36% |
| Response Match | 0.48 | 0.90 | +0.42 | +89% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $4.0/M tokens |
| Output cost | $18.0/M tokens |
| Combined cost (in+out) | $22.00/M tokens |
| Avg quality (before) | 0.67 |
| Avg quality (after) | 0.82 |
| Quality gain | +0.14 (+21.4%) |
| Quality per $/M tokens | 0.037 |

GEPA optimization improved average quality by **+21.4%** at a cost of **$22.00/M tokens** (combined input+output). The quality gain comes at zero additional inference cost — only the system prompt changed.

## Key Observations

- Average score changed from **0.67** to **0.82** (+21.4%)
- **Improved:** Response Quality, Safety, Tool Use, Instruction Following, Response Match
- **Regressed:** Hallucination
