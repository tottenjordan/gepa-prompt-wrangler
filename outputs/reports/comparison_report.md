# GEPA Prompt Wrangler — Cross-Model Comparison Report (wrangler_v3)

## Pipeline Overview

![GEPA Optimization Pipeline](diagrams/demo_pipeline.png)

## Prompt Evolution Summary

All agents started with the same generic 78-character prompt:

```
You are a helpful assistant. Use the available tools to answer user questions.
```

GEPA expanded this into specialized, model-tailored instructions using a 28/12 train/val split evalset:

| Agent | Model | Generic | Optimized | GEPA Val Score |
|-------|-------|---------|-----------|----------------|
| Lite | `gemini-3.1-flash-lite` | 78 chars | 3954 chars | 0.833 |
| Flash | `gemini-3.5-flash` | 78 chars | 5004 chars | 0.667 |
| Pro | `gemini-3.1-pro-preview` | 78 chars | 3332 chars | 0.750 |
| Sonnet | `claude-sonnet-4-6` | 78 chars | 2909 chars | 0.750 |
| Opus | `claude-opus-4-6` | 78 chars | 3193 chars | 0.917 |

![Before/After Overview](diagrams/before_after_overview.png)

## Eval Dataset

- 40 cases from eval_cases.yaml
- Train/val split: 28 train / 12 validation (stratified by tier and category)
- Tiers: low (15), medium (9), high (4) train; low (6), medium (4), high (2) val

## Baseline vs Optimized Scores (Batch Eval on GEAP)

### Baseline (Generic Prompt)

| Metric | Lite | Flash | Pro | Sonnet | Opus |
|--------|------|-------|-----|--------|------|
| Response Quality | 0.86 | 0.92 | 0.92 | 0.89 | 0.89 |
| Hallucination | 1.00 | 1.00 | 1.00 | 0.91 | 0.91 |
| Safety | 1.00 | 0.98 | 0.92 | 0.88 | 0.70 |
| Tool Use | 0.39 | 0.41 | 0.42 | 0.41 | 0.42 |
| Instruction Following | 0.76 | 0.80 | 0.73 | 0.81 | 0.79 |
| Response Match | 0.81 | 0.78 | 0.80 | 0.83 | 0.91 |

### After Optimization (GEPA wrangler_v3)

| Metric | Lite | Flash | Pro | Sonnet | Opus |
|--------|------|-------|-----|--------|------|
| Response Quality | 0.80 | 0.84 | 0.85 | 0.82 | 0.80 |
| Hallucination | 0.96 | 0.95 | 0.94 | 0.92 | 0.94 |
| Safety | 1.00 | 0.96 | 0.99 | 0.97 | 1.00 |
| Tool Use | 0.45 | 0.47 | 0.46 | 0.41 | 0.47 |
| Instruction Following | 0.75 | 0.78 | 0.62 | 0.66 | 0.67 |
| Response Match | 0.85 | 0.82 | 0.69 | 0.62 | 0.77 |

### Improvement Delta (After - Before)

| Metric | Lite | Flash | Pro | Sonnet | Opus |
|--------|------|-------|-----|--------|------|
| Response Quality | -0.06 | -0.08 | -0.07 | -0.07 | -0.09 |
| Hallucination | -0.04 | -0.05 | -0.06 | +0.01 | +0.03 |
| Safety | +0.00 | -0.02 | +0.07 | +0.09 | +0.30 |
| Tool Use | +0.06 | +0.06 | +0.04 | -0.00 | +0.05 |
| Instruction Following | -0.01 | -0.02 | -0.11 | -0.15 | -0.12 |
| Response Match | +0.04 | +0.04 | -0.11 | -0.21 | -0.14 |
| **Average** | **-0.00** | **-0.01** | **-0.04** | **-0.06** | **+0.01** |

## Cost-Benefit Analysis

### Per-Model Cost and Quality

| Agent | Model | Input $/M | Output $/M | Combined $/M | Avg (Before) | Avg (After) | Delta | Quality/$ |
|-------|-------|-----------|------------|-------------|-------------|------------|-------|----------:|
| Lite | `gemini-3.1-flash-lite` | $0.25 | $1.50 | $1.75 | 0.80 | 0.80 | +0.00 | 0.457 |
| Flash | `gemini-3.5-flash` | $1.50 | $9.00 | $10.50 | 0.82 | 0.80 | -0.02 | 0.076 |
| Pro | `gemini-3.1-pro-preview` | $4.00 | $18.00 | $22.00 | 0.80 | 0.76 | -0.04 | 0.035 |
| Sonnet | `claude-sonnet-4-6` | $3.00 | $15.00 | $18.00 | 0.79 | 0.73 | -0.06 | 0.041 |
| Opus | `claude-opus-4-6` | $5.00 | $25.00 | $30.00 | 0.77 | 0.78 | +0.01 | 0.026 |

