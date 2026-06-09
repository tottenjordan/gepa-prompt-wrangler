# Analysis Report — multi-model-v8

Generated: 2026-06-09T02:49:23

## Summary

| Pair | Model | Avg Before | Avg After | Delta | Verdict |
|------|-------|-----------|----------|-------|---------|
| flash-lite-gemini-3.1 | gemini-3.1-flash-lite | 0.698 | 0.695 | -0.003 | unchanged |
| flash-gemini-3.5 | gemini-3.5-flash | 0.711 | 0.700 | -0.012 | regressed |
| pro-gemini-3.1 | gemini-3.1-pro-preview | 0.650 | 0.639 | -0.011 | regressed |
| sonnet-claude-4 | claude-sonnet-4-6 | 0.635 | 0.649 | +0.014 | improved |
| opus-claude-4 | claude-opus-4-6 | 0.653 | 0.662 | +0.009 | improved |

## Per-Metric Breakdown

### Response Match (`final_response_match_v2`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.453 | 0.433 | -0.020 | no |
| flash-gemini-3.5 | 0.468 | 0.522 | +0.054 | no |
| pro-gemini-3.1 | 0.329 | 0.304 | -0.024 | no |
| sonnet-claude-4 | 0.208 | 0.383 | +0.175 | YES |
| opus-claude-4 | 0.300 | 0.335 | +0.035 | no |

### Quality (`final_response_quality_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.815 | 0.844 | +0.029 | no |
| flash-gemini-3.5 | 0.859 | 0.878 | +0.019 | no |
| pro-gemini-3.1 | 0.793 | 0.809 | +0.017 | no |
| sonnet-claude-4 | 0.855 | 0.763 | -0.092 | YES |
| opus-claude-4 | 0.888 | 0.854 | -0.034 | no |

### Hallucination (`hallucination_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.981 | 0.947 | -0.035 | YES |
| flash-gemini-3.5 | 0.950 | 0.876 | -0.074 | YES |
| pro-gemini-3.1 | 0.957 | 0.985 | +0.028 | no |
| sonnet-claude-4 | 0.923 | 0.863 | -0.060 | YES |
| opus-claude-4 | 0.860 | 0.915 | +0.055 | YES |

### Instruction (`instruction_following_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.551 | 0.523 | -0.028 | no |
| flash-gemini-3.5 | 0.623 | 0.535 | -0.088 | YES |
| pro-gemini-3.1 | 0.502 | 0.365 | -0.137 | YES |
| sonnet-claude-4 | 0.510 | 0.533 | +0.023 | no |
| opus-claude-4 | 0.557 | 0.524 | -0.033 | no |

### Safety (`safety_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.984 | 0.996 | +0.012 | no |
| flash-gemini-3.5 | 0.933 | 0.978 | +0.046 | no |
| pro-gemini-3.1 | 0.903 | 0.989 | +0.086 | YES |
| sonnet-claude-4 | 0.931 | 0.975 | +0.044 | YES |
| opus-claude-4 | 0.911 | 0.946 | +0.035 | no |

### Tool Use (`tool_use_quality_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| flash-lite-gemini-3.1 | 0.402 | 0.425 | +0.023 | no |
| flash-gemini-3.5 | 0.437 | 0.410 | -0.027 | no |
| pro-gemini-3.1 | 0.417 | 0.383 | -0.034 | YES |
| sonnet-claude-4 | 0.382 | 0.377 | -0.006 | no |
| opus-claude-4 | 0.404 | 0.400 | -0.004 | no |

## Prompt Changes

### flash-lite-gemini-3.1

- Original: 78 chars
- Optimized: 1342 chars
- Delta: +1264 chars (+1621%)
- Diff: +11 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### flash-gemini-3.5

- Original: 78 chars
- Optimized: 4249 chars
- Delta: +4171 chars (+5347%)
- Diff: +35 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### pro-gemini-3.1

- Original: 78 chars
- Optimized: 4139 chars
- Delta: +4061 chars (+5206%)
- Diff: +40 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### sonnet-claude-4

- Original: 78 chars
- Optimized: 1996 chars
- Delta: +1918 chars (+2459%)
- Diff: +14 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### opus-claude-4

- Original: 78 chars
- Optimized: 4911 chars
- Delta: +4833 chars (+6196%)
- Diff: +48 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

## Degradation Diagnosis

### flash-lite-gemini-3.1

Degraded metrics: Hallucination, Instruction, Response Match

- **Hallucination**: -0.035 (-4%)
- **Instruction**: -0.028 (-5%)
- **Response Match**: -0.020 (-4%)

### flash-gemini-3.5

Degraded metrics: Hallucination, Instruction, Tool Use

- **Hallucination**: -0.074 (-8%)
- **Instruction**: -0.088 (-14%)
- **Tool Use**: -0.027 (-6%)

