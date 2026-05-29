# Pro — GEPA Optimization Analysis (wrangler_v3)

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-pro-preview`
- **Provider:** Google
- **Input cost:** $4.00/M tokens
- **Output cost:** $18.00/M tokens
- **Engine ID:** `6112627692236963840`

## Eval Dataset

- 40 cases from eval_cases.yaml (28 train / 12 val split)
- GEPA local eval score: 0.750 (val set)

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

## Optimized Prompt (wrangler_v3)

3332 characters — focused on hotel search and expense policy checks with detailed tool usage guidelines and known policy limits.

## Eval Results

### Before Optimization (Baseline)

| Metric | Score |
|--------|-------|
| Response Quality | 0.92 |
| Hallucination | 1.00 |
| Safety | 0.92 |
| Tool Use | 0.42 |
| Instruction Following | 0.73 |
| Response Match | 0.80 |

### After Optimization (wrangler_v3)

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
| Input cost | $4.00/M tokens |
| Output cost | $18.00/M tokens |
| Combined cost (in+out) | $22.00/M tokens |
| Avg quality (before) | 0.80 |
| Avg quality (after) | 0.76 |
| Quality gain | -0.04 (-4.6%) |
| Quality per $/M tokens | 0.034 |

GEPA optimization resulted in a net regression (-4.6%) for Pro. While safety and tool use improved, instruction following and response match declined significantly, suggesting the optimized prompt may be too restrictive for Pro's capabilities.

## Key Observations

- Average score decreased from **0.80** to **0.76** (-4.6%)
- **Improved:** Safety (+8%), Tool Use (+9%)
- **Regressed:** Instruction Following (-15%), Response Match (-14%), Quality (-7%), Hallucination (-6%)
- Pro may perform better with a lighter-touch prompt that doesn't constrain its reasoning
