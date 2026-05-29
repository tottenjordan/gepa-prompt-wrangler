# Flash — GEPA Optimization Analysis (wrangler_v3)

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.5-flash`
- **Provider:** Google
- **Input cost:** $1.50/M tokens
- **Output cost:** $9.00/M tokens
- **Engine ID:** `4703001008869998592`

## Eval Dataset

- 40 cases from eval_cases.yaml (28 train / 12 val split)
- GEPA local eval score: 0.667 (val set, seeded with lite v3 prompt)

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

5004 characters — seeded from lite v3 prompt, expanded with hotel/flight booking, expense summaries, and list processing guidelines.

## Eval Results

### Before Optimization (Baseline)

| Metric | Score |
|--------|-------|
| Response Quality | 0.92 |
| Hallucination | 1.00 |
| Safety | 0.98 |
| Tool Use | 0.41 |
| Instruction Following | 0.80 |
| Response Match | 0.78 |

### After Optimization (wrangler_v3)

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.92 | 0.84 | -0.08 | -8% |
| Hallucination | 1.00 | 0.95 | -0.05 | -5% |
| Safety | 0.98 | 0.96 | -0.02 | -2% |
| Tool Use | 0.41 | 0.47 | +0.06 | +15% |
| Instruction Following | 0.80 | 0.78 | -0.02 | -3% |
| Response Match | 0.78 | 0.82 | +0.04 | +6% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $1.50/M tokens |
| Output cost | $9.00/M tokens |
| Combined cost (in+out) | $10.50/M tokens |
| Avg quality (before) | 0.82 |
| Avg quality (after) | 0.80 |
| Quality gain | -0.02 (-1.8%) |
| Quality per $/M tokens | 0.076 |

GEPA optimization produced a slight overall regression (-1.8%) while improving tool use and response match. Flash had the hardest time with GEPA optimization — the optimizer struggled to beat baseline in both generic-seeded and lite-seeded runs.

## Key Observations

- Average score decreased slightly from **0.82** to **0.80** (-1.8%)
- **Improved:** Tool Use (+15%), Response Match (+6%)
- **Regressed:** Response Quality (-8%), Hallucination (-5%), Safety (-2%), Instruction Following (-3%)
- GEPA hit a ceiling at 0.667 local val score regardless of seed prompt
- Flash may benefit more from few-shot examples than system prompt optimization
