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