### Cost-Quality Tradeoff

![Cost-Quality Tradeoff](charts/cost_quality.png)

**Ranked by Quality/$:**

1. **Lite** — 0.457 quality/$ (avg 0.80 at $1.75/M)
2. **Flash** — 0.076 quality/$ (avg 0.80 at $10.50/M)
3. **Sonnet** — 0.041 quality/$ (avg 0.73 at $18.00/M)
4. **Pro** — 0.035 quality/$ (avg 0.76 at $22.00/M)
5. **Opus** — 0.026 quality/$ (avg 0.78 at $30.00/M)

## Evaluation Charts

### Baseline Comparison

![Baseline Comparison](charts/comparison.png)

### Optimization Impact

![Improvement Delta](charts/improvement_delta.png)

## GEPA Local Eval vs GEAP Batch Eval

A notable finding: GEPA's local eval scores (used during optimization) don't directly translate to GEAP batch eval scores. This is expected — GEPA uses `final_response_match_v2` as its primary metric, while GEAP batch eval averages across 6 metrics.

| Model | GEPA Local (Val) | GEAP Batch (Avg) | Gap |
|-------|-----------------|-----------------|-----|
| Opus | 0.917 | 0.78 | 0.14 |
| Lite | 0.833 | 0.80 | 0.03 |
| Pro | 0.750 | 0.76 | -0.01 |
| Sonnet | 0.750 | 0.73 | 0.02 |
| Flash | 0.667 | 0.80 | -0.13 |

## Key Findings and Recommendations

### Findings

1. **Mixed optimization results.** Unlike wrangler_v2, wrangler_v3 optimization with train/val split produced mixed results on GEAP batch eval. Only Opus showed a net improvement (+0.6%), while others were flat or regressed.

2. **Consistent pattern across models:** Safety and tool use improved universally, while response quality, instruction following, and response match declined. This suggests GEPA's conciseness-oriented optimization conflicts with what the batch evaluator rewards for these metrics.

3. **Best value remains Lite** at $1.75/M tokens with 0.80 avg quality — unchanged from baseline, but with better tool use and response match.

4. **Opus safety dramatically improved** from 0.70 to 1.00 (+43%), the single largest metric improvement across all models and versions.

5. **GEPA local eval vs batch eval gap.** Opus scored 0.917 on GEPA's local eval but only 0.78 on batch eval. Flash scored 0.667 locally but 0.80 on batch eval. The metrics measure different things.

6. **Train/val split worked as intended.** The split exposed that wrangler_v2's gains were partially due to eval aliasing (training and evaluating on the same cases). wrangler_v3 scores are more realistic estimates of generalization.

### Recommendations

1. **For cost-sensitive workloads:** Use **Lite** — best quality-per-dollar, stable performance.

2. **For quality-critical workloads:** Use **Pro** or **Opus** — highest batch eval scores (0.76/0.78). Flash matches on quality (0.80) at lower cost ($10.50/M vs $22-30/M) but with less reasoning depth.

3. **Investigate the instruction following / response match regression.** The consistent decline suggests GEPA's optimizer is over-indexing on conciseness at the expense of completeness. Consider adding instruction_following as an explicit GEPA optimization criterion.

4. **Consider hybrid prompts.** The batch eval data suggests combining the safety/tool-use improvements from wrangler_v3 with the instruction-following style from the baseline could yield the best overall results.

## End-to-End Results Interpretation

### The Central Tension: What GEPA Optimizes vs What Batch Eval Rewards

GEPA optimizes for `final_response_match_v2` — a metric that rewards concise, precise responses that semantically match a reference answer. The batch evaluator averages six metrics, several of which reward different qualities: `instruction_following` and `response_quality` favor completeness, detail, and elaboration. This creates an inherent tension: GEPA-optimized prompts push models toward brevity and precision, which can hurt scores on metrics that reward thoroughness.

This tension explains the most consistent pattern in wrangler_v3 results: **safety and tool use improved across all models**, while **response quality and instruction following regressed across all models**. The optimizer successfully taught models to use tools more carefully and avoid unsafe outputs, but the conciseness directives it introduced penalized scores on metrics that reward detailed, complete responses.

