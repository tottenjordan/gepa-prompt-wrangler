# Lite — GEPA Optimization Analysis (wrangler_v3)

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-flash-lite`
- **Provider:** Google
- **Input cost:** $0.25/M tokens
- **Output cost:** $1.5/M tokens
- **Engine ID:** `8685308979372359680`

## Eval Dataset

- 40 cases from eval_cases.yaml (28 train / 12 val split)
- GEPA local eval score: 0.833 (val set)

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

3954 characters — specialized for hotel bookings, expense policy checks, with domain-specific knowledge (hotel IDs, policy limits) and structured tool usage guidelines.

## Eval Results

### Before Optimization (Baseline)

| Metric | Score |
|--------|-------|
| Response Quality | 0.86 |
| Hallucination | 1.00 |
| Safety | 1.00 |
| Tool Use | 0.39 |
| Instruction Following | 0.76 |
| Response Match | 0.81 |

### After Optimization (wrangler_v3)

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.86 | 0.80 | -0.06 | -7% |
| Hallucination | 1.00 | 0.96 | -0.04 | -4% |
| Safety | 1.00 | 1.00 | +0.00 | +0% |
| Tool Use | 0.39 | 0.45 | +0.06 | +15% |
| Instruction Following | 0.76 | 0.75 | -0.01 | -1% |
| Response Match | 0.81 | 0.85 | +0.04 | +5% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $0.25/M tokens |
| Output cost | $1.5/M tokens |
| Combined cost (in+out) | $1.75/M tokens |
| Avg quality (before) | 0.80 |
| Avg quality (after) | 0.80 |
| Quality gain | +0.00 (+0.0%) |
| Quality per $/M tokens | 0.457 |

GEPA optimization maintained average quality at **0.80** while improving response match and tool use at the expense of slight quality and hallucination regressions. The optimized prompt comes at zero additional inference cost.

## Key Observations

- Average score held steady at **0.80** (before and after)
- **Improved:** Tool Use (+15%), Response Match (+5%)
- **Regressed:** Response Quality (-7%), Hallucination (-4%)
- **Unchanged:** Safety (1.00), Instruction Following (~0.75)
- Best value model — highest quality-per-dollar ratio at $1.75/M tokens
