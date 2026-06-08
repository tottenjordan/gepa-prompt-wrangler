# Improving GEPA Performance: Pipeline Fixes That Unmasked True Model Capabilities

This document captures the series of code changes that dramatically improved the reliability and accuracy of our GEPA prompt optimization and evaluation pipeline. Many of these fixes addressed silent bugs that were previously misattributed as poor model or eval performance.

## 1. Per-Case Score Extraction Fix

**Commit:** [`d410af3`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/d410af31c7ad834a4f801763ce3e16f4e9383ae2)

`_extract_per_case_scores()` was using the wrong SDK path to pull individual case scores from the eval API response, resulting in empty `per_case` arrays. This meant:

- The optimizer had no per-case signal to work with
- We couldn't tell *which* cases were failing — it looked like the model was just bad overall
- Multi-run averaging had no per-case data to aggregate

**Fix:** Use the correct SDK path: `evaluation_item_results.eval_case_results[i].response_candidate_results[0].metric_results`

**Impact:** This was the biggest silent bug. What appeared to be "the model doesn't score well" was really us not reading the scores at all.

## 2. Threshold Injection for GEPA Binary Scoring

**Commits:**
- [`cfa836c`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/cfa836cd86f07407edea9a7a0e91bb4f0c0fc4cb) — Add required `threshold` field to rubric-based sampler criteria
- [`44bf7e7`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/44bf7e77b53a21b8cea51da00b70f70fc5712ac2) — Auto-inject threshold for ADK schema without changing continuous scoring
- [`98cba5a`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/98cba5a3a412813d7d580959940925f082e34a4e) — Add tiered thresholds for GEPA binary pass/fail scoring

The ADK's `LocalEvalSamplerConfig` schema *requires* a `threshold` field on rubric-based criteria, but the sampler config we were generating omitted it. Without thresholds, GEPA treated everything as either trivially passing or couldn't evaluate it — the optimizer had no meaningful pass/fail signal to work with.

**Fix:** Three incremental commits that (1) added the required field, (2) auto-injected it without disrupting continuous scoring, and (3) introduced tiered thresholds per metric type:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| `safety_v1` | 0.8 | High bar — safety failures are unacceptable |
| `hallucination_v1` | 0.8 | High bar — factual accuracy is critical |
| `final_response_quality_v1` | 0.7 | Moderate-high — overall quality matters |
| `final_response_match_v2` | 0.5 | Moderate — partial matches are valuable |
| `instruction_following_v1` | 0.5 | Moderate — some flexibility acceptable |
| `tool_use_quality_v1` | 0.3 | Lower — tool use is harder, reward incremental progress |

**Impact:** Tighter thresholds give the optimizer more signal about what to improve, leading to faster convergence and better optimized prompts.

## 3. Threshold Merge and Metric Name Mapping

**Commit:** [`e953d52`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/e953d5252b259f2df490ec3818ad4704e6ccfe5a)

The sampler config uses `rubric_based_` prefixed names (e.g., `rubric_based_tool_use_quality_v1`) but the experiment config uses plain names (`tool_use_quality_v1`). Without `METRIC_NAME_MAP` and `_merge_thresholds()`, the experiment's threshold overrides silently failed to match any criteria keys in the sampler config — falling back to whatever defaults existed (or none).

**Fix:** Added `_merge_thresholds()` with a `METRIC_NAME_MAP` that handles the `rubric_based_` prefix translation, plus logging so mismatches are visible.

**Impact:** Experiment-level threshold calibration now actually reaches the optimizer, enabling per-experiment tuning.

## 4. Rate-Limit Resilience with Batched Inference

**Commit:** [`d410af3`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/d410af31c7ad834a4f801763ce3e16f4e9383ae2)

Sending all 64 eval cases at once to Gemini models hit 429 rate limit errors. The eval would get partial results or crash entirely, and it looked like the model was producing null/bad responses when really the API was rejecting requests.

**Fix:** Introduced per-model rate-limit-aware batching:

| Model Tier | Batch Size | Inter-Batch Delay | Workers |
|------------|------------|-------------------|---------|
| Gemini 3.x | 4 cases | 15s | 4 |
| Claude models | 64 cases | 0s | 64 |

Additional resilience features:
- `_retry_failed_cases()` automatically re-runs any cases that returned null responses
- Configurable concurrency per model tier
- Progress logging per batch

**Impact:** Eliminated 429 errors and null-response noise. Eval results are now complete and reliable across all model tiers.

## 5. Generic Seed Prompt for Optimization

**Commit:** [`1874641`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/1874641f1684bdd81bda63df7e70cbd3cbb32334)

The optimizer was starting from a *previously optimized* prompt instead of the generic seed prompt. This limited GEPA's search space — it was trying to incrementally improve an already-specialized prompt rather than exploring the full prompt design space.

**Fix:** Always start optimization from the generic seed: `"You are a helpful assistant. Use the available tools to answer user questions."`

**Impact:** Clean-slate optimization produces better results because GEPA can explore the full prompt space without being anchored to a prior local optimum.

## 6. `instruction_following_v1` as Explicit Metric

**Commit:** [`7c76a03`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/7c76a0314585e333b371d4f0819fe18bf9391179)

This metric was entirely missing from the GEPA optimization criteria. The optimizer wasn't being told to care about whether the agent follows its system prompt instructions — which is the core objective of prompt optimization.

**Fix:** Added `instruction_following_v1` with a 0.5 threshold to the criteria passed to GEPA.

**Impact:** The optimizer now explicitly targets instruction adherence, producing prompts that are more directive and effective.

## 7. Multi-Run Eval Averaging

**Commit:** [`8e65054`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/8e65054ab561ed29015656178c5a37ec88e4d58a)

Before this, eval ran once and that single score was the baseline/result. Variance between runs was high enough that a "bad" baseline could make optimization look more effective than it was (or vice versa).

**Fix:** Added `--num-runs N` flag that runs N independent evals and averages results. Also reports `scores_std` (standard deviation) so we can see variance and assess statistical significance.

**Impact:** Eval results are now statistically meaningful. A 3-run average with reported standard deviation distinguishes real improvements from noise.

## Summary

Before these fixes, we were:

1. **Not reading per-case scores** — empty arrays meant no granular signal
2. **Not sending thresholds** — GEPA had no pass/fail criteria to optimize against
3. **Hitting rate limits silently** — partial results looked like poor model performance
4. **Starting from stale prompts** — limiting the optimizer's search space
5. **Missing a key metric** — instruction following wasn't being measured
6. **Using single-run evals** — high variance masked true performance

All of these manifested as "the model performs poorly" or "optimization doesn't help much" when the real problem was pipeline bugs masking actual capabilities. With these fixes in place, the v8 experiment pipeline produces reliable, reproducible results with clear before/after signal.
