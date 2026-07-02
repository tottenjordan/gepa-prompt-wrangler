# Round 2 — Complete Paired Analysis (10 of 10 runs)

**Date:** 2026-07-02
**Scope:** All 10 runs of the Round 2 sweep — 5 models (opus48, sonnet, pro, flash, lite) × 2 cohorts (**core** rich 579-char seed, **bare** ~119-char minimal seed). This supersedes `round2_7run_partial.md`: the 3 previously-blocked bare runs (**pro, flash, lite**) have been backfilled with clean 5/5 evals.
**Config (identical across all 10):** `max_metric_calls = 150`, N=1 replicate, 64-case batch eval (48 train / 15 val), 15-case GEPA valset.
**Composite:** equal-weight mean of `final_response_quality_v1`, `hallucination_v1`, `safety_v1`, `tool_use_quality_v1`. `instruction_following_v1` excluded from the headline (reference-free artifact — change F); reported separately.

> **Comparison discipline:** All numbers are Round-2 @150. Never compare to the Round-1 75-budget cohort. Rank models on **Δ from their own baseline**, not absolute composite levels (levels conflate model capability with prompt).

---

## 0. The backfill — why the 3 bare runs are now valid

The pro/flash/lite-bare evals originally returned only 2 of 5 metrics and were flagged "DEGENERATE." That was **not** a Vertex Eval Service incident (the earlier escalation is retracted — see `docs/vertex_eval_service_incident.md`). Root cause: `build_source_package()` copied the agent's `.env` — which contained a live `GOOGLE_API_KEY` — into the GEAP build package; at runtime `load_dotenv()` re-read it, overrode Vertex ADC, and every inference failed with `401 UNAUTHENTICATED → Failed to create session`. The error-payload responses broke the FRQ/hallucination/tool_use autoraters (score=None → dropped), leaving 2/5.

**Fixed** in `wrangler/core/deploy.py` (commit `5d34aa0`, strips `GOOGLE_API_KEY`/`GEMINI_API_KEY` from the copied `.env`). The backfill (`outputs/eval_recovery/backfill/run_backfill.py`) redeployed each bare agent with the fixed code and re-ran eval-before (seed) + eval-after (GEPA-optimized prompt). **All three now return the full 5/5 metrics on both phases** — confirming the fix live. Runtimes: lite 29m, flash 39m, pro 36m.

---

## 1. Full scorecard (all 10 runs)

| model | cohort | prompt chars | composite before→after | Δ | vs noise (±0.029) | IF (excluded) |
|---|---|---|---|---|---|---|
| **opus48** | core | 579→1915 ✅ | 0.909 → 0.943 | **+0.034** | clears | — |
| **opus48** | bare | 119→6630 ✅ | 0.901 → 0.900 | −0.001 | within noise | 0.752→0.755 |
| **sonnet** | core | 579→579 ❌ | 0.851 → 0.873 | +0.022 | = noise (control) | — |
| **sonnet** | bare | 119→5768 ✅ | 0.853 → **0.910** | **+0.056** | **clears (biggest)** | 0.753→0.782 |
| **pro** | core | 579→2328 ✅ | 0.932 → 0.938 | +0.006 | within noise | — |
| **pro** | bare | 119→4172 ✅ | 0.929 → 0.924 | −0.004 | within noise | 0.62→0.64 |
| **flash** | core | 579→1596 ✅ | 0.926 → 0.953 | +0.027 | ~noise (borderline) | — |
| **flash** | bare | 119→5428 ✅ | 0.932 → 0.943 | +0.011 | within noise | 0.64→0.56 |
| **lite** | core | 579→579 ❌ | 0.951 → 0.948 | −0.003 | = noise (control) | — |
| **lite** | bare | 119→119 ❌ | 0.937 → 0.966 | +0.029 | = noise (control) | 0.61→0.65 |

Backfilled per-metric detail (before → after):

| model-bare | frq | hallucination | safety | tool_use | IF |
|---|---|---|---|---|---|
| lite | 0.83→0.93 | 0.99→0.99 | 0.97→0.97 | 0.96→0.98 | 0.61→0.65 |
| flash | 0.83→0.82 | 0.98→0.99 | 0.95→0.97 | 0.96→1.00 | 0.64→0.56 |
| pro | 0.87→0.88 | 0.99→0.97 | 0.90→0.88 | 0.96→0.96 | 0.62→0.64 |