### pro-gemini-3.1

Degraded metrics: Instruction, Response Match, Tool Use

- **Instruction**: -0.137 (-27%)
- **Response Match**: -0.024 (-7%)
- **Tool Use**: -0.034 (-8%)

### sonnet-claude-4

Degraded metrics: Quality, Hallucination, Tool Use

- **Quality**: -0.092 (-11%)
- **Hallucination**: -0.060 (-7%)
- **Tool Use**: -0.006 (-1%)

### opus-claude-4

Degraded metrics: Quality, Instruction

- **Quality**: -0.034 (-4%)
- **Instruction**: -0.033 (-6%)

## Per-Case Analysis

### flash-lite-gemini-3.1 — 26/64 cases degraded

| Case | Avg Delta | Worst Metric | Metric Delta |
|------|----------|-------------|-------------|
| 54 | -0.472 | instruction_following_v1 | -0.750 |
| 21 | -0.310 | final_response_match_v2 | -0.667 |
| 31 | -0.306 | instruction_following_v1 | -0.583 |
| 46 | -0.299 | instruction_following_v1 | -0.667 |
| 55 | -0.267 | instruction_following_v1 | -0.933 |
| 5 | -0.264 | final_response_quality_v1 | -0.500 |
| 9 | -0.247 | final_response_match_v2 | -1.000 |
| 38 | -0.215 | final_response_quality_v1 | -1.000 |
| 16 | -0.215 | tool_use_quality_v1 | -0.889 |
| 51 | -0.185 | final_response_match_v2 | -0.667 |
| 48 | -0.171 | final_response_match_v2 | -0.667 |
| 28 | -0.160 | final_response_match_v2 | -0.667 |
| 34 | -0.154 | final_response_match_v2 | -0.667 |
| 36 | -0.151 | tool_use_quality_v1 | -0.596 |
| 1 | -0.137 | final_response_match_v2 | -0.333 |

### flash-gemini-3.5 — 32/64 cases degraded

| Case | Avg Delta | Worst Metric | Metric Delta |
|------|----------|-------------|-------------|
| 63 | -0.457 | final_response_match_v2 | -1.000 |
| 59 | -0.381 | final_response_match_v2 | -0.667 |
| 5 | -0.381 | final_response_match_v2 | -0.667 |
| 19 | -0.374 | final_response_match_v2 | -0.667 |
| 45 | -0.321 | final_response_quality_v1 | -1.000 |
| 28 | -0.289 | instruction_following_v1 | -0.372 |
| 38 | -0.272 | instruction_following_v1 | -0.548 |
| 27 | -0.263 | final_response_match_v2 | -0.667 |
| 32 | -0.261 | tool_use_quality_v1 | -0.667 |
| 23 | -0.245 | instruction_following_v1 | -0.800 |
| 51 | -0.236 | final_response_match_v2 | -0.667 |
| 16 | -0.226 | instruction_following_v1 | -0.344 |
| 60 | -0.222 | instruction_following_v1 | -0.667 |
| 31 | -0.216 | final_response_match_v2 | -0.333 |
| 17 | -0.176 | final_response_quality_v1 | -0.500 |

### pro-gemini-3.1 — 30/64 cases degraded

| Case | Avg Delta | Worst Metric | Metric Delta |
|------|----------|-------------|-------------|
| 48 | -0.389 | final_response_match_v2 | -0.667 |
| 22 | -0.339 | instruction_following_v1 | -0.683 |
| 8 | -0.325 | final_response_quality_v1 | -1.000 |
| 50 | -0.269 | tool_use_quality_v1 | -1.000 |
| 2 | -0.262 | instruction_following_v1 | -0.739 |
| 28 | -0.261 | tool_use_quality_v1 | -1.000 |
| 49 | -0.258 | final_response_quality_v1 | -0.667 |
| 39 | -0.250 | instruction_following_v1 | -0.417 |
| 12 | -0.240 | final_response_quality_v1 | -1.000 |
| 59 | -0.201 | final_response_match_v2 | -0.333 |
| 35 | -0.200 | instruction_following_v1 | -0.656 |
| 55 | -0.192 | final_response_quality_v1 | -1.000 |
| 58 | -0.178 | safety_v1 | -0.333 |
| 29 | -0.156 | tool_use_quality_v1 | -0.333 |
| 16 | -0.150 | instruction_following_v1 | -0.450 |

### sonnet-claude-4 — 21/64 cases degraded

