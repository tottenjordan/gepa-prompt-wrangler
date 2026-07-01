# GEPA Prompt-Optimization Sweep — Round 1 Analysis

**Date:** 2026-06-25
**Scope:** 7 pipeline runs across 5 models (opus48, sonnet, pro, flash, lite), budgets 75 and 150, on the cleaned eval harness.
**Judge:** gemini-2.5-flash · **Eval set:** 64 cases (49 train / 15 val) · **Metric:** equal-weight avg of frq, instruction_following, tool_use, hallucination, safety.

---

## 0. Data-integrity correction (read first)

The figure **"opus48 0.859→0.890"** used as a reference throughout the live monitoring **is unsubstantiated and is retracted.** The actual artifact metrics for the only budget-150 opus48 job (`gepa-run-d9523ff46d-20260624-044057`) are **0.7466 → 0.7908**, and that run was on **dirty eval goldens with `tool_use_quality` floored at 0.42** — i.e. it predates both the eval-data cleanup and the tool_use metric fix. It is **not comparable** to the cleaned runs and must not be cited as a clean opus48@150 result.

**Consequence:** we have **no clean opus48@150 run.** The only clean opus48 data is @75 (which returned the seed). Round 2 is therefore *required* to establish whether opus48 improves at 150 on the clean harness.

Additionally, the opus48@150 run used a **different (barer) seed** — 391 chars vs the rich 579-char seed used everywhere else. So its dramatic prompt evolution (391→1121) is confounded by starting point, not attributable to budget alone.

---

## Comparison rule (important)

**Model-vs-model comparisons are made WITHIN a single fixed-budget cohort only.** The purpose of equalizing budget (all 5 at 75; Round 2 = all 5 at 150) is precisely so cross-model comparisons are fair. We do **not** compare a 75-budget result against a 150-budget result to rank models — budget would be a confound. Budget is a *separate, secondary* axis (same model + same seed, varying only budget) used solely to observe whether GEPA escapes the seed; it is never used to compare different models.

## 1. The complete data table

| Run | Budget | Data | Seed chars | Prompt after | avg before | avg after | Δ | tool_use | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **opus48** | 150 | ⚠️dirty | 391 | 1121 (evolved) | 0.7466 | 0.7908 | +0.044 | 0.42→0.48 **floored** | ❌ non-comparable |
| **opus48** | 75 | clean | 579 | 579 (**seed**) | 0.8666 | 0.8754 | +0.009 | 0.97→0.97 | ⚠️ noise |
| **sonnet** | 150 | clean | 579 | 2816 (evolved) | 0.8476 | 0.8651 | +0.018 | 0.95→0.95 | ⚙️ evolved, Δ within noise |
| **sonnet** | 75 | clean | 579 | 579 (**seed**) | 0.8779 | 0.8510 | −0.027 | 0.96→0.95 | ⚠️ noise |
| **pro** | 75 | clean | 579 | 579 (**seed**) | 0.8463 | 0.8832 | +0.037 | 0.99→0.99 | ⚠️ noise |
| **flash** | 75 | clean | 579 | 1943 (**evolved +236%**) | 0.8859 | 0.8880 | +0.002 | 0.95→0.98 | ⚙️ real trade |
| **lite** | 75 | clean | 579 | 579 (**seed**) | 0.8633 | 0.8767 | +0.013 | 0.97→0.96 | ⚠️ noise |

`tool_use` stayed off the ~0.42 floor on every clean run — the custom LLMMetric fix is solid across all 5 models.

---

## 2. Three findings

### Finding 1 — The eval-noise floor is ~±0.03, and it swallows almost every "gain"
The cleanest measurement of pure noise: the **same seed prompt, same model, same clean eval set**, scored in two independent runs. (This is a *noise measurement* on the unchanged seed — not a cross-budget comparison of optimized results, which we never make.)
- **sonnet seed eval-before:** 0.8476 vs 0.8779 → **0.030 spread**, identical seed/model/eval.
- This run-to-run scatter is why proper noise quantification needs **within-cohort replicates** (Round 2 suggestion A), not incidental pairs.

So a single eval pass carries **±0.01–0.03 of scatter** from agent sampling + non-deterministic LLM-judge scoring. Every aggregate delta in the table except flash's structural change is **inside or barely outside this band.** We cannot rank models or call a +0.018 a "win" without replicates.

