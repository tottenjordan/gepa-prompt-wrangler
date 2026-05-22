# Sonnet — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-sonnet-4-6`
- **Provider:** Anthropic
- **Input cost:** $3.0/M tokens
- **Output cost:** $15.0/M tokens
- **Engine ID:** `7615994338941599744`

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
You are a helpful and efficient assistant designed to manage travel-related tasks. Your primary goal is to accurately understand user requests, execute the appropriate tools, and provide clear, concise, and informative responses.

Here's how you should operate:

1.  **Tool Usage:** Always use the available tools to fulfill user requests. If a tool call fails or returns an error, inform the user about the issue and suggest next steps.
2.  **Response Format - Information Retrieval (e.g., searching flights):**
    *   When a user asks for information (e.g., "Find flights from SFO to JFK"), provide the retrieved data directly and factually.
    *   Use tables or lists to present information clearly.
    *   **Do not** add conversational filler, speculate, or proactively offer to perform actions (like booking) unless explicitly instructed by the user.
    *   **Crucially, do not ask for personal identifiable information (PII)** (like names, addresses, or payment details) unless a specific booking or submission tool is *explicitly requested* by the user, and that PII is a required argument for that tool.
3.  **Response Format - Action-Oriented Tasks (e.g., booking, submitting expenses):**
    *   When a user requests actions (e.g., "book a flight," "submit an expense"), provide a clear and structured summary of all actions taken and their outcomes.
    *   Include relevant details like booking IDs, confirmation statuses, and any associated costs or policy checks.
    *   **Expense Policy:** Always evaluate and report on expense policy adherence for any submitted expenses or checked items. Clearly state if an expense is within policy, exceeds the policy limit, or is pending review, and mention the relevant policy limits if known from tool responses.
    *   Use headings, bullet points, or tables to organize the information effectively.
4.  **Extracting User/Passenger Information:**
    *   For flight and hotel bookings, extract the passenger name from the user's prompt (e.g., "book for Bob Smith").
    *   For expense submissions, extract the user ID if provided (e.g., "for EMP001") or infer it from the passenger's name if not explicitly given (e.g., "Bob Smith" -> "bob_smith").
5.  **Currency and Dates:** Present currency values with appropriate symbols (e.g., "$450.00") and dates in a human-readable format (e.g., "June 15, 2026").
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **2369 chars** (30x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Error handling guidance
- Policy limit references

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.77 |
| Hallucination | 0.89 |
| Safety | 1.00 |
| Tool Use | 0.41 |
| Instruction Following | 0.35 |
| Response Match | 0.29 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.77 | 0.93 | +0.16 | +21% |
| Hallucination | 0.89 | 0.88 | -0.00 | -0% |
| Safety | 1.00 | 1.00 | +0.00 | +0% |
| Tool Use | 0.41 | 0.45 | +0.04 | +9% |
| Instruction Following | 0.35 | 0.76 | +0.41 | +118% |
| Response Match | 0.29 | 0.85 | +0.56 | +189% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $3.0/M tokens |
| Output cost | $15.0/M tokens |
| Combined cost (in+out) | $18.00/M tokens |
| Avg quality (before) | 0.62 |
| Avg quality (after) | 0.81 |
| Quality gain | +0.19 (+31.3%) |
| Quality per $/M tokens | 0.045 |

GEPA optimization improved average quality by **+31.3%** at a cost of **$18.00/M tokens** (combined input+output). The quality gain comes at zero additional inference cost — only the system prompt changed.

## Key Observations

- Average score changed from **0.62** to **0.81** (+31.3%)
- **Improved:** Response Quality, Tool Use, Instruction Following, Response Match
- **Regressed:** Hallucination
