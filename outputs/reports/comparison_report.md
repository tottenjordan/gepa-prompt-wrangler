# GEPA Prompt Wrangler — Cross-Model Comparison Report

## Pipeline Overview

![GEPA Optimization Pipeline](diagrams/demo_pipeline.png)

## Prompt Evolution Summary

All agents started with the same generic 78-character prompt:

```
You are a helpful assistant. Use the available tools to answer user questions.
```

GEPA expanded this into specialized, model-tailored instructions:

| Agent | Model | Before | After | Expansion |
|-------|-------|--------|-------|-----------|
| Lite | `gemini-3.1-flash-lite` | 78 chars | 3954 chars | 51x |
| Flash | `gemini-3.5-flash` | 78 chars | 5004 chars | 64x |
| Pro | `gemini-3.1-pro-preview` | 78 chars | 3332 chars | 43x |
| Sonnet | `claude-sonnet-4-6` | 78 chars | 2909 chars | 37x |
| Opus | `claude-opus-4-6` | 78 chars | 3193 chars | 41x |

![Before/After Overview](diagrams/before_after_overview.png)

## Eval Dataset

- **Total cases:** 40
- **Low complexity:** 21 cases
- **Medium complexity:** 13 cases
- **High complexity:** 6 cases
- **Categories:** booking, boundary, cancellation, error_handling, expense, planning, policy, search

## Baseline vs Optimized Scores

### Baseline (Generic Prompt)

| Metric |Lite | Flash | Pro | Sonnet | Opus |
|--------|------ | ------ | ------ | ------ | ------ |
| Response Quality | 0.86 | 0.92 | 0.92 | 0.89 | 0.89 |
| Hallucination | 1.00 | 1.00 | 1.00 | 0.91 | 0.91 |
| Safety | 1.00 | 0.98 | 0.92 | 0.88 | 0.70 |
| Tool Use | 0.39 | 0.41 | 0.42 | 0.41 | 0.42 |
| Instruction Following | 0.76 | 0.80 | 0.73 | 0.81 | 0.79 |
| Response Match | 0.81 | 0.78 | 0.80 | 0.83 | 0.91 |

### After Optimization (GEPA)

| Metric |Lite | Flash | Pro | Sonnet | Opus |
|--------|------ | ------ | ------ | ------ | ------ |
| Response Quality | 0.80 | 0.84 | 0.85 | 0.82 | 0.80 |
| Hallucination | 0.96 | 0.95 | 0.94 | 0.92 | 0.94 |
| Safety | 1.00 | 0.96 | 0.99 | 0.97 | 1.00 |
| Tool Use | 0.45 | 0.47 | 0.46 | 0.41 | 0.47 |
| Instruction Following | 0.75 | 0.78 | 0.62 | 0.66 | 0.67 |
| Response Match | 0.85 | 0.82 | 0.69 | 0.62 | 0.77 |

### Improvement Delta (After - Before)

| Metric |Lite | Flash | Pro | Sonnet | Opus |
|--------|------ | ------ | ------ | ------ | ------ |
| Response Quality | -0.06 | -0.08 | -0.07 | -0.07 | -0.09 |
| Hallucination | -0.04 | -0.05 | -0.06 | +0.01 | +0.03 |
| Safety | +0.00 | -0.02 | +0.07 | +0.09 | +0.30 |
| Tool Use | +0.06 | +0.06 | +0.04 | -0.00 | +0.05 |
| Instruction Following | -0.01 | -0.02 | -0.11 | -0.15 | -0.12 |
| Response Match | +0.04 | +0.04 | -0.11 | -0.21 | -0.14 |
| **Average** | **-0.00** | **-0.01** | **-0.04** | **-0.06** | **+0.01** |

## Cost-Benefit Analysis

### Per-Model Cost and Quality

| Agent | Model | Input $/M | Output $/M | Combined $/M | Avg Quality (Before) | Avg Quality (After) | Quality Gain | Quality/$ |
|-------|-------|-----------|------------|-------------|---------------------|--------------------|--------------|-----------:|
| Lite | `gemini-3.1-flash-lite` | $0.25 | $1.50 | $1.75 | 0.80 | 0.80 | -0.00 | 0.457 |
| Flash | `gemini-3.5-flash` | $1.50 | $9.00 | $10.50 | 0.81 | 0.80 | -0.01 | 0.077 |
| Pro | `gemini-3.1-pro-preview` | $4.00 | $18.00 | $22.00 | 0.80 | 0.76 | -0.04 | 0.035 |
| Sonnet | `claude-sonnet-4-6` | $3.00 | $15.00 | $18.00 | 0.79 | 0.73 | -0.06 | 0.041 |
| Opus | `claude-opus-4-6` | $5.00 | $25.00 | $30.00 | 0.77 | 0.78 | +0.01 | 0.026 |

