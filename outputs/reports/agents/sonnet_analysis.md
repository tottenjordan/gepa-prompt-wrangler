# Sonnet — GEPA Optimization Analysis (wrangler_v3)

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-sonnet-4-6`
- **Provider:** Anthropic (via LiteLLM + Vertex AI)
- **Input cost:** $3.00/M tokens
- **Output cost:** $15.00/M tokens
- **Engine ID:** `1374840884243202048`

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

2909 characters — concise, directive style with strict guidelines for tool usage, no proactive booking, and comparative analysis support.

## Eval Results

### Before Optimization (Baseline)

| Metric | Score |
|--------|-------|
| Response Quality | 0.89 |
| Hallucination | 0.91 |
| Safety | 0.88 |
| Tool Use | 0.41 |
| Instruction Following | 0.81 |
| Response Match | 0.83 |

### After Optimization (wrangler_v3)

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
| Input cost | $3.00/M tokens |
| Output cost | $15.00/M tokens |
| Combined cost (in+out) | $18.00/M tokens |
| Avg quality (before) | 0.79 |
| Avg quality (after) | 0.73 |
| Quality gain | -0.06 (-7.0%) |
| Quality per $/M tokens | 0.041 |

GEPA optimization resulted in a net regression (-7.0%) for Sonnet. Safety and hallucination improved, but instruction following and response match declined sharply, suggesting the GEPA-optimized prompt's conciseness directives conflict with what the batch evaluator rewards.

## Key Observations

- Average score decreased from **0.79** to **0.73** (-7.0%)
- **Improved:** Safety (+10%), Hallucination (+1%)
- **Regressed:** Response Match (-25%), Instruction Following (-19%), Quality (-8%)
- **Unchanged:** Tool Use (0.41)
- Largest response match regression of all models — GEPA's conciseness optimization may produce responses too terse for the response match evaluator
