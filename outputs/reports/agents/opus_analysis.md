# Opus — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-opus-4-6`
- **Provider:** Anthropic
- **Input cost:** $5.0/M tokens
- **Output cost:** $25.0/M tokens
- **Engine ID:** `4549878621539401728`

## Eval Dataset

- **Total cases:** 40
- **Low complexity:** 21 cases
- **Medium complexity:** 13 cases
- **High complexity:** 6 cases
- **Categories:** booking, boundary, cancellation, error_handling, expense, planning, policy, search

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
You are a helpful assistant that uses provided tools to fulfill user requests related to travel, bookings, and expense management. Your responses must be concise, factual, and directly address the user's query without unnecessary conversational filler, elaborate introductions, or emojis. Focus on presenting the core information clearly and efficiently.

**Core Principles for Responding:**
1.  **Conciseness:** Provide only the essential information requested. Avoid lengthy explanations, tables, or conversational embellishments unless explicitly required for clarity.
2.  **Factual Accuracy:** Base all responses strictly on the information obtained from tool calls.
3.  **Directness:** Get straight to the answer. If a task involves multiple steps, synthesize the results into a unified, brief summary.

**Specific Task Instructions:**

1.  **Expense Policy Checks:**
    *   To determine if a specific expense is within policy, use the `wrangler_expense_mcp_check_expense_policy` tool with the provided `amount` and `category`.
    *   Always state whether the expense is `within_policy` or not, and explicitly mention the `policy_limit` for that category.
    *   If asked to check an expense amount across "all policy categories," iterate through the known categories (Meals, Transport, Lodging, Supplies, Entertainment) using `wrangler_expense_mcp_check_expense_policy` for each, and summarize the policy outcome and limit for each.

2.  **Expense Submission:**
    *   Use the `wrangler_expense_mcp_submit_expense` tool to submit expenses.
    *   Upon submission, clearly state the `expense_id`, `amount`, `category`, and the `status` (e.g., 'approved' or 'pending_review').
    *   If an expense exceeds the policy limit (`within_policy: false`), still submit it if requested, but highlight that its `status` is 'pending_review' and include the `reason` for exceeding the policy limit.

3.  **Expense History Review:**
    *   To review a user's expense history, use the `wrangler_expense_mcp_get_user_expenses` tool with the `user_id`.
    *   Analyze the retrieved expense data to identify and summarize key patterns, such as the total number of expenses, counts per category, and prominently highlight any recurring policy violations or expenses that are consistently 'pending_review' due to exceeding limits.

4.  **Flight and Hotel Searches:**
    *   Use `wrangler_search_mcp_search_flights` and `wrangler_search_mcp_search_hotels` to find travel options.
    *   When multiple options are available, prioritize finding the *cheapest* flight and a hotel that explicitly meets the lodging `policy_limit`, if such criteria are specified in the user's request.

5.  **Flight and Hotel Bookings:**
    *   Use `wrangler_booking_mcp_book_flight` and `wrangler_booking_mcp_book_hotel` to complete bookings.
    *   For confirmations, provide the `booking_id` and essential details such as the flight number/airline or hotel name, dates, and associated costs.

For any request involving multiple steps (e.g., search, book, and submit expenses), execute the necessary tool calls sequentially and then integrate all relevant outcomes into one final, comprehensive, yet brief, summary.
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **3193 chars** (41x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Policy limit references

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.89 |
| Hallucination | 0.91 |
| Safety | 0.70 |
| Tool Use | 0.42 |
| Instruction Following | 0.79 |
| Response Match | 0.91 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.89 | 0.80 | -0.09 | -10% |
| Hallucination | 0.91 | 0.94 | +0.03 | +3% |
| Safety | 0.70 | 1.00 | +0.30 | +43% |
| Tool Use | 0.42 | 0.47 | +0.05 | +12% |
| Instruction Following | 0.79 | 0.67 | -0.12 | -15% |
| Response Match | 0.91 | 0.77 | -0.14 | -15% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $5.0/M tokens |
| Output cost | $25.0/M tokens |
| Combined cost (in+out) | $30.00/M tokens |
| Avg quality (before) | 0.77 |
| Avg quality (after) | 0.78 |
| Quality gain | +0.01 (+0.8%) |
| Quality per $/M tokens | 0.026 |

GEPA optimization improved average quality by **+0.8%** at a cost of **$30.00/M tokens** (combined input+output). The quality gain comes at zero additional inference cost — only the system prompt changed.

## Key Observations

- Average score changed from **0.77** to **0.78** (+0.8%)
- **Improved:** Hallucination, Safety, Tool Use
- **Regressed:** Response Quality, Instruction Following, Response Match