### Model-Level Observations

**Lite ($1.75/M) — the clear winner for this workload.** Matched its baseline average quality (0.80) at the lowest cost, with improved tool use (+15%) and response match (+5%). GEPA optimization was essentially free — it improved the dimensions it could without hurting overall quality. At 6x cheaper than flash and 17x cheaper than opus, lite delivers equivalent or better quality on this corporate travel/expense task.

**Flash ($10.50/M) — resistant to GEPA optimization.** Scored 0.667 on GEPA's local eval regardless of seed prompt (generic or lite v3), yet achieved 0.80 on batch eval — the joint-highest score. Flash appears to have strong baseline instruction following that GEPA's evolutionary approach couldn't improve. The 3-case subsample strategy kept hitting "perfect" samples, giving no improvement signal. Flash may benefit more from few-shot examples or structured output formatting than from system prompt optimization.

**Pro ($22.00/M) — largest overall regression.** Dropped from 0.80 to 0.76 average (-4.2%), with steep declines in instruction following (-15%) and response match (-14%). The optimizer's conciseness directives conflicted most sharply with pro's tendency toward detailed, structured responses. The +7% safety improvement doesn't offset the quality loss at this price point.

**Sonnet ($18.00/M) — similar regression pattern to Pro.** Dropped from 0.79 to 0.73 (-6.2%), the largest regression of any model. Response match fell -25%, suggesting the optimizer's conciseness directives actively degraded Sonnet's output format. Sonnet was the only model where tool use showed no improvement (0.41 → 0.41), indicating GEPA couldn't find a prompt variant that improved tool calling for this model.

**Opus ($30.00/M) — the safety story.** The only model with a net improvement (+0.6%), driven almost entirely by a dramatic safety jump from 0.70 to 1.00 (+43%). This is the single largest metric improvement across all models and versions. Opus's baseline safety score (0.70) was an outlier — the lowest of any model — and GEPA's optimizer found prompt language that eliminated the safety failures. However, at $30/M with 0.78 average quality, opus offers poor value compared to lite (0.80 at $1.75/M).

### The Eval Aliasing Finding

The train/val split in wrangler_v3 exposed that wrangler_v2's gains were partially inflated by eval aliasing — training and evaluating on the same 40 cases. With a proper 28/12 split, the batch eval scores are more realistic estimates of generalization. This doesn't invalidate wrangler_v2 results, but it means wrangler_v3's more modest improvements are a truer measure of what GEPA optimization delivers on unseen inputs.

### GEPA Local Eval vs Batch Eval Gap

The gap between GEPA's local eval score and batch eval average reveals how different the two measurement systems are:

- **Opus** scored 0.917 locally but only 0.78 on batch eval (0.14 gap) — GEPA's metric rewarded opus's concise, precise style, but batch eval penalized the loss of detail.
- **Flash** scored 0.667 locally but 0.80 on batch eval (-0.13 gap) — flash's verbosity hurt it on GEPA's precision metric but helped on batch eval's completeness metrics.

This gap is not a bug — it reflects a genuine difference in what each evaluation system values. Teams choosing between GEPA optimization and manual prompt engineering should understand which metrics matter most for their use case.

### Practical Recommendations

1. **For cost-sensitive production workloads:** Deploy **lite** with the wrangler_v3 prompt. Best quality-per-dollar (0.457), stable performance, and improved tool use.

2. **For quality-critical workloads:** Use **opus** if safety is paramount (1.00 safety score), or **flash** for the best quality-to-cost balance at a higher tier ($10.50/M, 0.80 avg).

3. **For future GEPA optimization runs:** Add `instruction_following_v1` as an explicit optimization criterion alongside `final_response_match_v2`. The consistent regression in instruction following across all models suggests GEPA needs a multi-objective target to avoid over-indexing on conciseness.

4. **Consider hybrid prompts:** The batch eval data suggests combining the safety/tool-use improvements from wrangler_v3 with the instruction-following style from the baseline could yield the best overall results. This would require manual prompt engineering informed by the GEPA-generated sections.

## Per-Agent Reports

- [Lite Agent Analysis](agents/lite_analysis.md)
- [Flash Agent Analysis](agents/flash_analysis.md)
- [Pro Agent Analysis](agents/pro_analysis.md)
- [Sonnet Agent Analysis](agents/sonnet_analysis.md)
- [Opus Agent Analysis](agents/opus_analysis.md)
