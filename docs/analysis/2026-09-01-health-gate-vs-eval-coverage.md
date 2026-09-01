# The health gate said 1.7%, the eval scored 88% — both were right

**Date:** 2026-09-01 · **Engine:** `3126647680801964032` (`gepa-c06-ctrl-claude-n1`)
**Prompted by:** campaign-06 validation arm, run 2 (`gepa-run-3aa99b8293-20260901-051435`)

## The contradiction

The deploy stage artifact recorded a **failed** health gate:

```json
"health": {"passed": false, "rate": 0.0167, "n": 60, "threshold": 0.8, "rerolls": 2,
           "rejected": ["2731456813500203008", "7185516844969623552"]}
```

Three engines drawn, all failed, the worst passed through — `gate_engine_health` is documented
"Never raises… a failing gate is information the run should carry, not a crash", so the pipeline
continued. `eval_before` against that same engine then scored **56 of 64 cases (87.5% coverage)**
with unremarkable metrics.

1 in 60 versus 88%. Both numbers are real.

## What was measured

| | Probe (`wrangler/tools/boot_probe.py:188`) | Eval (`wrangler/eval/evaluator.py:610`) |
| --- | --- | --- |
| Call path | client-side `create_session` + stream | `client.evals.run_inference(agent=…)` |
| Retries | **none** — one attempt, one verdict | **`max_retries=6`**, patched in deliberately |
| Statistic | `reached = events > 0` | case scored, after retries |

`rate` is a **single-attempt reach rate**. Coverage is an **after-7-attempts completion rate**.
They were never comparable quantities, and nothing in the artifact says so.

## The arithmetic closes

A warm re-probe of the same engine ~3 h later, same parameters:

| When | Reach | 95% CI |
| --- | --- | --- |
| At deploy (05:45, gate) | 1/60 = **1.7%** | 0.003–0.089 |
| Warm (08:50, re-probe) | 15/60 = **25.0%** | 0.158–0.372 |

The intervals do not overlap, so the rate genuinely improved with engine age. Applying the eval's
retry budget to the warm rate:

```
1 − (1 − 0.25)^7 = 86.6%      observed coverage = 56/64 = 87.5%
```

Retries explain the gap almost exactly. The engine served a quarter of single attempts and the
SDK's six retries carried the eval the rest of the way.

## Three hypotheses, and what actually held

- **H1 — cold-start starvation.** *Partly.* The rate really did climb (1.7% → 25%, disjoint CIs),
  so engine age matters. But the engine was never idle: it logged ~300 events/min continuously
  from 05:45, and worker-start rate was flat across both windows (3.8/min during the probe,
  3.5/min during the eval). Nothing was "waiting to boot", and warming stops far short of the bar.
- **H2 — the paths differ.** *Yes, and this is the mechanism.* Confirmed from source, not inferred.
- **H3 — intermittency.** Subsumed: the rate is time-varying but the variation is nowhere near
  large enough to reconcile 1.7% with 88% on its own.

## The gate is not mis-tuned

The tempting conclusion — that 0.8 is unreachable and the gate should be relaxed — is wrong.
Campaign 01 probed ten byte-identical engines with this same single-attempt method:

| reach | engines |
| --- | --- |
| 97–100% | **six** |
| 55%, 35% | two |
| 6%, 0% | two |

A healthy engine scores ≥97% on single attempts. 25% sits in the unhealthy tail, beside
lottery-04's 35%. **The threshold is achievable and the gate was right to reject all three draws.**

## What is actually wrong

1. **The gate does not gate.** After `max_rerolls`, it hands the eval an engine it has just judged
   unfit. The verdict is recorded and ignored.
2. **Three consecutive bad draws.** At Campaign 01's ~60% healthy rate that is `0.4³ ≈ 6%` — possible,
   but worth watching for a shifted distribution rather than assuming bad luck.
3. **Retries hide it.** Six retries turn a 25% engine into 88% coverage, which looks like a
   successful eval. The missing 12% is not random: it is dropout on an engine known to be sick.
4. **That dropout is exactly what a noise floor measures.** CLAUDE.md already warns the floor
   "varies with load and with how many arms run at once". A floor measured on a 25% engine
   describes that engine's dropout, not the pipeline's noise.

## Consequences

- **Do not reuse this engine for campaign 06.** An earlier plan proposed reusing it via the KFP
  deploy cache, on the mistaken belief it had passed the gate. It failed at 1.7% and is at 25% now.
- **A failed gate must stop a characterisation run.** Measuring a noise floor on an engine the
  gate rejected produces a number about the engine.
- **Report `rate` and coverage with their definitions attached**, or the next reader repeats this
  investigation. They differ by a factor of ~50 and neither is wrong.

## Open

- Has the healthy fraction dropped since 2026-08-24? Three bad draws is weak evidence; a
  ten-engine repeat of Campaign 01 Phase A would settle it and would be the cheapest way to find
  out whether the lottery has got worse.
- Does reach keep climbing past 25% with more age, or plateau? Two points cannot say.
