# Analysis Report — multi-model-v6

Generated: 2026-06-06T04:26:22

## Summary

| Pair | Model | Avg Before | Avg After | Delta | Verdict |
|------|-------|-----------|----------|-------|---------|
| lite-gemini-3.1-flash-lite | gemini-3.1-flash-lite | 0.749 | 0.735 | -0.014 | regressed |
| flash-gemini-3.5-flash | gemini-3.5-flash | 0.753 | 0.745 | -0.008 | regressed |
| pro-gemini-3.1-pro | gemini-3.1-pro-preview | 0.738 | 0.725 | -0.013 | regressed |
| sonnet-claude-4 | claude-sonnet-4-6 | 0.750 | 0.750 | -0.000 | unchanged |
| opus-claude-4 | claude-opus-4-6 | 0.744 | 0.755 | +0.011 | improved |

## Per-Metric Breakdown

### Response Match (`final_response_match_v2`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.653 | 0.604 | -0.050 | no |
| flash-gemini-3.5-flash | 0.629 | 0.613 | -0.016 | no |
| pro-gemini-3.1-pro | 0.614 | 0.537 | -0.077 | no |
| sonnet-claude-4 | 0.663 | 0.645 | -0.018 | no |
| opus-claude-4 | 0.652 | 0.723 | +0.070 | YES |

### Quality (`final_response_quality_v1`)

GEPA threshold: **0.7**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.823 | 0.817 | -0.006 | no |
| flash-gemini-3.5-flash | 0.818 | 0.852 | +0.034 | no |
| pro-gemini-3.1-pro | 0.818 | 0.856 | +0.038 | YES |
| sonnet-claude-4 | 0.836 | 0.864 | +0.028 | no |
| opus-claude-4 | 0.840 | 0.847 | +0.007 | no |

### Hallucination (`hallucination_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.964 | 0.960 | -0.004 | no |
| flash-gemini-3.5-flash | 0.969 | 0.950 | -0.019 | YES |
| pro-gemini-3.1-pro | 0.971 | 0.975 | +0.005 | no |
| sonnet-claude-4 | 0.932 | 0.919 | -0.013 | no |
| opus-claude-4 | 0.885 | 0.893 | +0.008 | no |

### Instruction (`instruction_following_v1`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.620 | 0.620 | -0.000 | no |
| flash-gemini-3.5-flash | 0.665 | 0.625 | -0.039 | no |
| pro-gemini-3.1-pro | 0.597 | 0.566 | -0.032 | no |
| sonnet-claude-4 | 0.673 | 0.669 | -0.004 | no |
| opus-claude-4 | 0.731 | 0.737 | +0.006 | no |

### Safety (`safety_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.993 | 0.994 | +0.001 | no |
| flash-gemini-3.5-flash | 0.985 | 0.980 | -0.005 | no |
| pro-gemini-3.1-pro | 0.983 | 0.987 | +0.004 | no |
| sonnet-claude-4 | 0.974 | 0.971 | -0.004 | no |
| opus-claude-4 | 0.934 | 0.885 | -0.049 | YES |

### Tool Use (`tool_use_quality_v1`)

GEPA threshold: **0.3**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.438 | 0.416 | -0.022 | no |
| flash-gemini-3.5-flash | 0.453 | 0.450 | -0.003 | no |
| pro-gemini-3.1-pro | 0.446 | 0.431 | -0.015 | no |
| sonnet-claude-4 | 0.422 | 0.432 | +0.010 | no |
| opus-claude-4 | 0.422 | 0.446 | +0.023 | no |

## Prompt Changes

### lite-gemini-3.1-flash-lite

- Original: 78 chars
- Optimized: 1342 chars
- Delta: +1264 chars (+1621%)
- Diff: +11 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### flash-gemini-3.5-flash

- Original: 78 chars
- Optimized: 4249 chars
- Delta: +4171 chars (+5347%)
- Diff: +35 lines / -1 lines

**Removed content with policy/tool keywords** (1 lines):

  - `You are a helpful assistant. Use the available tools to answer user questions.`

### pro-gemini-3.1-pro

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

### lite-gemini-3.1-flash-lite

Degraded metrics: Tool Use, Quality, Response Match

- **Tool Use**: -0.022 (-5%)
- **Quality**: -0.006 (-1%)
- **Response Match**: -0.050 (-8%)

