# Opus — GEPA Optimization Analysis (wrangler_v3)

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `claude-opus-4-6`
- **Provider:** Anthropic (via LiteLLM + Vertex AI)
- **Input cost:** $5.00/M tokens
- **Output cost:** $25.00/M tokens
- **Engine ID:** `4549878621539401728`

## Eval Dataset

- 40 cases from eval_cases.yaml (28 train / 12 val split)
- GEPA local eval score: 0.917 (val set)

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

3193 characters — task-specific instructions covering expense policy, submission, history review, travel search/booking with conciseness and factual accuracy directives.

## Eval Results

### Before Optimization (Baseline)

| Metric | Score |
|--------|-------|
| Response Quality | 0.89 |
| Hallucination | 0.91 |
| Safety | 0.70 |
| Tool Use | 0.42 |
| Instruction Following | 0.79 |
| Response Match | 0.91 |

### After Optimization (wrangler_v3)

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.89 | 0.80 | -0.09 | -10% |
| Hallucination | 0.91 | 0.94 | +0.03 | +3% |
| Safety | 0.70 | 1.00 | +0.30 | +43% |
| Tool Use | 0.42 | 0.47 | +0.05 | +12% |
| Instruction Following | 0.79 | 0.67 | -0.12 | -15% |
| Response Match | 0.91 | 0.77 | -0.14 | -15% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $5.00/M tokens |
| Output cost | $25.00/M tokens |
| Combined cost (in+out) | $30.00/M tokens |
| Avg quality (before) | 0.77 |
| Avg quality (after) | 0.78 |
| Quality gain | +0.01 (+0.6%) |
| Quality per $/M tokens | 0.026 |

GEPA optimization produced a marginal improvement (+0.6%) for Opus. The standout result is the **+43% safety improvement** (0.70 to 1.00), the largest single-metric gain across all models. Tool use and hallucination also improved, offsetting regressions in response match and instruction following.

## Key Observations

- Average score improved slightly from **0.77** to **0.78** (+0.6%)
- **Improved:** Safety (+43%), Tool Use (+12%), Hallucination (+3%)
- **Regressed:** Response Match (-15%), Instruction Following (-15%), Quality (-10%)
- Highest GEPA local eval score (0.917) but batch eval gains are modest — suggests gap between GEPA's local eval and GEAP's batch eval metrics
- Most expensive model ($30/M) with lowest quality-per-dollar ratio
