# First optimization sweep — results and what they support

**Run:** 2026-08-22 · three Managed Pipeline jobs
`gepa-run-2cb23b568f` (sonnet) · `d7677f2073` (flash) · `38d87b5b32` (pro)
**Artifacts:** `gs://gepa-prompt-wrangler-staging-bucket-v1/pipeline-runs/run-{id}/stages/`

This is the first sweep in this repo that produced a prompt change worth measuring. It is
also the first where the measurement's limits are known well enough to say what it does
*not* support.

---

## Design

Three arms, deliberately identical except for the model: the same 78-character generic
seed, the same 800-metric-call budget, the same 64-case eval set, the same 49/15 GEPA
train/validation split, `num_runs=1`.

Holding the seed constant matters. Before this, sonnet seeded from a rich ~600-char prompt
at 150 calls while flash seeded from the generic prompt at the default 50 — any
disagreement between them would have been explainable by seed quality rather than model.

## What GEPA produced

| Arm | seed → optimized | optimize time | real tools named | phantom tools |
| --- | --- | --- | --- | --- |
| sonnet | 78 → **5370** chars, 47 lines | 11.2 h | 6 | **0** |
| pro | 78 → **2489** chars, 25 lines | 9.6 h | 4 | **0** |
| flash | 78 → **78** (unchanged) | 10.9 h | — | — |

**Two arms of three optimized.** This is the first genuine prompt change since v4 in May.
Flash searched for 10.9 hours and concluded nothing beat the seed — a legitimate result,
not a failure, and one that turns out to be the most useful thing in the run.

**Zero phantom tool names in either prompt.** Both name only tools the agent actually has.
That is end-to-end confirmation that the prompt and golden fixes (PRs #13, #15) worked:
GEPA, given a corrected substrate, writes prompts against reality. Before those fixes the
registries and eval goldens named `wrangler_search_mcp_search_flights` and friends, which
exist nowhere.

Sonnet's prompt is the more structured of the two — six domain sections (flights, hotels,
booking details, expenses) against pro's two, and six tools named against four. Neither is
over-prescriptive: one hard directive apiece.

## Scores

| metric | sonnet | flash *(prompt unchanged)* | pro |
| --- | --- | --- | --- |
| final_response_quality | 0.797 → 0.908 **+0.111** | 0.883 → 0.922 +0.039 | 0.868 → 0.902 +0.034 |
| hallucination | 0.861 → 0.956 **+0.095** | 0.962 → 0.960 −0.002 | 0.968 → 0.952 −0.016 |
| instruction_following | 0.690 → 0.789 **+0.099** | 0.845 → 0.826 −0.018 | 0.855 → 0.760 **−0.095** |
| safety | 0.943 → 0.956 +0.014 | 0.945 → 0.980 +0.035 | 0.885 → 0.967 **+0.082** |
| tool_use_quality | 0.904 → 0.977 **+0.072** | 0.976 → 1.000 +0.024 | 0.981 → 0.976 −0.005 |

### Flash is the control, and it was not planned

Flash's prompt is byte-identical before and after. Every delta it shows is therefore pure
measurement noise: **up to +0.039**. That is the floor any claim in this table has to
clear, and it is why "all three arms moved the same way on response quality and safety" is
*not* evidence — flash moved on both while doing nothing at all.

Against that floor:

- **sonnet clears it on four of five metrics** (+0.111, +0.095, +0.099, +0.072). It is the
  only arm where the change is bigger than the noise across the board.
- **pro clears it on safety only** (+0.082). Its +0.034 on response quality is *inside*
  flash's noise and should not be reported as an improvement.
- **pro regresses on instruction_following**, and this one is real.

### Pro's instruction-following regression is structural

Not an outlier dragging a mean:

| | perfect scores | median |
| --- | --- | --- |
| pro before | 30/58 (52%) | 1.000 |
| pro after | 25/62 (**40%**) | **0.800** |
| flash before → after (control) | 53% → 56% | 1.000 → 1.000 |

The whole distribution shifted down while the control held steady. Pro's optimized prompt
made the agent measurably worse at following instructions, and it bought response quality
and safety with that. Sonnet moved the same metric the *other* way (+0.099) from the same
seed, so this is a property of what pro's search converged on, not of the metric.