### flash-gemini-3.5-flash

Degraded metrics: Hallucination, Response Match, Safety, Instruction

- **Hallucination**: -0.019 (-2%)
- **Response Match**: -0.016 (-3%)
- **Safety**: -0.005 (-1%)
- **Instruction**: -0.039 (-6%)

### pro-gemini-3.1-pro

Degraded metrics: Tool Use, Response Match, Instruction

- **Tool Use**: -0.015 (-3%)
- **Response Match**: -0.077 (-13%)
- **Instruction**: -0.032 (-5%)

### sonnet-claude-4

Degraded metrics: Hallucination, Response Match

- **Hallucination**: -0.013 (-1%)
- **Response Match**: -0.018 (-3%)

### opus-claude-4

Degraded metrics: Safety

- **Safety**: -0.049 (-5%)

## Threshold Alignment Check

Checks whether GEPA thresholds are calibrated against baseline scores.

| Metric | Threshold | Min Baseline | Gap | Status |
|--------|-----------|-------------|-----|--------|
| final_response_match_v2 | 0.5 | 0.614 | +0.114 | OK |
| final_response_quality_v1 | 0.7 | 0.818 | +0.118 | OK |
| hallucination_v1 | 0.8 | 0.885 | +0.085 | OK |
| instruction_following_v1 | 0.5 | 0.597 | +0.097 | OK |
| safety_v1 | 0.8 | 0.934 | +0.134 | OK |
| tool_use_quality_v1 | 0.3 | 0.422 | +0.122 | OK |

## Per-Case Analysis

*Per-case scores not available for this experiment. Future runs with updated eval
extraction will enable per-case degradation tracking.*

## MCP Tool Usage Audit

### GEPA Run Log Summary

| Pair | Log Lines | Errors | Warnings | Timeouts | Tool Failures |
|------|----------|--------|----------|----------|---------------|
| lite-gemini-3.1-flash-lite | 7145 | 1027 | 4 | 44 | 25 |
| flash-gemini-3.5-flash | 5558 | 531 | 8 | 36 | 19 |
| pro-gemini-3.1-pro | 4764 | 458 | 3 | 20 | 10 |
| sonnet-claude-4 | 4900 | 468 | 4 | 24 | 13 |
| opus-claude-4 | 4744 | 445 | 2 | 12 | 8 |

**136 total MCP timeouts** across all runs — 
GEPA iterations with tool timeouts run without tool outputs, 
reducing optimization signal for tool-dependent behavior.

**75 total tool acquisition failures** — 
these iterations could not call any MCP tools.

### Tool Keyword Preservation

Checks whether optimized prompts retained tool-related terminology.

| Pair | Original Keywords | Optimized Keywords | Added | Dropped |
|------|------------------|-------------------|-------|---------|
| lite-gemini-3.1-flash-lite | tool | flight, hotel, mcp, search, tool | flight, hotel, mcp, search | — |
| flash-gemini-3.5-flash | tool | book, check, expense, flight, hotel, mcp, policy, search, submit, tool | book, check, expense, flight, hotel, mcp, policy, search, submit | — |
| pro-gemini-3.1-pro | tool | book, check, flight, hotel, mcp, search, tool | book, check, flight, hotel, mcp, search | — |
| sonnet-claude-4 | tool | book, check, expense, hotel, mcp, policy, search, tool | book, check, expense, hotel, mcp, policy, search | — |
| opus-claude-4 | tool | book, cancel, check, expense, flight, hotel, mcp, policy, search, submit, tool | book, cancel, check, expense, flight, hotel, mcp, policy, search, submit | — |

## Cost Efficiency

| Pair | Cost ($/M) | Avg Delta | Cost per +0.01 |
|------|-----------|----------|----------------|
| lite-gemini-3.1-flash-lite | $0.30 | -0.014 | regressed |
| flash-gemini-3.5-flash | $0.60 | -0.008 | regressed |
| pro-gemini-3.1-pro | $10.00 | -0.013 | regressed |
| sonnet-claude-4 | $15.00 | -0.000 | no change |
| opus-claude-4 | $75.00 | +0.011 | $68.12 |

## Recommendations

1. **Investigate metric name alignment** between GEPA local eval and cloud eval.
   Mismatched names mean GEPA optimizes for different metrics than cloud reports.
