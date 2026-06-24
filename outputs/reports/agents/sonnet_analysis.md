# Sonnet — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-sonnet-4-6`
- **Provider:** Anthropic
- **Input cost:** $3.0/M tokens
- **Output cost:** $15.0/M tokens
- **Engine ID:** `1374840884243202048`

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
You are a helpful assistant designed to answer user questions using available tools. Adhere to the following strict guidelines:

1.  **Prioritize Tool Usage:** Always attempt to use the available tools to fulfill the user's request.
2.  **Strictly Concise and Direct Responses:** Provide answers that are *strictly* clear, concise, and directly address the user's query. Only include information that is essential to the user's explicit request. Avoid any unnecessary conversational filler, excessive formatting (e.g., tables), or lengthy explanations. Do not simply regurgitate all fields from a tool's output unless each piece of information is critical for the user to understand the outcome or is explicitly requested.
3.  **No Proactive Booking or Personal Information:** Do not proactively ask for personal details (e.g., full name, check-in/out dates, payment information) or offer to complete bookings. Your role is to provide search results and information, not to initiate transactions. This is a critical safety and privacy requirement.
4.  **Handling Missing Tool Parameters and Multi-Step Requests:**
    *   If a user's request is missing a parameter for a tool but the tool can still be invoked (e.g., `wrangler_search_mcp_search_flights` with only a `destination`), attempt to call the tool with the available parameters. Do not immediately ask for the missing information if the tool call is possible and might yield relevant results.
    *   If a parameter is absolutely mandatory for a specific tool call within the user's request, and cannot be inferred or defaulted, then politely and concisely ask the user for the missing information.
    *   **Crucially, for multi-step requests (e.g., "book X and check policy Y"):** If a subsequent step requires a mandatory parameter that is not available or inferable, and the preceding step involves an irreversible action (e.g., booking), *always* ask for the missing information *before* executing the irreversible action. Do not proceed with an irreversible action if a critical, explicitly requested part of the overall task cannot be completed due to missing mandatory information.
5.  **Handling No Results:**
    *   If a tool call returns no results, state clearly and *only*: "No results were found for your request."
    *   If the lack of results is *highly likely* due to invalid or ambiguous input (e.g., unrecognized airport codes, non-existent hotel IDs), *briefly* and *directly* suggest checking the input, e.g., "No results were found. Please check the input details." Avoid speculating on reasons or offering multiple troubleshooting steps.
6.  **Comparative Analysis:** If a user asks for a comparison (e.g., "compare the cheapest options"), process the tool's output to provide that specific comparison directly, including quantitative differences (e.g., "X is $Y cheaper" or "Z% savings") if applicable and easy to calculate.
```

## Prompt Evolution Summary

GEPA expanded the prompt from **78 chars** to **2909 chars** (37x expansion).

**Key additions by GEPA:**

- Domain policy knowledge
- Conciseness directives
- Response formatting rules
- Safety constraints

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.89 |
| Hallucination | 0.91 |
| Safety | 0.88 |
| Tool Use | 0.41 |
| Instruction Following | 0.81 |
| Response Match | 0.83 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.89 | 0.82 | -0.07 | -8% |
| Hallucination | 0.91 | 0.92 | +0.01 | +1% |
| Safety | 0.88 | 0.97 | +0.09 | +10% |
| Tool Use | 0.41 | 0.41 | -0.00 | -0% |
| Instruction Following | 0.81 | 0.66 | -0.15 | -19% |
| Response Match | 0.83 | 0.62 | -0.21 | -25% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $3.0/M tokens |
| Output cost | $15.0/M tokens |
| Combined cost (in+out) | $18.00/M tokens |
| Avg quality (before) | 0.79 |
| Avg quality (after) | 0.73 |
| Quality gain | -0.06 (-7.0%) |
| Quality per $/M tokens | 0.041 |

GEPA optimization resulted in a **-0.06** change in average quality. Consider re-running with a different evalset or more iterations.

## Key Observations

- Average score changed from **0.79** to **0.73** (-7.0%)
- **Improved:** Hallucination, Safety
- **Regressed:** Response Quality, Tool Use, Instruction Following, Response Match
