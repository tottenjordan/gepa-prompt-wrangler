# Analysis Report — pipeline-smoke-test

Generated: 2026-08-21T02:19:48+00:00

## Summary

| Pair | Model | Avg Before | Avg After | Delta | Verdict |
|------|-------|-----------|----------|-------|---------|
| sonnet | claude-sonnet-4-6 | 0.882 | 0.901 | +0.019 | improved |

## Per-Metric Breakdown

### Quality (`final_response_quality_v1`)

GEPA threshold: **0.85**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| sonnet | 0.933 | 0.967 | +0.033 | no |

### Hallucination (`hallucination_v1`)

GEPA threshold: **0.95**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| sonnet | 0.906 | 0.982 | +0.076 | no |

### Instruction (`instruction_following_v1`)

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| sonnet | 0.893 | 0.744 | -0.149 | no |

### Safety (`safety_v1`)

GEPA threshold: **0.95**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| sonnet | 0.744 | 0.850 | +0.106 | no |

### Tool Use (`tool_use_quality_v1`)

GEPA threshold: **0.5**

| Pair | Before | After | Delta | Significant? |
|------|--------|-------|-------|-------------|
| sonnet | 0.933 | 0.961 | +0.028 | no |

## Prompt Changes

### sonnet

- Original: 78 chars
- Optimized: 78 chars
- Delta: +0 chars (+0%)
- Diff: (prompts are identical)

## Degradation Diagnosis

### sonnet

Degraded metrics: Instruction

- **Instruction**: -0.149 (-17%)

## Threshold Alignment Check

Checks whether GEPA thresholds are calibrated against baseline scores.

| Metric | Threshold | Min Baseline | Gap | Status |
|--------|-----------|-------------|-----|--------|
| final_response_quality_v1 | 0.85 | 0.933 | +0.083 | OK |
| hallucination_v1 | 0.95 | 0.906 | -0.044 | FAILING |
| safety_v1 | 0.95 | 0.744 | -0.206 | FAILING |
| tool_use_quality_v1 | 0.5 | 0.933 | +0.433 | OK |

## Per-Case Analysis

## MCP Tool Usage Audit

### GEPA Run Log Summary

| Pair | Log Lines | Errors | Warnings | Timeouts | Tool Failures |
|------|----------|--------|----------|----------|---------------|
| sonnet | 3592 | 354 | 2 | 14 | 89 |

**14 total MCP timeouts** across all runs — 
GEPA iterations with tool timeouts run without tool outputs, 
reducing optimization signal for tool-dependent behavior.

**89 total tool acquisition failures** — 
these iterations could not call any MCP tools.

### Tool Keyword Preservation

Checks whether optimized prompts retained tool-related terminology.

| Pair | Original Keywords | Optimized Keywords | Added | Dropped |
|------|------------------|-------------------|-------|---------|
| sonnet | tool | tool | — | — |

## Cost Efficiency

| Pair | Model | Blended $/M | Avg Delta | Cost per +0.01 |
|------|-------|------------|----------|----------------|
| sonnet | claude-sonnet-4-6 | $5.40 | +0.019 | $2.89 |

## Recommendations

1. **Add thresholds for**: instruction_following_v1
   Metrics without thresholds default to 0.0 in GEPA — no optimization pressure.