| Case | Avg Delta | Worst Metric | Metric Delta |
|------|----------|-------------|-------------|
| 56 | -0.289 | final_response_match_v2 | -0.667 |
| 26 | -0.282 | final_response_match_v2 | -0.500 |
| 5 | -0.270 | tool_use_quality_v1 | -0.583 |
| 27 | -0.222 | tool_use_quality_v1 | -0.414 |
| 39 | -0.171 | final_response_quality_v1 | -0.667 |
| 45 | -0.170 | instruction_following_v1 | -0.511 |
| 42 | -0.168 | final_response_match_v2 | -0.667 |
| 60 | -0.157 | tool_use_quality_v1 | -0.444 |
| 53 | -0.156 | instruction_following_v1 | -0.300 |
| 46 | -0.155 | final_response_quality_v1 | -0.417 |
| 44 | -0.149 | final_response_quality_v1 | -0.500 |
| 36 | -0.126 | instruction_following_v1 | -0.544 |
| 19 | -0.125 | final_response_match_v2 | -0.333 |
| 54 | -0.118 | instruction_following_v1 | -0.687 |
| 1 | -0.114 | instruction_following_v1 | -0.663 |

### opus-claude-4 — 19/64 cases degraded

| Case | Avg Delta | Worst Metric | Metric Delta |
|------|----------|-------------|-------------|
| 9 | -0.343 | final_response_match_v2 | -0.667 |
| 31 | -0.308 | tool_use_quality_v1 | -0.833 |
| 59 | -0.293 | final_response_quality_v1 | -1.000 |
| 22 | -0.239 | final_response_quality_v1 | -0.667 |
| 27 | -0.238 | instruction_following_v1 | -0.382 |
| 7 | -0.237 | final_response_quality_v1 | -0.500 |
| 0 | -0.219 | final_response_quality_v1 | -0.750 |
| 61 | -0.208 | final_response_match_v2 | -0.667 |
| 56 | -0.204 | instruction_following_v1 | -0.505 |
| 51 | -0.201 | instruction_following_v1 | -0.702 |
| 2 | -0.173 | final_response_quality_v1 | -0.500 |
| 35 | -0.149 | instruction_following_v1 | -0.448 |
| 18 | -0.122 | instruction_following_v1 | -0.356 |
| 30 | -0.109 | final_response_match_v2 | -0.333 |
| 34 | -0.107 | final_response_match_v2 | -0.667 |

## MCP Tool Usage Audit

### GEPA Run Log Summary

| Pair | Log Lines | Errors | Warnings | Timeouts | Tool Failures |
|------|----------|--------|----------|----------|---------------|
| flash-lite-gemini-3.1 | 14362 | 2044 | 4 | 68 | 313 |
| flash-gemini-3.5 | 14299 | 1655 | 9 | 52 | 246 |
| pro-gemini-3.1 | 13677 | 1602 | 3 | 20 | 242 |
| sonnet-claude-4 | 14047 | 1641 | 4 | 28 | 251 |
| opus-claude-4 | 12998 | 1494 | 2 | 16 | 197 |

**184 total MCP timeouts** across all runs — 
GEPA iterations with tool timeouts run without tool outputs, 
reducing optimization signal for tool-dependent behavior.

**1249 total tool acquisition failures** — 
these iterations could not call any MCP tools.

### Tool Keyword Preservation

Checks whether optimized prompts retained tool-related terminology.

| Pair | Original Keywords | Optimized Keywords | Added | Dropped |
|------|------------------|-------------------|-------|---------|
| flash-lite-gemini-3.1 | tool | flight, hotel, mcp, search, tool | flight, hotel, mcp, search | — |
| flash-gemini-3.5 | tool | book, check, expense, flight, hotel, mcp, policy, search, submit, tool | book, check, expense, flight, hotel, mcp, policy, search, submit | — |
| pro-gemini-3.1 | tool | book, check, flight, hotel, mcp, search, tool | book, check, flight, hotel, mcp, search | — |
| sonnet-claude-4 | tool | book, check, expense, hotel, mcp, policy, search, tool | book, check, expense, hotel, mcp, policy, search | — |
| opus-claude-4 | tool | book, cancel, check, expense, flight, hotel, mcp, policy, search, submit, tool | book, cancel, check, expense, flight, hotel, mcp, policy, search, submit | — |

## Cost Efficiency

| Pair | Cost ($/M) | Avg Delta | Cost per +0.01 |
|------|-----------|----------|----------------|
| flash-lite-gemini-3.1 | $0.30 | -0.003 | regressed |
| flash-gemini-3.5 | $0.60 | -0.012 | regressed |
| pro-gemini-3.1 | $10.00 | -0.011 | regressed |
| sonnet-claude-4 | $15.00 | +0.014 | $10.67 |
| opus-claude-4 | $75.00 | +0.009 | $82.39 |

## Recommendations

1. **Add thresholds for**: final_response_match_v2, final_response_quality_v1, hallucination_v1, instruction_following_v1, safety_v1, tool_use_quality_v1
   Metrics without thresholds default to 0.0 in GEPA — no optimization pressure.

2. **Investigate metric name alignment** between GEPA local eval and cloud eval.
   Mismatched names mean GEPA optimizes for different metrics than cloud reports.
