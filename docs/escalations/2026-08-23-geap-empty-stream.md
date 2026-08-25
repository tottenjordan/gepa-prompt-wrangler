# Agent Engine returns HTTP 200 with an empty event stream and no inference

**Status:** ready to file · **Filed:** _(not yet)_ · **Case:** _(none)_
**Reported by:** GEPA Prompt Wrangler team
**Date of measurement:** 2026-08-23, extended 2026-08-24
**Supporting analysis:** [../analysis/2026-08-23-geap-empty-stream-doe.md](../analysis/2026-08-23-geap-empty-stream-doe.md)
**Worker-level follow-up:** [../doe/01-engine-lottery.md](../doe/01-engine-lottery.md)

---

## 1. Summary

`async_stream_query` against a healthy, fully-started Agent Engine returns **HTTP 200 with
zero events and no error**, and the server performs no inference for that request at all.

**The failure belongs to individual worker processes.** Across 2,360 requests and 14
engines, a worker is almost always either permanently good or permanently bad: of 626
worker processes observed with a per-request join, **only 20 (3.2%) ever both succeeded and
failed** — 330 always succeeded, 276 always failed. The bad ones boot normally, log
`Application startup complete`, accept requests, return 200, and never run an inference.

**Which workers an engine gets appears to be decided at deployment.** Ten byte-identical
engines — same source package, same model, same config, deployed within an hour — ranged
from **0% to 100%** reach at n=100 each: six effectively perfect, two effectively dead, two
in between. Redeploying a dead engine in place with identical content redraws the rate
(0%→50%, 6%→56%), so it is not durably attached to the engine id.

Pooled over a mixture of such engines the failure rate was **31.7%** (95% CI 28.8–34.7%),
but that average is not a property of the service so much as of the mix.

The response is indistinguishable from a legitimately empty answer, so a client cannot
detect it, cannot attribute it, and cannot correctly decide to retry. In our workload it
silently removes ~17% of cases from every evaluation run, and every caller we own now
spends up to ten attempts per request to work around it.

**What we are asking for is in §7 and it is narrow:** do not use 200 for a request the
service did not serve.

## 2. Environment

| | |
| --- | --- |
| Project | `hybrid-vertex` (number `934903580331`) |
| Region | `us-central1` |
| Resource | `aiplatform.googleapis.com/ReasoningEngine`, deployed via `source_packages` |
| Framework | `google-adk`, ADK 2.7.1 |
| SDK | `google-cloud-aiplatform` 1.165.1 |
| Scaling | `min_instances: 2`, passed explicitly on every engine |
| Client call | `agent_engines.get(...).async_stream_query(user_id, session_id, message)` |

Four engines, deployed back to back on 2026-08-23, identical but for two factors:

| arm | engine id | toolsets | model |
| --- | --- | --- | --- |
| `mcp-claude` | `8437205280076333056` | 3 MCP toolsets | `claude-sonnet-4-6` (global) |
| `bare-claude` | `3191356139119837184` | none | `claude-sonnet-4-6` (global) |
| `mcp-gemini` | `554498557294411776` | 3 MCP toolsets | `gemini-3.5-flash` (global) |
| `bare-gemini` | `1373309264545710080` | none | `gemini-3.5-flash` (global) |

A further **ten byte-identical engines** were deployed on 2026-08-24 for §4.2b, all matching
the `bare-gemini` configuration above:

```
923793726738792448  4728209511960018944  8555143295318097920  6346690628046290944
4040847618832596992 5725756829422583808  3482401265038655488  890016729533513728
12377752149688320   642881699981557760
```

Every engine ran the same trivial instruction: *"Reply to every message with exactly the
word OK and nothing else."* Prompts were one line and provoked no tool use.

## 3. Symptom

For an affected request:

- HTTP **200**
- the event stream yields **zero events**
- **no exception**, no error payload, no status detail
- latency is **normal** — median 10.8s against 10.5s for successful requests, so it is not
  waiting on a timeout
- the server-side structured log stream contains **no inference event** for the request:
  no `gen_ai.input.messages`, no model call, nothing

The client's only observable is "the agent returned nothing", which is a valid answer for a
language model to give.

## 4. Evidence

### 4.1 How each request was tied to what the server did

