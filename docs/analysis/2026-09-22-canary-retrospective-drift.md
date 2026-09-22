# Retrospective autorater drift, 2026-09-17 → 2026-09-22

**Question:** campaign 09's control arm drifted **+0.0732** on `safety_v1` and the write-up's
leading explanation was a service-side autorater change. Is the autorater actually drifting?

**Answer: no — not on `safety_v1`, over five days, on two independent captures.** The drift
hypothesis does not survive, and the +0.0732 has a better explanation that the existing data
already supported.

## Method

Two DOE 03 captures (`doe03-c1`, `doe03-c5`, 64 cases each) were frozen as canaries and
re-scored on 2026-09-22. Each was scored **five times on 2026-09-17**, so the comparison has a
same-day spread to judge against rather than a single point.

The responses are byte-identical between the two dates — the canary replays a stored inference
pass and makes **no agent calls**. So anything that moves is the judge.

## Result

`sd` is over the five 2026-09-17 passes. **Coverage is cases-scored, and where it moved the
metric is not strictly comparable** — a mean over a different case set mixes dropout with the
judge (silent-failures #5).

### `doe03-c1`

| metric | 2026-09-17 | sd | 2026-09-22 | Δ | in sd | coverage |
| --- | --- | --- | --- | --- | --- | --- |
| `safety_v1` | 1.0000 | 0.0000 | **1.0000** | **±0.0000** | exact | 62 → 64 |
| `hallucination_v1` | 0.9699 | 0.0036 | 0.9639 | −0.0060 | 1.7 | 64 → 64 |
| `tool_use_quality_v1` | 0.9736 | 0.0040 | 0.9904 | +0.0168 | 4.2 | 64 → 64 |
| `final_response_quality_v1` | 0.9151 | 0.0150 | 0.9013 | −0.0138 | 0.9 | 62 → 63 |
| `instruction_following_v1` | 0.8048 | 0.0108 | 0.7511 | −0.0538 | 5.0 | 61 → 64 |

### `doe03-c5`

| metric | 2026-09-17 | sd | 2026-09-22 | Δ | in sd | coverage |
| --- | --- | --- | --- | --- | --- | --- |
| `safety_v1` | 0.9870 | 0.0081 | 0.9819 | −0.0051 | 0.6 | 48 → 63 |
| `tool_use_quality_v1` | 0.9666 | 0.0062 | 0.9698 | +0.0033 | 0.5 | 64 → 64 |
| `hallucination_v1` | 0.9356 | 0.0069 | 0.9330 | −0.0026 | 0.4 | 63 → 64 |
| `final_response_quality_v1` | 0.9167 | 0.0147 | 0.9238 | +0.0070 | 0.5 | 60 → 62 |
| `instruction_following_v1` | 0.7954 | 0.0258 | 0.7718 | −0.0236 | 0.9 | 60 → 64 |

## What this says

**1. `safety_v1`'s judge did not move.** On c1 it returned **exactly 1.0000** on both dates —
sd 0.0000 across five passes then, and 1.0000 again five days later over *more* cases. A
saturated metric is robust to the coverage change: adding two cases that also score 1.0 cannot
conceal drift. On c5 it moved −0.0051, 0.6 sd, well inside the same-day spread.

**2. So campaign 09's +0.0732 was not the autorater.** Over five days the safety judge moved
≤0.005; campaign 09's control moved **0.0732 in ~16 hours**. That is fifteen times larger over
a thirtieth of the interval. The service-side-shift hypothesis does not fit.

**3. The explanation the data already supported: a control arm re-runs inference.** It holds
the *prompt* fixed, not the *responses*. The agent samples fresh output on each eval side, so a
control delta contains **agent variability plus judge variability**. A canary holds the
responses fixed, so it isolates the judge — and the judge is flat.

Three independent measurements already agreed and were not read together:

| evidence | says |
| --- | --- |
| DOE 02: `safety_v1` per-case judge disagreement **0/64** | the judge is deterministic on safety |
| DOE 03: `score_repeats` exponent **0.02**, `num_runs` **0.58** | safety's floor responds to re-running inference, not re-scoring |
| this canary: ≤0.005 over 5 days | the judge does not drift on safety either |

A judge-side floor would fall with `score_repeats`. Safety's does not move at all with it. The
noise is agent-side, and always was.

**4. Coverage is less stable than any of this assumed.** `doe03-c5`'s `safety_v1` scored
**48/64** on 2026-09-17 and 63/64 on 2026-09-22. DOE 03's floors were computed over case sets
that varied by up to 16 cases between passes — which is a caveat on the floors themselves, not
just on this comparison.

## What this does not say

- **Two captures, one re-score each.** This is a screen, not a characterisation. A drift of
  <0.005 is established for safety; nothing here bounds drift on a metric whose coverage moved.
- **`tool_use_quality_v1` on c1 (+0.0168, 4.2 sd) is not interpretable.** The judge prompt was
  re-baselined on **2026-09-17**, the same day the captures were scored, and CLAUDE.md records
  that per-case tool-use comparisons must not cross that boundary. c5 does not reproduce it
  (+0.0033, 0.5 sd), which is consistent with a boundary artefact rather than drift.
- **`instruction_following_v1` is the worst case for this method**, and both captures had
  coverage move. c1's −0.0538 is over 61 → 64 cases and cannot be separated from the three
  cases that appeared.
- **It does not rule out drift on other days.** It rules out *sustained* drift large enough to
  produce +0.0732 in 16 h, on this metric.

## What to do

1. **Stop attributing control-arm drift to the autorater by default.** For `safety_v1` the
   floor is agent-side. `num_runs` is the lever; `score_repeats` is not, exactly as DOE 03
   measured.
2. **Keep the canary running anyway.** Its value here was ruling a hypothesis *out* in fifteen
   minutes, which is worth as much as confirming one — and it is the only instrument that can
   separate the two.
3. **Report canary coverage next to every reading.** Found while running this: the first
   version of `score_canary` recorded scores and not coverage, which would have let a dropout
   artefact be read as drift. Fixed, and guarded — `canary_drift` now excludes any metric whose
   coverage moved from `max_abs_drift`.
4. **Re-read DOE 03's floors with the coverage caveat.** A floor computed over 48/64 cases on
   one pass and 64/64 on another is not the quantity it was taken to be.

## Reproduce

```bash
uv run wrangler canary freeze outputs/captures/doe03-c1_20260917_164141.pkl --label doe03-c1
uv run wrangler canary score data/canaries/doe03-c1.json --out outputs/canary/readings-c1.jsonl
```

Readings: `outputs/canary/readings-c1.jsonl`, `outputs/canary/readings-c5.jsonl`.
Baselines: `outputs/doe03/manifest.json` → the five `*_scored_*.json` files per capture.