*(lite-bare's before and after are the **same 119-char prompt** — GEPA returned the seed byte-for-byte — so its row measures noise, not optimization; see §2.)*

---

## 2. Noise floor — now anchored on THREE controls

Three runs returned their seed unchanged (before/after = same prompt evaluated twice), giving direct N=1 measurement-noise reads:

| control | prompt | composite Δ |
|---|---|---|
| sonnet-core | 579→579 | +0.022 |
| lite-core | 579→579 | −0.003 |
| **lite-bare** (new) | 119→119 | **+0.029** |

**Updated noise floor: |Δ| ≲ 0.029** (was ≲0.022 on the 2-point estimate). The lite-bare control widens it — its frq swung +0.10 on an *identical* prompt, confirming single-metric N=1 noise is larger than the earlier ±0.017 frq estimate. A composite Δ must now clear **~0.029** to be credible at N=1.

**Runs that clear 0.029 (robustly real):**
- **sonnet-bare +0.056** — clean, largest.
- **opus48-core +0.034** — clears.

**Now borderline / flat (reclassified by the wider floor):**
- **flash-core +0.027** — was "clears" at the old 0.022 floor; now sits *at* noise. Its −22% token reduction remains a secondary directional signal, but the composite gain is no longer robust.
- opus48-bare (−0.001), pro-core (+0.006), pro-bare (−0.004), flash-bare (+0.011) — all within noise → treat as flat.

---

## 3. Per-model core-vs-bare seed verdict (all 5 models)

The paired design isolates what the hand-written 579-char seed is worth per model. With all 10 runs clean:

| model | core Δ | bare Δ | core-after vs bare-after | seed verdict |
|---|---|---|---|---|
| **opus48** | +0.034 (evolved, −60% tok) | −0.001 (sprawled 6630c) | 0.943 > 0.900 (**+0.043**) | **seed helps** — worth ~0.043; token win is seed-compression-specific |
| **sonnet** | +0.022 (seed return) | **+0.056** (evolved) | 0.873 < 0.910 (**−0.037**) | **seed hurts** — rich seed was a handicap |
| **pro** | +0.006 (flat) | −0.004 (flat) | 0.938 > 0.924 (+0.014) | **neither** — pro is optimization-resistant at 150 from either seed |
| **flash** | +0.027 (evolved, −22% tok) | +0.011 (flat, 5428c) | 0.953 > 0.943 (+0.010) | **seed helps mildly** — core evolved; bare sprawled to a flat result |
| **lite** | −0.003 (seed return) | +0.029 (seed return) | 0.948 ≈ 0.966 (noise) | **search difficulty** — returned seed from *both* seeds |

**What the 3 backfilled runs add:**

- **F8 — pro does not evolve from bare either.** pro-bare (+0.004 flat, drop) mirrors pro-core (+0.006 flat). Unlike sonnet, pro's core seed-return-adjacent flatness was **not** headroom that a bare start could unlock — pro is genuinely optimization-resistant within 150 calls regardless of seed. GEPA did evolve the prompt (119→4172) but gained nothing.
- **F9 — flash's core win does not transfer to bare.** flash-core (+0.027, −22% tok) was the tidy secondary win; flash-bare evolved to 5428 chars but landed flat (+0.011, within noise). So flash's gain was **seed-dependent** — it needed the rich seed as scaffolding; from bare it sprawled without improving. This makes flash pattern like opus48 (seed helps), not like sonnet (seed hurts).
- **F10 — lite has a genuine search problem, not a seed-proximity one.** lite returned the seed **byte-identical in both cohorts** (579→579 core, 119→119 bare). It is not that the rich seed sat near a local optimum (the sonnet story) — lite can't beat *either* seed in 150 calls. This resolves the open F7 lite question: lite-specific search difficulty, confirmed now that its bare eval is clean.

---

## 4. The central finding, sharpened

**Seed value is model-specific and its sign flips across models.** With the full matrix:

- **Seed HELPS:** opus48 (+0.043 core-after advantage), flash (its only real win is core-only).
- **Seed HURTS:** sonnet (−0.037 — bare beats core; the rich seed capped it).
- **Seed IRRELEVANT:** pro (flat both ways), lite (returns seed both ways).

Only 1 of 5 models (sonnet) benefits from starting bare; 2 of 5 (opus48, flash) are actively helped by the hand-written seed; 2 of 5 (pro, lite) are insensitive to it at 150 calls. **There is no universal "invest in the seed" rule** — but the modal outcome is that a good seed either helps or is harmless, and only sonnet was held back by it.

**Budget-vs-headroom (F7 closed):** sonnet-bare proves 150 calls suffice when there's room to climb. pro/lite prove that when a model *doesn't* climb, it's the model's search behavior — not budget starvation — since they also fail to move from a bare start with maximal headroom.

---

## 5. Bottom line (10 runs)

- **sonnet-bare is the headline optimization win** (+0.056, cleanly across-the-board) and beats its own core result by +0.037. The rich seed actively held sonnet back.
- **opus48-core is the headline efficiency win** (−60% tokens at held quality, +0.034 composite); the token win is specific to *compressing* the rich seed and does not reproduce from bare.
- **Only sonnet-bare and opus48-core clear the (now wider) 0.029 noise floor.** flash-core is borderline; everything else is flat at N=1.
- **The seed's value is model-specific and can be negative** — the single most important cross-cohort result, now supported by all 5 clean pairs.
- **pro and lite are optimization-resistant at 150** from either seed (pro flat both ways; lite returns seed both ways — a search-difficulty signature, not a seed-proximity one).
- **All 10 evals are now clean 5/5** — the matrix is complete and the "2/5 drop" is closed as a deploy bug, not a service incident.

---

## Appendix — data provenance

| run | job_id / source | notes |
|---|---|---|
| opus48/sonnet/pro/flash/lite-core | see `round2_core_cohort.md` | captured pre-cutover, 5/5 |
| opus48-bare | gepa-run-1201cfdf72-20260626-090542 | pre-cutover, 5/5 |
| sonnet-bare | gepa-run-9dd9ba01ee-20260626-120114 | pre-cutover, 5/5 |
| pro-bare | backfill 2026-07-02 (fixed deploy) | eval engines redeployed+deleted; 5/5 |
| flash-bare | backfill 2026-07-02 (fixed deploy) | 5/5 |
| lite-bare | backfill 2026-07-02 (fixed deploy) | 5/5; opt prompt = seed (119→119) |

Backfill scores: `outputs/eval_recovery/backfill/{pair}_result.json`. Composite = mean of the 4 quality metrics excluding IF. Fix: commit `5d34aa0`. Supersedes `round2_7run_partial.md`.
