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
| `hallucinations_v1` | 0.8 | High bar — factual accuracy is critical (plural, ADK 2.x) |
| `final_response_quality_v1` | 0.7 | Moderate-high — overall quality matters |
| `final_response_match_v2` | 0.5 | Moderate — partial matches are valuable |
| `tool_use_quality_v1` | 0.3 | Lower — tool use is harder, reward incremental progress |

> **Note:** `instruction_following_v1` was previously included but is NOT in the ADK metric evaluator registry (any version). Using it causes `NotFoundError` during GEPA optimization. It was removed in the ADK 2.x upgrade.

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

## 6. `instruction_following_v1` — Added Then Removed

**Commit (added):** [`7c76a03`](https://github.com/tottenjordan/gepa-prompt-wrangler/commit/7c76a0314585e333b371d4f0819fe18bf9391179)

This metric was added to the GEPA optimization criteria with a 0.5 threshold. However, `instruction_following_v1` is NOT in the ADK metric evaluator registry (any version, including 2.x). GEPA threw `NotFoundError` for every eval case, scoring it as 0.00 and polluting the optimization signal.

**Fix (removed):** Removed `instruction_following_v1` from GEPA criteria. Instruction adherence is approximated by the `rubric_based_final_response_quality_v1` metric's custom rubrics (`instruction_adherence` and `completeness`). The batch eval's `instruction_following_v1` (server-side) still measures it in eval_before/eval_after.

**Impact:** Cleaner optimization signal — GEPA no longer wastes evaluation budget on a metric that always returns 0.00.

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

## Interpreting Results: Why "Optimized" Doesn't Always Mean Higher Scores

Even with a fully working pipeline, an optimized prompt may produce aggregate scores similar to (or occasionally lower than) the baseline. This does not necessarily mean optimization failed — it means the continuous average is an incomplete lens. Here's why:

### GEPA Optimizes Binary Pass/Fail, Not Continuous Scores

GEPA uses thresholds to convert each metric's continuous score into a binary pass/fail signal per eval case. It then tries to maximize the number of cases that pass *all* thresholds simultaneously. This means a prompt that flips 5 failing cases to passing — but regresses 5 other cases from 0.95 to 0.75 (still above threshold) — is a net win for GEPA, even though the continuous average may stay flat or dip slightly.

**Takeaway:** The aggregate average can mask real improvements. Look at the per-case pass/fail counts and per-metric breakdowns in the report, not just the headline number.

### Per-Metric Tradeoffs Can Net Out

The "average score" reported across all 6 metrics can hide per-metric movement. An optimized prompt might significantly improve `instruction_following` and `tool_use_quality` while slightly regressing `response_match` — netting to the same overall number. The per-metric delta table in the report stage reveals these tradeoffs.

### Model Capability Ceiling

Lower-tier models (e.g., flash-lite at $0.30/M output tokens) have inherent reasoning limitations. If the model can't reason through multi-step tool chains, no prompt can compensate. Prompt optimization has the most room to help on models that are capable but under-directed — expect larger lifts on mid-to-upper-tier models (pro, sonnet, opus) than on the cheapest tier.

### The Generic Prompt Is Surprisingly Competitive

The baseline seed prompt — `"You are a helpful assistant. Use the available tools to answer user questions."` — already conveys the core task. For simple tool-use scenarios, the model's pretraining may already handle the task well, leaving less headroom for prompt-driven improvement. The optimized prompt's value shows up more on complex, multi-intent, or ambiguous cases where explicit instructions reduce guesswork.

### Variance Between Runs

Eval scores vary between runs due to model sampling (temperature, tool-call ordering, API latency). A 3-run average with standard deviation (`scores_std`) helps distinguish real improvements from noise. If the baseline's standard deviation is ±0.03 and the post-optimization delta is +0.01, the improvement is within noise. The report stage flags this.

### What to Look At Instead of the Aggregate Average

1. **Per-metric deltas** — Which metrics improved, which regressed?
2. **Per-case pass/fail counts** — How many cases crossed a threshold boundary?
3. **Standard deviation** — Is the delta statistically meaningful vs. run-to-run variance?
4. **Qualitative prompt inspection** — Does the optimized prompt contain domain-specific instructions (tool names, response structure, edge-case handling) that the generic prompt lacks?
5. **Cost-quality tradeoff** — A cheaper model with an optimized prompt matching a more expensive model's baseline is still a win.
