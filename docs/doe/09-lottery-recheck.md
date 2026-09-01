# Campaign 09 — Has the deploy lottery got worse?

**Status:** **Complete 2026-09-01 — the lottery has not shifted; it was bad luck** · **Blocks:** campaign 06, and therefore 07 and 08
**Repeat of:** [01-engine-lottery.md](01-engine-lottery.md) Phase A, unchanged

## Question

Campaign 01 measured, on 2026-08-24, that **six of ten** byte-identical engines
served ≥97% of single attempts, two were middling (55%, 35%) and two were
effectively dead (6%, 0%). Everything since has been sized from that ~60%
healthy fraction — in particular `max_rerolls: 2`, chosen because three draws
clear the bar ~94% of the time.

On 2026-09-01 the campaign-06 validation arm drew **three engines and all three
failed**, at 1.7% reach after two rerolls. Under the Campaign 01 distribution
that is `0.4³ ≈ 6%` — unlikely, not impossible. The question is whether it was
bad luck or whether the distribution has moved.

This matters more than a reroll budget. If the healthy fraction has dropped,
every campaign this repo plans is more expensive and less reliable than its
estimate, and the finding belongs in the escalation rather than in a config.

## Design

**Phase A only, unchanged from Campaign 01**, so the two are comparable:

- Ten byte-identical `bare-gemini` engines, `min_instances=2`, same minimal
  instruction ("reply with exactly the word OK").
- 100 attempts per engine, strictly serialized per engine, arms concurrent,
  5 s spacing — `wrangler/tools/boot_probe.py`.
- `reached = event_count > 0`, i.e. **single-attempt** reach. Not eval
  coverage, which is a completion rate after `EVAL_MAX_RETRIES` attempts and
  differs by ~50x on a degraded engine.
- Deployed via `scripts/deploy_probe_arms.py --replicates 10 --campaign 01r`.

Phase B (the reroll) is **not** repeated. Its conclusion — redeploying redraws
the rate — is what the health gate already assumes, and re-establishing it costs
another four deploys without changing any decision.

## Pre-registration

Written before the probe ran.

- **n = 100 per engine.** No engine gets extra attempts, in either direction.
- **Report all ten**, whatever the spread, including any that fail to deploy.
- **The comparison is the healthy fraction** — engines at ≥97% — against
  Campaign 01's 6/10. Not the mean reach, which a single dead engine dominates.
- **Stopping rule:** one pass. No extending a promising or disappointing result.
- **Secondary, declared in advance so it is not a fishing expedition:** does
  reach climb with engine age? The 1.7% → 25% observation on engine
  `3126647680801964032` was two points on one engine, and `lottery_b`'s joined
  age table shows reach *flat or falling* with worker age (0–2 s: 100%,
  90 s+: 76.5%). These disagree and one probe cannot settle it; record the
  numbers and say so.

## What each outcome would mean

| Healthy fraction (≥97%) | Reading |
| --- | --- |
| ≈ 6/10, as before | Bad luck. Set `max_rerolls: 4` (`0.4⁵ ≈ 1%`) and run campaign 06 |
| Materially below 6/10 | **The platform regressed.** A bigger finding than the noise floor; it goes into the escalation, and campaign 06's cost estimate has to be redone |
| Materially above 6/10 | The three failures were unluckier still, or something about the c06 deploy config differs from a bare probe engine — worth one look at `min_instances` and the MCP toolsets before proceeding |
| Deploys themselves start failing | Not a lottery question at all; stop and report |

Note the asymmetry: the first row leads to a config change, the second to an
escalation. Both are useful. There is no outcome here that wastes the ~4 h.

## Cost

10 deploys at ~5 min ≈ 50 min, then 1,000 attempts. Campaign 01 measured that
concurrency does **not** multiply throughput — something beneath
`async_stream_query` serializes, giving ~1.5× a single arm regardless of arm
count — so budget `total_attempts / 6` ≈ **~3 h**, not the 14 min the spacing
arithmetic suggests.

Engines carry `lifecycle: ephemeral` and `campaign: 01r`, and teardown via
`wrangler engines prune` is the last step, not a later chore.

## Result

**Run 2026-09-01. Ten engines deployed clean, 1,000 attempts, n=100 each as registered.**

| engine | reach | 95% CI | | engine | reach | 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| lottery-07 | **100%** | 0.963–1.000 | | lottery-08 | 75% | 0.657–0.825 |
| lottery-03 | **99%** | 0.946–0.998 | | lottery-04 | 53% | 0.433–0.625 |
| lottery-05 | **99%** | 0.946–0.998 | | lottery-01 | 49% | 0.394–0.587 |
| lottery-09 | **99%** | 0.946–0.998 | | lottery-02 | 49% | 0.394–0.587 |
| lottery-06 | **98%** | 0.930–0.994 | | lottery-10 | **0%** | 0.000–0.037 |

**Healthy fraction: 5/10 at ≥97%**, against Campaign 01's 6/10.

### The lottery has not shifted

Fisher exact on 5/10 vs 6/10 gives **p = 1.000**. There is no evidence of a change,
and with n=10 per campaign there could not have been unless the shift were enormous.
The pre-registered reading is the first row: bad luck.

The three consecutive failures that prompted this are unremarkable at the pooled
rate — `0.45³ = 9.1%`. Unlucky, not diagnostic.

**Pooled estimate across both campaigns: 11/20 = 55% healthy.** That is the number
to size budgets from, not either campaign alone.

The shape reproduces too, and it is the more useful finding: the distribution is
**bimodal, not continuous**. Five engines at 98–100%, then a gap, then 75/53/49/49/0.
Nothing sits between 75% and 98%. An engine is either fine or it is not, which is
what makes a threshold at 0.8 a sensible instrument rather than an arbitrary cut.

### Reroll budget

| max_rerolls | draws | P(all fail) at 55% healthy |
| --- | --- | --- |
| 2 (current) | 3 | 9.1% |
| 3 | 4 | 4.1% |
| **4** | **5** | **1.8%** |
| 5 | 6 | 0.8% |

Set to **4**. Nine percent is too high for an overnight campaign where a failure
costs the night; 1.8% is acceptable and each extra reroll costs ~5 min of deploy
plus ~5 min of probe only in the cases that need it.

### Secondary: reach does not improve with age

Registered in advance because two earlier observations disagreed. Both new
measurements say **flat**:

- **By worker age at request** (nonce join): 0–2 s 66.7%, 30–90 s 61.3%, 90 s+ 72.6%.
  Overlapping intervals throughout.
- **By attempt index**, pooled over all ten engines: 69% in the first ten attempts,
  71% in the last, never leaving 66–76% in between.

This kills the warming explanation for the health gate. Engine
`3126647680801964032` did go 1.7% → 25% over three hours, and that remains
unexplained, but it is not an instance of a general effect — there is no general
effect. **A gate verdict taken immediately after deploy is therefore valid**, and
the earlier suggestion of adding a warmup wait before probing is withdrawn.

### The join agrees with the client

Client-side `reached` and the nonce join match exactly on every engine checked
(lottery-10 0/100 both ways, lottery-04 53/100, lottery-08 75/100). The cheap
client-side measurement can be trusted; the join is needed for per-worker
attribution, not for the reach rate itself.

Raw and joined rows archived at `docs/data/probes/lottery_01r{,.joined}.jsonl.gz`.

### What this means for campaign 06

It clears it to run. The gate is a valid instrument, the threshold is achievable
by half of all deploys, and `max_rerolls: 4` makes a wholly-failed arm a 1.8%
event rather than a 9% one.
