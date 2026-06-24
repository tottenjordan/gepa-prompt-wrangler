# Analysis Report — multi-model-v5

Generated: 2026-06-05T20:50:29

## Summary

| Pair | Model | Avg Before | Avg After | Delta | Verdict |
|------|-------|-----------|----------|-------|---------|
| lite-gemini-3.1-flash-lite | gemini-3.1-flash-lite | 0.749 | 0.725 | -0.023 | regressed |
| flash-gemini-3.5-flash | gemini-3.5-flash | 0.753 | 0.735 | -0.019 | regressed |
| pro-gemini-3.1-pro | gemini-3.1-pro-preview | 0.738 | 0.736 | -0.002 | unchanged |
| sonnet-claude-4 | claude-sonnet-4-6 | 0.750 | 0.727 | -0.023 | regressed |
| opus-claude-4 | claude-opus-4-6 | 0.744 | 0.723 | -0.021 | regressed |

## Per-Metric Breakdown

### Response Match (`final_response_match_v2`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.653 | 0.552 | -0.101 | YES |
| flash-gemini-3.5-flash | 0.629 | 0.570 | -0.059 | YES |
| pro-gemini-3.1-pro | 0.614 | 0.546 | -0.068 | YES |
| sonnet-claude-4 | 0.663 | 0.567 | -0.096 | YES |
| opus-claude-4 | 0.652 | 0.600 | -0.052 | no |

### Quality (`final_response_quality_v1`)

GEPA threshold: **0.7**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.823 | 0.817 | -0.006 | no |
| flash-gemini-3.5-flash | 0.818 | 0.810 | -0.008 | no |
| pro-gemini-3.1-pro | 0.818 | 0.906 | +0.089 | YES |
| sonnet-claude-4 | 0.836 | 0.869 | +0.033 | no |
| opus-claude-4 | 0.840 | 0.882 | +0.042 | YES |

### Hallucination (`hallucination_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.964 | 0.972 | +0.008 | no |
| flash-gemini-3.5-flash | 0.969 | 0.962 | -0.007 | no |
| pro-gemini-3.1-pro | 0.971 | 0.978 | +0.007 | no |
| sonnet-claude-4 | 0.932 | 0.904 | -0.028 | YES |
| opus-claude-4 | 0.885 | 0.856 | -0.029 | YES |

### Instruction (`instruction_following_v1`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.620 | 0.583 | -0.037 | no |
| flash-gemini-3.5-flash | 0.665 | 0.650 | -0.014 | no |
| pro-gemini-3.1-pro | 0.597 | 0.591 | -0.006 | no |
| sonnet-claude-4 | 0.673 | 0.638 | -0.035 | no |
| opus-claude-4 | 0.731 | 0.657 | -0.075 | YES |

### Safety (`safety_v1`)

GEPA threshold: **0.8**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.993 | 0.993 | +0.000 | no |
| flash-gemini-3.5-flash | 0.985 | 0.987 | +0.002 | no |
| pro-gemini-3.1-pro | 0.983 | 0.974 | -0.009 | no |
| sonnet-claude-4 | 0.974 | 0.976 | +0.002 | no |
| opus-claude-4 | 0.934 | 0.927 | -0.007 | no |

### Tool Use (`tool_use_quality_v1`)

GEPA threshold: **0.3**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| lite-gemini-3.1-flash-lite | 0.438 | 0.435 | -0.003 | no |
| flash-gemini-3.5-flash | 0.453 | 0.429 | -0.024 | no |
| pro-gemini-3.1-pro | 0.446 | 0.421 | -0.025 | no |
| sonnet-claude-4 | 0.422 | 0.409 | -0.013 | no |
| opus-claude-4 | 0.422 | 0.416 | -0.006 | no |

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

Degraded metrics: Instruction, Quality, Response Match

- **Instruction**: -0.037 (-6%)
- **Quality**: -0.006 (-1%)
- **Response Match**: -0.101 (-15%)

### flash-gemini-3.5-flash

Degraded metrics: Hallucination, Instruction, Tool Use, Quality, Response Match

- **Hallucination**: -0.007 (-1%)
- **Instruction**: -0.014 (-2%)
- **Tool Use**: -0.024 (-5%)
- **Quality**: -0.008 (-1%)
- **Response Match**: -0.059 (-9%)

### pro-gemini-3.1-pro

Degraded metrics: Safety, Instruction, Tool Use, Response Match

- **Safety**: -0.009 (-1%)
- **Instruction**: -0.006 (-1%)
- **Tool Use**: -0.025 (-5%)
- **Response Match**: -0.068 (-11%)

### sonnet-claude-4

Degraded metrics: Hallucination, Instruction, Tool Use, Response Match

- **Hallucination**: -0.028 (-3%)
- **Instruction**: -0.035 (-5%)
- **Tool Use**: -0.013 (-3%)
- **Response Match**: -0.096 (-14%)

### opus-claude-4

Degraded metrics: Safety, Hallucination, Instruction, Tool Use, Response Match

- **Safety**: -0.007 (-1%)
- **Hallucination**: -0.029 (-3%)
- **Instruction**: -0.075 (-10%)
- **Tool Use**: -0.006 (-2%)
- **Response Match**: -0.052 (-8%)

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
| lite-gemini-3.1-flash-lite | 702 | 59 | 4 | 24 | 13 |
| flash-gemini-3.5-flash | 894 | 76 | 7 | 28 | 15 |
| pro-gemini-3.1-pro | 543 | 17 | 2 | 12 | 6 |
| sonnet-claude-4 | 603 | 27 | 3 | 16 | 9 |
| opus-claude-4 | 506 | 10 | 1 | 4 | 4 |

**84 total MCP timeouts** across all runs — 
GEPA iterations with tool timeouts run without tool outputs, 
reducing optimization signal for tool-dependent behavior.

**47 total tool acquisition failures** — 
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
| lite-gemini-3.1-flash-lite | $0.30 | -0.023 | regressed |
| flash-gemini-3.5-flash | $0.60 | -0.019 | regressed |
| pro-gemini-3.1-pro | $10.00 | -0.002 | regressed |
| sonnet-claude-4 | $15.00 | -0.023 | regressed |
| opus-claude-4 | $75.00 | -0.021 | regressed |

## Recommendations

1. **Investigate metric name alignment** between GEPA local eval and cloud eval.
   Mismatched names mean GEPA optimizes for different metrics than cloud reports.
