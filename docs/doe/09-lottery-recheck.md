# Campaign 09 — Has the deploy lottery got worse?

**Status:** Running 2026-09-01 · **Blocks:** campaign 06, and therefore 07 and 08
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

_Not yet run._
