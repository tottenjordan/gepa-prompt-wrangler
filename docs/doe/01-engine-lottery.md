# Campaign 01 — Is the engine failure rate a deployment lottery?

**Status:** **Complete, 2026-08-24.** Yes — and it is a property of individual *worker
processes*, which is sharper than the question asked. · **Depends on:** nothing

## Question

Four engines deployed minutes apart from the same source package returned HTTP 200 with no
inference for 4.2%, 10.4%, 44.6% and 67.5% of requests
([../analysis/2026-08-23-geap-empty-stream-doe.md](../analysis/2026-08-23-geap-empty-stream-doe.md)).
That analysis had one engine per cell, so engine identity and the 2×2 factors are perfectly
confounded and it could not say which was responsible.

Is the spread a property of the *deployment* rather than of anything we configured — and if
so, can a bad engine be detected and rerolled?

The second half is why this campaign runs first. Eval runs currently lose ~11 of 64 cases to
this defect. If a bad engine can be spotted in sixty requests and replaced, coverage roughly
doubles for nothing, which is worth more than any prompt improvement measured in this repo
so far.

## Design

**Phase A — the lottery.** Deploy **10 byte-identical engines**: one model, one instruction,
`include_mcp=False` (fast deploys, and it removes the toolset variable entirely),
`min_instances: 2` passed explicitly so the ambient `GEAP_MIN_INSTANCES` cannot decide it.
Probe each with **100 attempts** — one request in flight per engine, engines concurrent,
5s spacing, blocks of 25 — using `wrangler/tools/boot_probe.py` unchanged.

**Phase B — the reroll.** Take the two best and two worst by reach rate. Call
`update_agent_from_source()` on each with byte-identical content, then re-probe at 100.

Join both phases with `wrangler/tools/boot_probe_join.py` for worker age and the nonce-based
model-reach check.

## Pre-registration

- n = **100 per engine per phase**. Wilson interval ±0.10 at p≈0.5 — ample to separate 4%
  from 68%, which is the effect in question.
- No engine gets extra attempts, in either phase. Report all ten whatever the spread.
- Phase B's four engines are chosen **by rank on Phase A**, fixed before Phase B runs.
- Report the unjoinable fraction and the PID-reuse count per window.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| **Wide spread across identical engines** | A per-deployment lottery. The 2×2's factor differences were confounded engine effects, and its stated limitation was correct. |
| **Tight clustering** | The 2×2 factors did matter after all, and the confound resolves the other way. Equally worth publishing, and it sends the escalation back for a rewrite. |
| **Redeploy rerolls the rate** | A deploy-time health gate works. Build it. |
| **Redeploy preserves the rate** | Something durable attaches to the engine id. That is a much sharper question for Google than the one in the current escalation, and it goes in. |
| **Bimodal rather than continuous** | Suggests a discrete cause — a placement, a zone, a bad host — and is worth naming as such. |

## Repo payoff

Conditional on Phase B rerolling: add `wrangler probe --engine-id <id> --gate`, ~60 attempts
post-deploy, failing the stage below a reach threshold, wired into `stage_deploy`
(`wrangler/orchestration/stages.py`). Threshold set from the Phase A distribution, not
guessed.

Do **not** build the gate if Phase B shows the rate is durable — then a failing engine cannot
be replaced by redeploying and the gate would only ever refuse to proceed.

## Cost

10 deploys at ~5 min each ≈ 1 h sequential. Probing 1,000 attempts takes **~3 h**, not the
~40 min a naive "ten arms run concurrently" estimate gives: measured throughput is about
1.5× a single arm regardless of arm count (4 arms → 6.9 attempts/min, 10 arms → 5.8/min),
because something beneath `async_stream_query` serializes. Budget `total_attempts / 6`
minutes. Token cost is negligible — the prompt is one line and the reply is one word.

Reuses `scripts/deploy_probe_arms.py --replicates` and both probe tools.

## Result

**Run 2026-08-24. 1,400 attempts. Both phases hit their pre-registered n.**
Data: `outputs/probes/lottery_a.jsonl`, `lottery_a.joined.jsonl`, `lottery_b.jsonl`.

