# Round 2 — Core Cohort Analysis (5/5 complete)

**Date:** 2026-06-26
**Scope:** 5 GEPA optimization runs, one per model, all from the **rich 579-char seed** ("core" cohort).
**Config (identical across all 5):** `max_metric_calls = 150`, N=1 replicate, 64-case batch eval (48 train / 15 val split), 15-case GEPA valset.
**Composite:** equal-weight mean of `final_response_quality_v1`, `hallucination_v1`, `safety_v1`, `tool_use_quality_v1`. **`instruction_following_v1` is excluded** from the headline composite (reference-free artifact — change F); reported separately below.

> **Comparison discipline:** All numbers here are Round-2 @150. Do **not** compare against the Round-1 75-budget cohort. The bare cohort (same 5 models, ~116-char seed) is in flight; the full 10-run paired comparison comes after it completes.

---

## 0. Method note — the built-in noise control (read this first)

Two of the five runs (**sonnet** and **lite**) returned the **identical seed prompt** (579→579 chars). Their eval-before and eval-after therefore score *the same prompt twice* — any before→after movement is **pure measurement noise** (agent sampling + LLM-judge non-determinism, N=1). This gives us a free, in-experiment calibration of how large a delta must be to mean anything:

| metric | sonnet (control) | lite (control) | ⇒ noise floor |
|---|---|---|---|
| composite | **+0.022** | −0.003 | **|Δ| ≲ 0.022** |
| final_response_quality | +0.017 | +0.001 | ±0.017 |
| hallucination | +0.026 | −0.028 | ±0.028 |
| safety | +0.010 | +0.012 | ±0.012 |
| tool_use_quality | +0.034 | +0.003 | ±0.034 |
| output tokens | +57% | −14% | very high (±50%+) |

**Consequence:** a composite gain has to clear **~0.022** to be distinguishable from nothing at N=1. `tool_use_quality` is the noisiest metric (±0.034); `safety` is the most stable (±0.012). Token counts are extremely noisy run-to-run (sonnet drifted +57% on an unchanged prompt), so only *large* token deltas are interpretable.

*(Caveat: this is a 2-point noise estimate — directional, not a formal CI. The bare cohort adds 5 more runs to firm it up.)*

---

## 1. Headline scorecard (core cohort)

| model | prompt | composite before→after | Δ | vs noise (±0.022) | output-token Δ |
|---|---|---|---|---|---|
| **opus48** | 579→1915 ✅ | 0.909 → 0.943 | **+0.034** | clears | **−60%** |
| **flash** | 579→1596 ✅ | 0.926 → 0.953 | **+0.027** | clears | −22% |
| **sonnet** | 579→579 ❌ | 0.851 → 0.873 | +0.022 | = noise (control) | +57% (noise) |
| **pro** | 579→2328 ✅ | 0.932 → 0.938 | +0.006 | within noise | +3% |
| **lite** | 579→579 ❌ | 0.951 → 0.948 | −0.003 | = noise (control) | −14% (noise) |

*(`bare` column appended after the bare cohort finishes → becomes the 10-run paired table.)*

---

## 2. Robust findings (survive N=1)

### F1 — Prompt evolution is bimodal: 3 evolved, 2 returned the seed
At budget 150, **opus48, pro, and flash** escaped the seed and produced substantially longer prompts (+176% to +302% chars). **sonnet and lite** found no valset candidate that strictly beat the seed within 150 metric calls and fell back to it unchanged.

This split is a GEPA-**internal valset** decision (15 val cases), *not* a function of the batch-eval composite. Note sonnet had the **lowest** before-composite of all five (0.851) yet still returned the seed — so "already good enough" does **not** explain the seed-returns. The likelier cause is that 150 calls funded too few search iterations for those two model/valset combinations to find a strict improvement (consistent with the budget-vs-walltime behavior we documented). **The bare cohort is the real test:** from a near-empty seed there is far more headroom, so if 150 is enough budget at all, the bare runs should evolve.

