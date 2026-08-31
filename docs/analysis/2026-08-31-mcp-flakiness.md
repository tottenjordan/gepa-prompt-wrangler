# The MCP flakiness was not MCP

**Investigated 2026-08-31.** Prompted by the v4 engine audit, which found 4–17% of workers
booting with an incomplete MCP toolset and one `opus` worker logging
`FATAL: no MCP tools connected — agent cannot use tools`.

**Conclusion: the MCP servers were healthy the whole time.** The failures were a 30-second
client-side probe budget being blown by containers that took two orders of magnitude longer
than usual to start. Three separate defects made that look like an MCP problem, and a fourth
made it look worse than it was.

---

## What the evidence said

**The servers are fine.** Over 3,000 Cloud Run requests to `wrangler-{search,booking,expense}-mcp`
in the window: all `200`/`202` except **seven** `409`s. No 5xx, no timeouts, no error spike.
All three run `minScale=3`, `maxScale=100`, session affinity on.

**The failing workers were slow, not unlucky.** Splitting `opus`'s workers by outcome:

| | n | import → MCP summary | import → serving |
| --- | --- | --- | --- |
| probe passed 3/3 | 24 | median **6.3s** | 6.1s |
| probe failed | 6 | median **109.2s**, max **834s** | max **1,179s** |

A 17× difference. And on the worker that failed all three, the log shows the handshake
completing **five seconds *after*** the probe had already logged failure:

```
16:53:01  MCP FAILED: .../agentregistry-...81a0-...
16:53:06  HTTP Request: POST .../wrangler-search-mcp/mcp "HTTP/1.1 200 OK"
16:53:06  Received session ID: 53d4d764ab7a49adbc79e5e75af6656e
```

That is a timeout, not a connection failure. `asyncio.wait_for(probe.get_tools(), timeout=30.0)`
gave up while a working handshake was still in flight.

**The agents were never actually toolless.** The probe deliberately builds *throwaway*
toolsets — the serving agent builds its own and connects lazily on first use. Sending a
tool-requiring query to all five engines, including the one that had logged `FATAL`:

| engine | tool called | role-played |
| --- | --- | --- |
| lite / flash / pro / sonnet / opus | `search_flights` | no |

So the `FATAL … agent cannot use tools` line was **false**. Claiming a fatal error that is
not one is how the real ones stop being believed.

## The four defects

1. **The probe budget was 30s** where the local `registry.py` uses 120s. The two
   implementations had drifted — exactly the trap CLAUDE.md flags for this file pair.
2. **The failure logged nothing.** `asyncio.wait_for` raises `TimeoutError`, whose `str()`
   is the empty string, so every failure rendered as `MCP FAILED: <server> -> ` with nothing
   after the arrow.
3. **The all-failed message overstated the damage**, as above.
4. **The startup duration was never reported**, so a starved container was indistinguishable
   from a broken dependency.

## Fixes, and what they measured afterwards

Probe budget → 120s with a test pinning it to the local registry's value; exception *type*
logged with a `(no detail)` fallback; the message rewritten to say it is the startup check
rather than the serving path; duration reported in the summary.

After redeploying all five engines, over 30 minutes:

| engine | workers | 3/3 OK | failed | probe duration |
| --- | --- | --- | --- | --- |
| lite | 20 | 20 | 0 | median 1.10s |
| flash | 20 | 20 | 0 | median 1.50s, max 12.7s |
| pro | 30 | 30 | 0 | median 1.20s |
| sonnet | 27 | 27 | 0 | median 0.80s |
| opus | 27 | 27 | 0 | median 0.70s |

**124 workers, zero MCP failures**, down from 4–17%.

## The real problem this exposed

Redeploying to pick up the fix **rerolled the engine health lottery**, and two of the five
came back broken:

| engine | reach after redeploy |
| --- | --- |
| flash | 100% |
| pro | 100% |
| sonnet | 93% |
| **opus** | **27%** |
| **lite** | **0/30** |

Nothing caught it. `deploy_agents.py` — the script that actually deploys these — had no
health check, so a completely dead engine shipped silently and would have been evaluated
against. That is Campaign 01's finding
([../doe/01-engine-lottery.md](../doe/01-engine-lottery.md)) landing in production.

**All three deploy paths are now gated** — `stage_deploy`, `deploy_agents.py`, and the KFP
component — with a test naming all three so a fourth cannot be added quietly. The gate
proved itself immediately on the reroll: it deployed `lite`, measured 28/60, refused it, and
redeployed.

## Recommended path

1. **Done — treat the probe as diagnostic, not as a gate.** It never gated anything; it just
   said alarming things. The budget, the message and the duration are fixed.
2. **Done — gate on what actually matters: can the engine serve?** Reach, measured, on every
   deploy path.
3. **Do not chase the MCP servers.** They were never the problem, and `minScale=3` plus
   session affinity is already the right configuration.
4. **Open: why are some containers 17× slower to start?** Not observable from outside, and
   it is the same shape as the empty-stream lottery — some workers are simply starved. Worth
   adding to the escalation rather than debugging locally.
5. **Cleanup available:** three duplicate MCP services (`search-mcp`, `booking-mcp`,
   `expense-mcp`, created 2026-08-07, `minScale=1`) sit alongside the `wrangler-` prefixed
   ones that `.env` actually points at. Three idle warm instances doing nothing.

## Related

- [../doe/01-engine-lottery.md](../doe/01-engine-lottery.md) — the deploy lottery this ran into
- [../notes/silent-failures.md](../notes/silent-failures.md) #5 — the empty-stream defect
- [../notes/engine-lifecycle.md](../notes/engine-lifecycle.md) — the gate's threshold and reroll budget