Every prompt carried a unique nonce. Agent Engine's structured log stream records the full
prompt in `labels."gen_ai.input.messages"`, so whether the model ran for a *specific*
request is a lookup rather than an inference:

```
resource.type="aiplatform.googleapis.com/ReasoningEngine"
AND resource.labels.reasoning_engine_id="<ENGINE_ID>"
AND labels."gen_ai.input.messages":"Probe id"
```

**Across all 960 requests, this agreed with the client-side event count 960 times and
disagreed 0 times.** An empty 200 always means no inference was performed.

Requests were sent strictly one at a time per engine (arms ran concurrently against
*different* engines), so each client window pairs with exactly one server-side
`POST /api/stream_reasoning_engine`. 948 of 960 (98.8%) joined; the remaining 12 are
reported rather than dropped.

### 4.2 Rate

| arm | replicate 1 | replicate 2 | pooled failure rate |
| --- | --- | --- | --- |
| `mcp-claude` | 81.7% | 53.3% | **67.5%** [0.613–0.731] |
| `bare-claude` | 12.5% | 8.3% | **10.4%** [0.072–0.149] |
| `mcp-gemini` | 5.0% | 3.3% | **4.2%** [0.023–0.075] |
| `bare-gemini` | 43.3% | 45.8% | **44.6%** [0.384–0.509] |
| **all** | | | **31.7%** [0.288–0.347] |

n = 120 per cell per replicate, Wilson 95% CI. Sample size was fixed before any data was
collected, and no arm was extended after the fact.

### 4.2b A worker is persistently good or persistently bad

Ten byte-identical engines, 100 attempts each, 2026-08-24
([../doe/01-engine-lottery.md](../doe/01-engine-lottery.md)):

| reach | engines |
| --- | --- |
| 97–100% | 6 |
| 35–55% | 2 |
| 0–6% | 2 |

Joining every request to its serving worker across those 1,000 attempts:

- **626 worker processes. 330 always succeeded, 276 always failed, 20 (3.2%) did both.**
- Requests-per-worker correlates with reach at **r = 0.954** across the ten engines: a
  healthy engine reuses each worker ~2.2 times, a dead one burns ~1.05 workers per request.
- Redeploying in place with identical content redraws the rate: 0%→50%, 6%→56%, while two
  100% engines stayed at 97% and 99%.

This is the most actionable thing we can tell you, and it accounts for every other
observation below: the per-engine stability, the failure of `min_instances` to help (it
keeps bad workers warm too), and why no client-side strategy moved the rate.

### 4.3 The workers serving these requests were fully started

We initially believed the cause was requests being admitted to workers that had not finished
booting. **Our own data refutes that**, and we would rather say so than send you a wrong
diagnosis:

| | n | reach |
| --- | --- | --- |
| served by a worker that had logged `Application startup complete` | **948** | 69.2% |
| served by a worker still starting up | **0** | — |

All 292 joined empty responses came from workers that had completed startup. The median age
of the serving worker at request time was **215 seconds**; the 10th percentile was 39s.

Reach does not vary with worker age (6 / 13 / 54 / 132 / 743 requests in the 0–2s, 2–10s,
10–30s, 30–90s and 90s+ bins, reaching 83.3 / 53.8 / 75.9 / 66.7 / 69.3%). Application
startup itself is sub-second on all four engines — median 0.00s from `Started server
process` to `Application startup complete`.

The ten-engine run reproduced this independently: all 998 joined requests were served by
workers past `Application startup complete`, median age ~1,100s. **A bad worker is not a
young worker — it is a fully-started one that never runs an inference.**

### 4.4 The service can already report this correctly

**12 of 960 requests (1.2%) returned `400 Reasoning Engine Execution failed … Service
Unavailable`.** That response is visible, attributable and safely retryable. The remaining
292 failures took the same journey and returned 200. Whatever distinguishes them, the
error-reporting path exists and is reachable.

## 5. What we ruled out

