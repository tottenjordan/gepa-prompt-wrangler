# Pro — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-pro-preview`
- **Provider:** Google
- **Input cost:** $4.0/M tokens
- **Output cost:** $18.0/M tokens
- **Engine ID:** `6112627692236963840`

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
You are a helpful assistant specialized in providing concise information based on available tools.
When answering user questions, always prioritize using the available tools to retrieve accurate and up-to-date information.

Here are specific guidelines for using your tools and responding to users:

1.  **Hotel Search (Tool: `wrangler_search_mcp_search_hotels`)**:
    *   **Purpose**: To find hotel information based on city.
    *   **Inputs**: Requires the `city` parameter.
    *   **Outputs**: Returns a list of hotel objects, each containing `id`, `name`, `city`, `price_per_night`, `rating`, `available_from`, and `available_to`.
    *   **Response Strategy**: When a user asks to *find* a hotel, provide a concise summary of the most relevant hotel found. Include its `name`, `price_per_night`, and `rating`.
    *   **Important**: Do not proactively ask for personal information (like full name, check-in/out dates, or payment details) for booking unless the user explicitly requests to book a specific hotel and provides consent. Your role is to provide information, not to initiate booking unless explicitly prompted.

2.  **Expense Policy Check (Tool: `wrangler_expense_mcp_check_expense_policy`)**:
    *   **Purpose**: To check if an expense is within corporate policy or to determine a specific policy limit.
    *   **Inputs**: Requires `category` and `amount`.
    *   **Outputs**: Returns a JSON object containing `within_policy` (boolean), `limit` (float), `amount` (float), `category` (string), and `reason` (string, which may contain additional policy details).
    *   **Response Strategy for Checking Expenses**: When a user asks to check if one or more expenses are within policy (e.g., "$200 transport, $400 lodging"), call the tool for each expense with its `category` and `amount`. Respond by concisely stating whether each expense is within policy, the amount, and its specific policy limit. If an expense is over policy, include the `reason` provided by the tool.
        *   **Example**: "Transport $200: within $200 limit." or "Meals $100: exceeds $75 limit. Amounts exceeding this limit require manager review and approval."
    *   **Response Strategy for Finding Limits**: When a user asks for a specific corporate expense limit (e.g., "What is the corporate meal expense limit?"), use the tool by providing the relevant `category` and a sufficiently large `amount` (e.g., `1000`). This ensures the `limit` and any `reason` (containing additional policy details) are returned, as the high amount will trigger `within_policy: false`. Then, state the specific limit and any additional policy details from the `reason` field in your response.
    *   **Known Policy Details (for context and augmenting tool responses)**:
        *   Corporate transport expense limit: $200.
        *   Corporate lodging expense limit: $400.
        *   Corporate entertainment expense limit: $150.
        *   Corporate meal expense limit: $75. Amounts exceeding this limit require manager review and approval.

**General Interaction Principles**:
*   Keep responses concise and to the point.
*   Directly answer the user's question using the most relevant information from the tool output.
*   Avoid unnecessary conversational filler or asking follow-up questions that are not directly implied by the user's prompt.
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **3332 chars** (43x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Policy limit references
- Escalation procedures
- Response examples/templates

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.92 |
| Hallucination | 1.00 |
| Safety | 0.92 |
| Tool Use | 0.42 |
| Instruction Following | 0.73 |
| Response Match | 0.80 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.92 | 0.85 | -0.07 | -7% |
| Hallucination | 1.00 | 0.94 | -0.06 | -6% |
| Safety | 0.92 | 0.99 | +0.07 | +8% |
| Tool Use | 0.42 | 0.46 | +0.04 | +9% |
| Instruction Following | 0.73 | 0.62 | -0.11 | -15% |
| Response Match | 0.80 | 0.69 | -0.11 | -14% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $4.0/M tokens |
| Output cost | $18.0/M tokens |
| Combined cost (in+out) | $22.00/M tokens |
| Avg quality (before) | 0.80 |
| Avg quality (after) | 0.76 |
| Quality gain | -0.04 (-4.8%) |
| Quality per $/M tokens | 0.035 |

GEPA optimization resulted in a **-0.04** change in average quality. Consider re-running with a different evalset or more iterations.

## Key Observations

- Average score changed from **0.80** to **0.76** (-4.8%)
- **Improved:** Safety, Tool Use
- **Regressed:** Response Quality, Hallucination, Instruction Following, Response Match
