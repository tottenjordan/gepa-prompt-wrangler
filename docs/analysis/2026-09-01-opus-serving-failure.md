# Opus fails on Agent Engine, and it is not the model id, the region, or the prompt

**Investigated 2026-08-31 → 2026-09-01.** Prompted by opus failing six consecutive
health-gated deploys while the other four tiers sat at 93–100%.

**Conclusion: opus-tier models have a systematically elevated empty-stream rate on the
Agent Engine serving path.** Twelve deploys across three opus versions produced **zero**
engines above the 80% reach bar, with a concurrent control showing the other four tiers
healthy. Every model-side explanation was tested and eliminated.

---

## What was changed, and what it did

The region was **already** `global` — in both `.env` files, and pinned per-model by
`model_location()` in the registry, which is the authority. No change was needed there.

`OPUS_MODEL` was moved `claude-opus-4-6` → `claude-opus-4-8` → `claude-opus-5`:

| model | reach per gated deploy | passed the 80% bar |
| --- | --- | --- |
| `claude-opus-4-6` | 3.3%, 50.0%, 16.7%, 28.3%, 13.3%, 0.0% | 0 of 6 |
| `claude-opus-4-8` | 1.7%, 40.0%, 48.3% | 0 of 3 |
| `claude-opus-5` | 6.7%, 35.0%, 21.7% | 0 of 3 |

**Twelve draws, zero passes.** Best single draw 50%. `claude-opus-5` is now configured —
it is the newest and no worse — but the change did not fix anything.

## Everything ruled out

| hypothesis | test | verdict |
| --- | --- | --- |
| Wrong region | `.env` and `model_location()` | already `global` |
| Model not servable | direct `AnthropicVertex(region="global")` call to all three | all answer `OK` |
| Model too slow for a deadline | tool-using turn, `max_tokens=8192`, 3 reps each | 1.6–3.9s mean, comparable to sonnet |
| Claude family generally | sonnet is Claude | 93% healthy, concurrently |
| Scaling config drift | `min_instances` on every engine | all `2` |
| **Time-of-day / regional load** | **control probe on 4 engines in the same window** | **flash 100%, lite 100%, pro 93%, sonnet 93%** |
| **Measurement artifact** | **nonce join vs client count on a 60-request gate probe** | **29 = 29, exact** |
| Agent module differs | `diff opus_agent.py sonnet_agent.py` | identical but for model + prompt |
| **The opus prompt** | **redeploy with the generic prompt** | **51.7% — still fails** |

The two in bold are the ones worth dwelling on. The **control** is what rules out "the whole
region is unhappy tonight" — four engines measured 93–100% in the same hours opus was at
0–50%. The **nonce join** rules out the possibility that opus was answering and the probe
was failing to count it: Agent Engine's structured log stream recorded exactly 29 inferences
for 60 requests, and the client independently counted 29. The empty streams really are
requests for which no inference ever ran.

## What is left

The opus model, on the Agent Engine serving path, and nothing else. That is not something
observable from outside: the same model answers a direct `rawPredict` in under two seconds,
and the engine's own logs show `claude-opus-4-6:rawPredict "HTTP/1.1 200 OK"` whenever a
request does get through. Requests simply fail to reach the model far more often on opus
engines than on any other tier.

## Recommendation

1. **Keep opus out of eval sweeps.** At best 50% reach, every case against it is dropout,
   and any delta computed from it measures the dropout rather than the prompt.
2. **Add this to the escalation.** A model-correlated failure rate with a concurrent control
   is a far sharper signal than the aggregate 31.7% already reported, and it is reproducible:
   twelve deploys, three model versions, one prompt variant, zero passes.
3. **Leave `OPUS_MODEL=claude-opus-5`.** It is the newest and performed no worse; there is no
   reason to revert to a model six months older.
4. **The health gate stays on.** It is what caught this. Without it a 0%-reach opus engine
   would have shipped into an eval and produced numbers.

## Related

- [2026-08-31-mcp-flakiness.md](2026-08-31-mcp-flakiness.md) — where opus's failures first surfaced
- [../doe/01-engine-lottery.md](../doe/01-engine-lottery.md) — the per-deployment lottery this is *not*
- [../notes/silent-failures.md](../notes/silent-failures.md) #5 — the empty-stream defect itself
- [../escalations/2026-08-23-geap-empty-stream.md](../escalations/2026-08-23-geap-empty-stream.md)