**This is the open question from the sweep.** A 2489-char prompt and a 5370-char prompt,
grown from identical starting conditions, disagreeing by ~0.2 on one metric is worth
understanding before either is promoted.

## Cost

**$1.14 for the entire sweep** — sonnet $0.613, pro $0.418, flash $0.114, covering
optimization and both eval sides for all three arms. The expensive resource here is
wall-clock (~32 hours across three arms), not money. Anyone hesitating over the spend
should hesitate over the calendar instead.

## What this measurement cannot support

**Before/after case counts are unbalanced and unpaired.** sonnet 30/57, flash 60/36, pro
58/62 — and `per_case` rows carry **no case identifier**, so the two sides cannot be
matched. Every delta compares two different subsets of the 64 cases.

Per-metric coverage *within* each side is clean: every metric scored every case on every
arm, so defect #7 (the autorater dropping a case from all metrics) is absent. The problem
is purely between sides, and it almost certainly explains most of flash's +0.039 floor.

Sonnet's baseline is the weakest of the three at n=30, so its large deltas rest on the
smallest sample. That is worth remembering before treating +0.111 as settled.

## Recommendations

1. **Add a case identifier to `per_case`.** This is the highest-value change available.
   Paired before/after on the same cases would collapse the noise floor and make deltas of
   this size readable. Everything else about the measurement is already good enough.
2. **Do not promote pro's prompt** until the instruction-following regression is
   understood. Sonnet's is the candidate worth considering.
3. **Keep an unchanged-prompt arm in every future sweep.** Flash gave this run its only
   calibration, by accident. Doing it deliberately costs one arm and converts every other
   number from suggestive to interpretable.
4. **Run arms sequentially.** These three ran concurrently, against the plan's own
   instruction; it corrupted nothing but cost wall-clock, and concurrency also worsens the
   cold-worker dropout that produced the unbalanced case counts in the first place.

## Related

- Defects that had to be fixed before any of this meant anything —
  [../notes/model-lifecycle.md](../notes/model-lifecycle.md)
- The inference dropout behind the unbalanced case counts —
  [../notes/silent-failures.md](../notes/silent-failures.md) #5

---

## Follow-up: why instruction_following diverged (+0.099 sonnet, −0.095 pro)

The sharpest disagreement in the sweep, investigated 2026-08-22.

### instruction_following is measured but never optimized

GEPA's criteria come from `sampler_config.json`:

| GEPA optimizes | batch eval reports |
| --- | --- |
| `safety_v1` | `safety_v1` |
| `rubric_based_final_response_quality_v1` *(rubrics: instruction_adherence, completeness)* | `final_response_quality_v1` |
| `rubric_based_tool_use_quality_v1` | `tool_use_quality_v1` |
| `hallucinations_v1` | `hallucination_v1` |
| — | **`instruction_following_v1`** |

`instruction_following_v1` appears only on the right. **GEPA never optimizes it**, so it is
free to be traded away for gains in the four criteria that are optimized. Its only
pressure is indirect, through the `instruction_adherence` rubric inside
`rubric_based_final_response_quality_v1`.

### The threshold on that rubric differed between the arms

At the time of the sweep (commit `67624a7~1`):

| arm | FRQ threshold gating `instruction_adherence` | prompt changed | instruction_following |
| --- | --- | --- | --- |
| sonnet | **0.85** | yes | **+0.099** |
| flash | 0.85 | no *(control)* | −0.018 (noise) |
| pro | **0.50** | yes | **−0.095** |

The two arms whose prompts changed sit at opposite ends of both columns, and the control
arm rules out the metric drifting on its own. The threshold is the **only** structural
difference between sonnet's and pro's runs — same seed, same budget, same eval set, same
train/validation split.

### The prompts corroborate it

Explicit behavioural constraints in the optimized prompts — the thing an
instruction-following judge scores:

| arm | prohibition sentences | words | density |
| --- | --- | --- | --- |
| sonnet | **7** | 776 | 10.3 / 1k words |
| pro | **1** | 343 | 2.9 / 1k words |

Sonnet's, verbatim: *"Absolutely avoid asking follow-up questions"*, *"Do not invent or
assume information"*, *"Avoid making inferences, adding speculative details"*, *"DO NOT use
markdown tables for single items, simple lists, or confirmations."*