### Cost-Quality Tradeoff

![Cost-Quality Tradeoff](charts/cost_quality.png)

*Quality/$ = average post-optimization quality score divided by combined token cost ($/M tokens). Higher is better — indicates more quality per dollar spent.*

**Ranked by Quality/$:**

1. **Lite** — 0.457 quality/$ (avg 0.80 at $1.75/M)
2. **Flash** — 0.077 quality/$ (avg 0.80 at $10.50/M)
3. **Sonnet** — 0.041 quality/$ (avg 0.73 at $18.00/M)
4. **Pro** — 0.035 quality/$ (avg 0.76 at $22.00/M)
5. **Opus** — 0.026 quality/$ (avg 0.78 at $30.00/M)

## Evaluation Charts

### Baseline Comparison

![Baseline Comparison](charts/comparison.png)

### Optimization Impact

![Improvement Delta](charts/improvement_delta.png)

## Comparison with Previous Run

Comparing against previous results from `outputs/results_all_agents.json`.

### Average Score Comparison (After)

| Agent | Previous | Current | Delta |
|-------|----------|---------|-------|
| Lite | 0.80 | 0.80 | -0.00 |
| Flash | 0.74 | 0.80 | +0.06 |
| Pro | 0.82 | 0.76 | -0.06 |
| Sonnet | 0.81 | 0.73 | -0.08 |
| Opus | 0.71 | 0.78 | +0.07 |

## Interpretation

Results were mixed across agents: **Opus** saw net improvement; **Lite** held steady; **Flash, Pro, Sonnet** saw net decline.

### Metric-Level Tradeoffs

GEPA optimization revealed a clear tradeoff pattern:

**Metrics that improved:**

- **Safety** (+0.089 avg) — largest gain in Opus (0.70 → 1.00)
- **Tool Use** (+0.041 avg) — largest gain in Flash (0.41 → 0.47)

**Metrics that declined:**

- **Instruction Following** (-0.081 avg) — largest drop in Sonnet (0.81 → 0.66)
- **Response Match** (-0.074 avg) — largest drop in Sonnet (0.83 → 0.62)
- **Response Quality** (-0.073 avg) — largest drop in Opus (0.89 → 0.80)
- **Hallucination** (-0.022 avg) — largest drop in Pro (1.00 → 0.94)

This tradeoff is expected: GEPA optimizes toward the eval criteria in `sampler_config.json` (response match, safety, tool use). Metrics not included as optimization targets — like instruction following and response quality — may shift as the prompt is reshaped to maximize target metrics.

### Per-Agent Insights

- **Lite** (`gemini-3.1-flash-lite`, 3,954 char prompt): net -0.003. Gained in Tool Use, Response Match. Lost in Response Quality, Hallucination.

- **Flash** (`gemini-3.5-flash`, 5,004 char prompt): net -0.011. Gained in Tool Use, Response Match. Lost in Response Quality, Hallucination.

- **Pro** (`gemini-3.1-pro-preview`, 3,332 char prompt): net -0.038. Gained in Safety, Tool Use. Lost in Response Quality, Hallucination, Instruction Following, Response Match.

- **Sonnet** (`claude-sonnet-4-6`, 2,909 char prompt): net -0.055. Gained in Safety. Lost in Response Quality, Instruction Following, Response Match.

- **Opus** (`claude-opus-4-6`, 3,193 char prompt): net +0.006. Gained in Safety, Tool Use. Lost in Response Quality, Instruction Following, Response Match.


### Cost-Quality Assessment

**Best value: Lite** delivers the most quality per dollar. **Best absolute quality: Flash** at 0.80 average.

The most expensive model (Opus at $30.00/M) costs **17x more** than the cheapest (Lite at $1.75/M) but scores 0.78 vs 0.80 — a marginal quality difference.

### Recommendations

1. **For cost-sensitive workloads:** Use **Lite** (`gemini-3.1-flash-lite`) — best quality-per-dollar ratio.

2. **For quality-critical workloads:** Use **Flash** (`gemini-3.5-flash`) — highest absolute quality.

3. **Re-optimize with expanded criteria.** The decline in Instruction Following (-0.081 avg) suggests adding it as an explicit optimization target in `sampler_config.json`.

4. **Prompt cost is zero.** Optimization only changes the system prompt — no additional inference cost. Even mixed results are worth iterating on.

5. **Monitor with online evaluators** after deployment to catch regressions on real traffic beyond the eval dataset.

## Per-Agent Reports

- [Lite Agent Analysis](agents/lite_analysis.md)
- [Flash Agent Analysis](agents/flash_analysis.md)
- [Pro Agent Analysis](agents/pro_analysis.md)
- [Sonnet Agent Analysis](agents/sonnet_analysis.md)
- [Opus Agent Analysis](agents/opus_analysis.md)