### Phase A — ten byte-identical engines, 100 attempts each

| engine | reach | 95% CI |
| --- | --- | --- |
| lottery-07 | **100%** | 0.963–1.000 |
| lottery-09 | **100%** | 0.963–1.000 |
| lottery-10 | **100%** | 0.963–1.000 |
| lottery-01 | 99% | 0.946–0.998 |
| lottery-06 | 99% | 0.946–0.998 |
| lottery-05 | 97% | 0.915–0.990 |
| lottery-03 | 55% | 0.452–0.644 |
| lottery-04 | 35% | 0.264–0.447 |
| lottery-08 | **6%** | 0.028–0.125 |
| lottery-02 | **0%** | 0.000–0.037 |

Same source package, same model, same instruction, same `min_instances`, deployed within an
hour of each other. **0% to 100%.** Not a continuous spread: six engines are effectively
perfect, two are effectively dead, two sit in between.

So the 4.2%–67.5% spread in the earlier 2×2 was a **deployment lottery**, and its factor
readings were confounded engine effects exactly as that analysis warned. The pooled 31.7%
failure rate reported in the escalation is an average over a mixture of near-perfect and
near-dead engines, not a uniform service property.

### The mechanism: it is the worker process, not the request

The join places every request on the worker that served it. Of **626 worker processes,
only 20 (3.2%) ever both succeeded and failed** — 330 always succeeded, 276 always failed.

A worker is good or bad essentially for its whole life, and an engine's failure rate is
close to the bad fraction of its worker pool. That predicts the strongest correlate in the
data: how many requests a worker gets to serve before the engine moves on.

| engine | reach | distinct workers | requests per worker |
| --- | --- | --- | --- |
| 100% arms | 97–100% | 44–49 | 2.04–2.27 |
| lottery-03 | 55% | 78 | 1.28 |
| lottery-04 | 35% | 77 | 1.30 |
| lottery-08 | 6% | 92 | 1.09 |
| lottery-02 | 0% | 94 | 1.05 |

Pearson r = **0.954** across the ten engines. A healthy engine reuses each worker about
twice; a dead one burns a fresh worker per request and nearly all of them fail.

**These are not cold workers.** Consistent with the earlier finding, all 998 joined requests
were served by workers that had already logged `Application startup complete`, median age
~1,100s, and reach against worker age is flat. The bad workers boot normally and log
`bare probe ready` — they simply never run an inference when a request arrives.

### Phase B — redeploying rerolls the rate

`update_agent_from_source` with byte-identical content, then 100 fresh attempts:

| engine | before | after |
| --- | --- | --- |
| lottery-02 | 0% | **50%** |
| lottery-08 | 6% | **56%** |
| lottery-07 | 100% | 97% |
| lottery-09 | 100% | 99% |

The two dead engines came back; the two healthy ones stayed healthy. **The rate is not
durably attached to the engine id** — it is redrawn on redeploy. Note the rerolled engines
landed mid-range rather than at 100%, so a reroll is a fresh draw from the same
distribution, not a repair.

### What this changes

1. **A deploy-time health gate is worth building.** Six of ten deployments were ≥97%; a gate
   that probes after deploy and redeploys below threshold converts a ~69% pooled reach into
   something close to 99%. At current eval coverage that is roughly a doubling of usable
   cases, for about sixty one-line requests per deploy.
2. **The escalation gets sharper and should be revised before filing.** The ask changes from
   "31.7% of requests get a 200 with no inference" to "a persistent subset of *worker
   processes* accept requests, return 200, and never run an inference — 276 of 626 here,
   and which workers an engine gets is decided at deploy time." That is a far more
   actionable report, and it explains every earlier observation: the per-engine stability,
   why `min_instances` does not help (it keeps bad workers warm too), and why no
   client-side strategy moved the rate.
3. **Any past per-engine comparison is suspect.** Two engines differing is the null
   hypothesis here, not evidence about whatever else differed between them.

### Caveats

- One arm per configuration in Phase B (n=4 engines), so "reroll lands mid-range" rests on
  two observations.
- Whether the good/bad split is a property of the worker, its host, or its placement is not
  observable from outside. Named as an open question in the escalation rather than guessed.
