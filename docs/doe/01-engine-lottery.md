# Campaign 01 — Is the engine failure rate a deployment lottery?

**Status:** Not started · **Depends on:** nothing · **Run first**

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

10 deploys at ~5 min each ≈ 1 h sequential; ~40 min of probing per phase. Token cost
negligible — the probe prompt is one line and the reply is one word. Reuses
`scripts/deploy_probe_arms.py` and both probe tools.

## Result

_Not yet run._