| hypothesis | how | result |
| --- | --- | --- |
| Our agent's startup cost (3 MCP handshakes at import) | `bare-*` arms have no toolsets and sub-second startup | **Not the cause.** `bare-gemini` still fails 44.6% |
| Claude-specific, or the `global` endpoint | both model families, both endpoints | **Not the cause.** Both families affected; best and worst arms are one of each |
| `sync` vs `async` stream_query | alternated against one engine | identical (2,2 / 0,0 / 2,2 / 0,0) |
| Session identity decides routing | new user+session / same user / same session, interleaved | flat: 1/12, 2/12, 1/12 |
| Pacing — bursts find warm workers | sequential vs concurrent, n=30 each | 17% vs 33%, Fisher **p=0.23** |
| A warm-up burst primes workers | cold burst vs discarded warm-up then burst | 28% vs 11% — no better |
| `min_instances` | 0 vs 2, 75 requests each | identical (1.3 startups per request either way) |
| Cold/booting workers | per-request join, 948 + 998 requests | **refuted** — 0 of 1,946 served during boot |
| Anything we configure | 10 byte-identical engines, 100 attempts each | **refuted** — 0% to 100% across identical deployments |

## 6. Impact

- **Evaluation runs lose cases silently.** Server-side `client.evals.run_inference` hits the
  same path. The empty stream becomes `{"error": "Failed to parse agent run response [] to
  agent data: list index out of range"}` stored *as the response text*, which then fails
  rubric rendering, which then makes the whole result file unparseable under `extra='forbid'`
  — so one empty stream drops that case from **every** metric. A recent 64-case run scored 53.
- **Every client we own now retries around it.** Ten attempts per eval case, six per traffic
  query. At a ~32% per-attempt failure rate this is load-bearing, and it multiplies cost and
  wall-clock across the board.
- **It is unretryable by contract.** Because the failure is a 200 with an empty body, a
  correct client cannot distinguish it from an agent that legitimately had nothing to say.
  Retrying is a guess.

## 7. What we are asking for

1. **Do not return 200 for a request the service did not serve.** A 5xx, or the existing
   `400 … Service Unavailable`, would make this detectable and correctly retryable. This is
   the whole ask; everything else below is optional.
2. If the empty stream is intentional in some cases, **a response header or trailer** that
   distinguishes "the agent produced no output" from "we did not run the agent".
3. **What makes a worker process permanently unable to serve** — §4.2b. Ten identical
   deployments produced anywhere from 0% to 100% healthy workers, and we cannot see from
   outside whether the difference is the worker, its host, its placement, or something in
   provisioning. This is the root cause; items 1 and 2 are about making it survivable.

## 8. Open questions we could not answer from outside

- What happens to the request between the `POST /api/stream_reasoning_engine` that the
  container logs and the inference that never runs? The worker logs the request and then
  simply produces nothing.
- Why is the rate so strongly per-engine and so stable within an engine? `mcp-gemini` sat at
  95.8% reach across both replicates while `mcp-claude` sat at 32.5% — same project, same
  region, same day, same build tooling.
- Is the worker-churn correlation causal? The worst arm was the only one spawning more
  workers than it served requests (1.68 boots per request, vs 0.75–0.95 elsewhere).
- Does `container_concurrency` interact with this? We did not vary it.

## 9. Reproducing

Roughly 30 minutes end to end:

```bash
# 1. Deploy a minimal agent -- no tools, sub-second startup
uv run python scripts/deploy_probe_arms.py --arm bare-gemini

# 2. 120 requests, one in flight at a time, no retries
uv run python -m wrangler.tools.boot_probe --arm bare-gemini=<ENGINE_ID> \
    --n 120 --spacing 5 --block-size 30

# 3. Join each request to the serving worker and to the inference log
uv run python -m wrangler.tools.boot_probe_join outputs/probes/<run>.jsonl --lead-in 90
```

Note when reading the container logs directly: everything is emitted at `DEFAULT` severity,
so a `severity>=WARNING` filter returns nothing at all and looks like a clean bill of health.
Filter on `textPayload`.

## 10. Raw data

- `outputs/probes/doe_rep1.jsonl`, `doe_rep2.jsonl` — one row per attempt
- `outputs/probes/doe_rep{1,2}.joined.jsonl` — the same rows with serving worker, worker age,
  boot state and inference-log membership
- `outputs/probes/lottery_a.jsonl`, `lottery_a.joined.jsonl` — ten identical engines, 1,000
  attempts, the per-worker analysis in §4.2b
- `outputs/probes/lottery_b.jsonl` — the same four engines after an in-place redeploy