Pro's, in full: *"Do not proactively offer to book the flight or ask for additional
personal information."*

Under strong instruction-adherence pressure sonnet's search grew a prompt dense with
explicit constraints. Under weak pressure pro's grew a task-completion prompt and spent
its gains elsewhere — its safety went **+0.082**, the largest single improvement in the
sweep.

### What this is and is not

**Is:** a coherent mechanism with a matching structural cause, corroborated by prompt
content, with the control arm excluding metric drift.

**Is not** established causation. Two arms is two points. It is confounded with prompt
length — sonnet's is 2.3× longer and a longer prompt has more room for constraints
whatever the pressure. And the underlying deltas rest on unpaired case subsets (sonnet
30/57, pro 58/62), so sonnet's +0.099 in particular sits on a 30-case baseline.

### It is now testable

Thresholds were unified at 0.85 for all six agents. **If this explanation is right, a
re-run should show pro's instruction_following no longer regressing.** That is a real
prediction the next sweep will confirm or kill, and it costs nothing extra to check.

### The framing worth keeping

A held-out metric is not a defect. `instruction_following_v1` is the one number in the
report that GEPA cannot game, which makes it the sweep's best evidence of what the
optimizer traded away. Pro's −0.095 is that mechanism working as intended: it bought
+0.082 safety and +0.034 response quality with instruction-following it was never asked
to protect.

The actionable part is not to add it to the criteria but to **read it as a holdout** —
and to keep the rubric thresholds identical across arms so that the pressure on it is too.

---

## Follow-up 2: the noise floor, measured deliberately (2026-08-23)

The 2026-08-22 floor (+0.039) came from flash accidentally returning its seed. Before
re-running the sweep, the control arm was run **on purpose**: two standalone evals of the
same engine and the same prompt, nothing between them, 64 cases each.

### The floor is larger than the accident suggested

| control pair | scored | floor |
| --- | --- | --- |
| first (unpaired only) | 33 / 35 | **±0.180** |
| second, unpaired | 41 / 39 | ±0.069 |
| second, **paired on 34 common cases** | 34 | **±0.059** |

Two things follow, and the second was not what I expected.

**The accidental control understated the floor.** +0.039 from flash against ±0.059–0.180
measured deliberately. An arm that happens to return its seed is a control only by luck,
and luck can be flattering.

**Pairing is not the main lever.** It reduced this pair's floor by **15%** (0.069 → 0.059),
not the large fraction assumed when case IDs were built. The unpaired-subset problem was
real and worth fixing, but the residual ±0.059 is measured on the *same cases with the
same prompt* — so it is judge and agent non-determinism, not sampling.

### The lever that does work is averaging

Variance falls with √n, so:

| num_runs | expected floor |
| --- | --- |
| 1 | 0.059 |
| 2 | 0.042 |
| **3** | **0.034** |
| 5 | 0.026 |

The experiment config defaults to `num_runs: 3`. It was lowered to 1 for the 2026-08-22
sweep to save wall-clock, which raised the floor by ~1.7×. **That default existed for a
reason and should not have been overridden.**

### What this means for the published sweep

Against a ±0.059 paired floor, most of the sweep's claims survive: sonnet's four gains
(+0.111, +0.095, +0.099, +0.072), pro's safety (+0.082), and pro's instruction-following
regression (−0.095) all clear it. **Pro's +0.034 response-quality gain does not** — it was
already flagged as inside the +0.039 floor and remains so.

The instruction-following divergence therefore still stands as a real effect, though the
±0.180 seen in the first control pair is a warning that a single run can be much noisier
than a single measurement suggests.

### Recommended settings for the next sweep

1. **`num_runs: 3`.** The single highest-value change; it roughly halves the floor.
2. **Keep the deliberate control arm** — two evals of one unchanged prompt, run first as a
   gate. It cost ~3 hours here and changed the plan.
3. Coverage is still poor: 41 and 39 of 64 scored, implying ~9-15% per-attempt success
   against the ~25% the retry budget assumes. That is defect #5 and it is server-side.

### Cost of the gate

Four control evals, ~6 hours, and it stopped a ~30-hour three-arm sweep from running at a
setting that would have produced a floor larger than several of the effects being measured.
