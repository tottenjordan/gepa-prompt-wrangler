# Lite — GEPA Optimization Analysis

## Architecture

![Agent Architecture](../diagrams/agent_architecture.png)

## Agent Configuration

- **Model:** `gemini-3.1-flash-lite`
- **Provider:** Google
- **Input cost:** $0.25/M tokens
- **Output cost:** $1.5/M tokens
- **Engine ID:** `4981388556929859584`

## Eval Dataset

- See eval_cases.yaml for case details

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

```

## Eval Results

### Before Optimization

| Metric | Score |
|--------|-------|
| Response Quality | 0.94 |
| Hallucination | 0.99 |
| Safety | 0.92 |
| Tool Use | 0.36 |
| Instruction Following | 0.37 |
| Response Match | 0.43 |

### After Optimization

| Metric | Before | After | Delta | Change |
|--------|--------|-------|-------|--------|
| Response Quality | 0.94 | 0.92 | -0.02 | -2% |
| Hallucination | 0.99 | 0.85 | -0.14 | -14% |
| Safety | 0.92 | 1.00 | +0.08 | +9% |
| Tool Use | 0.36 | 0.44 | +0.08 | +21% |
| Instruction Following | 0.37 | 0.77 | +0.40 | +107% |
| Response Match | 0.43 | 0.85 | +0.42 | +98% |

## Cost-Benefit Analysis

| Metric | Value |
|--------|-------|
| Input cost | $0.25/M tokens |
| Output cost | $1.5/M tokens |
| Combined cost (in+out) | $1.75/M tokens |
| Avg quality (before) | 0.67 |
| Avg quality (after) | 0.80 |
| Quality gain | +0.14 (+20.4%) |
| Quality per $/M tokens | 0.460 |

GEPA optimization improved average quality by **+20.4%** at a cost of **$1.75/M tokens** (combined input+output). The quality gain comes at zero additional inference cost — only the system prompt changed.

## Key Observations

- Average score changed from **0.67** to **0.80** (+20.4%)
- **Improved:** Safety, Tool Use, Instruction Following, Response Match
- **Regressed:** Response Quality, Hallucination