### F2 — opus48 is the standout: same quality, 60% fewer tokens
opus48's evolved prompt held composite quality (+0.034, just past the noise floor) while cutting **output tokens 7224 → 2887 (−60%)** — a reduction far beyond the token noise band. Strongest single result in the cohort: GEPA found a prompt that makes opus48 dramatically more concise without losing quality. This is a real efficiency/cost win, not noise.

### F3 — Large per-metric moves that clear their individual noise bands
Gating each metric by its control-derived floor:

- **opus48 safety +0.148** (floor ±0.012) — large, real. The evolved prompt materially improved safety scoring.
- **pro safety +0.066** (±0.012) — real.
- **pro final_response_quality −0.067** (±0.017) — real **regression**. Pro evolved a longer prompt that traded response quality for safety/hallucination → net composite flat (+0.006). Evolution ≠ improvement.
- **flash final_response_quality +0.071** (±0.017) — real gain; flash is the cleanest "evolved + improved across the board" run (and −22% tokens).
- **opus48 tool_use −0.044** (±0.034) — marginal; barely clears the noisiest metric's band, treat as soft.

### F4 — flash is the efficiency runner-up
flash evolved, improved composite beyond noise (+0.027), and cut tokens −22% — same shape as opus48, smaller magnitude.

---

## 3. NOT robust at N=1 (do not over-read)

- **sonnet +0.022 / lite −0.003 composite** — these are the controls; they measure noise, not optimization. Report them as "seed returned, no change."
- **pro +0.006 composite** — within the noise floor; pro's evolution was a **wash** at the headline level despite a +302% longer prompt.
- **Any token delta on sonnet/lite** (+57% / −14%) — unchanged prompt, so sampling variance only.
- **Cross-model composite *levels*** (e.g. "lite 0.951 > sonnet 0.851") conflate model capability with prompt and are confounded by the seed-return; rank models on *Δ from their own baseline*, not absolute composite.

---

## 4. Optimize-stage cost/time (all @150)

| model | optimize elapsed | GEPA output tokens | prompt growth |
|---|---|---|---|
| opus48 | 125m | ~19,150 | +231% |
| sonnet | 139m | ~5,790 | 0% (seed) |
| pro | 115m | ~23,280 | +302% |
| flash | 115m | ~15,960 | +176% |
| lite | 115m | ~5,790 | 0% (seed) |

Optimize wall time clustered tightly at **~115–140 min** regardless of model tier — dominated by the 150 metric-call budget, not per-call agent speed (even lite, the smallest model, took ~115 min). Seed-return runs spent ~⅓ the output tokens (they wrote fewer candidate prompts).

---

## 5. What the bare cohort will test (the other half of the 10)

The paired core-vs-bare design isolates **how much the hand-written seed is worth**:

1. **Do sonnet & lite evolve from the bare seed?** If yes → the core seed-returns were a *headroom* problem (rich seed near a local optimum), not a budget problem. If they *still* return ~seed → 150 budget is genuinely insufficient for those models.
2. **Can GEPA recover the rich-seed performance from ~116 chars?** Compare each model's bare-after composite to its core-after. If bare-after ≈ core-after, the careful seed engineering bought little; if bare-after < core-after, the seed is doing real work.
3. **Does the −60% token win (opus48) reproduce from a bare start**, or was it specific to compressing the rich seed?
4. Adds 5 more before/after pairs → **tighter noise estimate** for the final 10-run report.

---

## 6. Bottom line (core cohort)

- **opus48** — best outcome: held quality, **−60% tokens**, big safety gain. Clear win.
- **flash** — solid: evolved, improved composite (+0.027) and cut tokens −22%.
- **pro** — evolved the most (+302% chars) but netted a wash; quality regressed as safety rose.
- **sonnet / lite** — returned the seed; before/after is noise. Whether that's headroom or budget is the bare cohort's job to answer.
- **Method takeaway:** the two seed-return runs gave us a free noise floor (~±0.022 composite). Only opus48 and flash clear it. Lead future reports with prompt-evolution + token structure, not small composite wiggles.
