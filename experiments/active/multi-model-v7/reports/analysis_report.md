# Analysis Report — multi-model-v7

Generated: 2026-06-06T15:58:27

## Summary

| Pair | Model | Avg Before | Avg After | Delta | Verdict |
|------|-------|-----------|----------|-------|---------|
| lite-gemini-3.1-flash-lite | gemini-3.1-flash-lite | 0.749 | 0.707 | -0.042 | regressed |
| flash-gemini-3.5-flash | gemini-3.5-flash | 0.753 | 0.729 | -0.024 | regressed |
| pro-gemini-3.1-pro | gemini-3.1-pro-preview | 0.738 | 0.688 | -0.050 | regressed |
| sonnet-claude-4 | claude-sonnet-4-6 | 0.750 | 0.616 | -0.134 | regressed |
| opus-claude-4 | claude-opus-4-6 | 0.744 | 0.642 | -0.102 | regressed |

## Per-Metric Breakdown

### Response Match (`final_response_match_v2`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.653 | 0.559 | -0.094 | YES |
| flash-gemini-3.5-flash | 0.629 | 0.563 | -0.066 | YES |
| pro-gemini-3.1-pro | 0.614 | 0.437 | -0.178 | no |
| sonnet-claude-4 | 0.663 | 0.262 | -0.401 | YES |
| opus-claude-4 | 0.652 | 0.279 | -0.373 | YES |

### Quality (`final_response_quality_v1`)

GEPA threshold: **0.7**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.823 | 0.756 | -0.067 | YES |
| flash-gemini-3.5-flash | 0.818 | 0.821 | +0.003 | no |
| pro-gemini-3.1-pro | 0.818 | 0.821 | +0.004 | no |
| sonnet-claude-4 | 0.836 | 0.766 | -0.070 | no |
| opus-claude-4 | 0.840 | 0.807 | -0.033 | YES |

### Hallucination (`hallucination_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.964 | 0.969 | +0.005 | no |
| flash-gemini-3.5-flash | 0.969 | 0.947 | -0.021 | YES |
| pro-gemini-3.1-pro | 0.971 | 0.990 | +0.019 | YES |
| sonnet-claude-4 | 0.932 | 0.890 | -0.042 | YES |
| opus-claude-4 | 0.885 | 0.885 | +0.000 | no |

### Instruction (`instruction_following_v1`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.620 | 0.525 | -0.096 | YES |
| flash-gemini-3.5-flash | 0.665 | 0.668 | +0.003 | no |
| pro-gemini-3.1-pro | 0.597 | 0.459 | -0.139 | YES |
| sonnet-claude-4 | 0.673 | 0.459 | -0.214 | YES |
| opus-claude-4 | 0.731 | 0.543 | -0.188 | YES |

### Safety (`safety_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.993 | 0.991 | -0.002 | no |
| flash-gemini-3.5-flash | 0.985 | 0.989 | +0.004 | no |
| pro-gemini-3.1-pro | 0.983 | 0.978 | -0.005 | no |
| sonnet-claude-4 | 0.974 | 0.961 | -0.014 | no |
| opus-claude-4 | 0.934 | 0.964 | +0.030 | no |

### Tool Use (`tool_use_quality_v1`)

GEPA threshold: **0.3**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.438 | 0.440 | +0.002 | no |
| flash-gemini-3.5-flash | 0.453 | 0.385 | -0.068 | YES |
| pro-gemini-3.1-pro | 0.446 | 0.441 | -0.005 | no |
| sonnet-claude-4 | 0.422 | 0.361 | -0.061 | YES |
| opus-claude-4 | 0.422 | 0.374 | -0.049 | YES |

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

Degraded metrics: Instruction, Response Match, Quality

- **Instruction**: -0.096 (-15%)
- **Response Match**: -0.094 (-14%)
- **Quality**: -0.067 (-8%)

### flash-gemini-3.5-flash

Degraded metrics: Hallucination, Tool Use, Response Match

- **Hallucination**: -0.021 (-2%)
- **Tool Use**: -0.068 (-15%)
- **Response Match**: -0.066 (-11%)

### pro-gemini-3.1-pro

Degraded metrics: Instruction, Response Match

- **Instruction**: -0.139 (-23%)
- **Response Match**: -0.178 (-29%)

### sonnet-claude-4

Degraded metrics: Hallucination, Safety, Tool Use, Instruction, Response Match, Quality

- **Hallucination**: -0.042 (-5%)
- **Safety**: -0.014 (-1%)
- **Tool Use**: -0.061 (-14%)
- **Instruction**: -0.214 (-32%)
- **Response Match**: -0.401 (-61%)
- **Quality**: -0.070 (-8%)

### opus-claude-4

Degraded metrics: Tool Use, Instruction, Response Match, Quality

- **Tool Use**: -0.049 (-12%)
- **Instruction**: -0.188 (-26%)
- **Response Match**: -0.373 (-57%)
- **Quality**: -0.033 (-4%)

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

## MCP Tool Usage Audit

### GEPA Run Log Summary

| Pair | Log Lines | Errors | Warnings | Timeouts | Tool Failures |
|------|----------|--------|----------|----------|---------------|
| lite-gemini-3.1-flash-lite | 7688 | 1138 | 4 | 56 | 31 |
| flash-gemini-3.5-flash | 7726 | 756 | 9 | 44 | 23 |
| pro-gemini-3.1-pro | 6872 | 672 | 3 | 20 | 11 |
| sonnet-claude-4 | 6940 | 678 | 4 | 24 | 13 |
| opus-claude-4 | 6784 | 655 | 2 | 12 | 8 |

**156 total MCP timeouts** across all runs — 
GEPA iterations with tool timeouts run without tool outputs, 
reducing optimization signal for tool-dependent behavior.

**86 total tool acquisition failures** — 
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
| lite-gemini-3.1-flash-lite | $0.30 | -0.042 | regressed |
| flash-gemini-3.5-flash | $0.60 | -0.024 | regressed |
| pro-gemini-3.1-pro | $10.00 | -0.050 | regressed |
| sonnet-claude-4 | $15.00 | -0.134 | regressed |
| opus-claude-4 | $75.00 | -0.102 | regressed |

## Recommendations

1. **Investigate metric name alignment** between GEPA local eval and cloud eval.
   Mismatched names mean GEPA optimizes for different metrics than cloud reports.