### Finding 2 — The reliable GEPA signal is efficiency, not score
Where GEPA *did* change the prompt, the robust, repeatable effect was **token reduction at equal quality**, not a score climb:
- **flash@75:** output tokens **5,334 → 2,460 (−54%)**, avg flat (+0.002).
- **sonnet@150:** prompt rewritten, output tokens dropped (the "−61% tokens" observed live), avg +0.018 (within noise).

GEPA reliably discovers **cheaper prompts that hold quality**. It does **not** reliably raise the aggregate score on this already-strong seed.

### Finding 3 — The aggregate score is distorted by two metric artifacts
- **`instruction_following` (predefined, reference-free)** depresses terse agents: pro's low before-score (0.846) is driven almost entirely by IF=0.54, not real weakness. This inflates apparent model differences.
- **Equal weighting hides trades:** flash's −0.092 frq drop was cancelled by gains elsewhere, so a real behavioral shift reads as "no change" in the headline number.

---

## 3. Interpretation — why the rich seed ceilings GEPA

The 579-char rich seed (tool-first rules, expense-policy ordering, city→airport mapping, concision) is **already near-optimal** for these models on this eval set. GEPA's job is to beat the starting prompt on the train set; when the start is already at the ceiling, the search returns it unchanged. Evidence:
- At budget 75, **4 of 5 models returned the seed byte-for-byte** (opus48, pro, lite, + sonnet). Only flash found a lateral (cheaper) variant.
- The one run that evolved most dramatically (opus48 391→1121) **started from a barer seed with headroom** — confounded, but consistent with "headroom drives evolution."
- For **sonnet**, the only clean controlled budget comparison (same 579 seed, clean data): **150 evolved the prompt, 75 did not.** Budget gates whether GEPA escapes the seed — but the resulting score gain was still within noise.

**Bottom line:** Round 1 largely measured *"the seed is good"* rather than *"GEPA lifts quality."* To measure GEPA's actual lift we must give it headroom (a barer seed) and the budget to use it, and we must quantify noise with replicates.

---

## 4. Recommendations

**Production guidance (current seed):**
1. For strong models on an already-good prompt, **budget ≥150** is needed for GEPA to even alter the prompt; at 75 you re-confirm the seed.
2. Value GEPA here for **cost reduction** (−50%+ output tokens at equal quality), not score lift.
3. Treat any avg-score change **within ±0.03 as noise** unless backed by replicates.
4. Stop citing single-pass deltas as wins; report the **prompt-changed? + token-cost + per-metric** triplet.

**Methodology fixes:**
5. Add **replicates (N≥3)** for eval-before/after to establish the noise band statistically.
6. Report a **frq-weighted or trade-aware composite**, and footnote the `instruction_following` artifact, so headline scores stop misleading.

---

## 5. Round 2 design

### 5a. Core ask — all 5 models at budget 150 (clean harness, rich 579 seed)
Produces a **self-contained 150-cohort** in which the 5 models are compared **against each other** — the same within-budget comparison we ran at 75, not a cross-comparison to the 75 cohort. Gives us a clean opus48@150 (we currently have only the dirty one) and tests whether pro/flash/lite evolve at 150 or stay at the seed. The 75-cohort and 150-cohort are two independent experiments; conclusions are drawn within each, never across.

Manifest changes (prepared): `max_metric_calls: 75 → 150`, cache_bust bumped on all five.

### 5b. Suggested additional configs (decision required)
| # | Config | Why | Cost |
|---|---|---|---|
| A | **Replicates: N=3 of each 150 run** | Only way to separate real gains from the ±0.03 noise floor. Highest methodological value. | 3× run count |
| B | **Barer seed variant** (~100–150 char minimal prompt) at 150 | Gives GEPA real headroom; measures its *actual* lift instead of "seed already good." The most informative single addition. | +1 cohort |
| C | **Intermediate budget = 100 or 125** (sonnet at least) | Locates the budget knee between 75 (seed) and 150 (evolve). | +1–2 runs |
| D | **Stronger judge (gemini-2.5-pro) cross-check** on a subset | Lower-variance scoring; validates the noisy frq metric. | judge cost only |

**Recommended round-2 scope:** Core (5×150) **+ B (barer seed)** as the highest-signal addition, **+ A (replicates)** if budget allows, since without replicates the 150 deltas will again be uninterpretable. C and D are nice-to-haves.

**Cost is not a constraint:** each run is ~$0.17–0.38; a full 5×150 cohort is ~$1.50.
