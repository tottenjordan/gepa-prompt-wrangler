# Silent Failures

**Verified on:** 2026-08-20, during the Task 3.4 smoke test.

Six defects found in one sitting. None of them raised. Every one reported success while
producing worthless output — which is why they had survived so long. Grouped here because
the *shape* is the transferable part, not the individual bugs (those are fixed).

Related: [repo-traps.md](repo-traps.md), [adk-patch-status.md](adk-patch-status.md).

---

## 1. A deployed agent with no tools role-plays tool use

**Symptom:** the agent answered travel questions fluently and emitted literal
`<tool_call>{"name": "search_flights", ...}</tool_call>` text in its response. Nothing
errored. Eval scored the response as prose.

**Cause:** the generated `registry.py` in the build package reads `*_MCP_SERVER` /
`*_MCP_URL` from the *GEAP server's* environment. The local deploy and redeploy paths
passed no `env_vars` at all. The `.env` copied into the build package cannot cover for
this: it lands at `/code/_geap_build_pkg/.env`, while `config.py`'s `load_dotenv()`
searches from the process CWD (`/code`) and never finds it.

**Why it stayed hidden:** ADK's `_MCP_GRACEFUL_ERROR_HANDLING` swallows toolset failures.
An agent with zero tools is a *valid* agent. The LLM, asked to use tools it does not have,
produces the next best thing — text that looks like a tool call.

**The lesson:** *"agent produced output" is not "agent worked."* For a tool-using agent,
assert on the trajectory (was a tool actually called?), never on the response text. A
model is happy to imitate the thing it cannot do.

Fixed in `_build_source_config` rather than at the two `stages.py` call sites, so deploy,
redeploy, `run_demo.py` and `deploy_agents.py` are all covered — and layered *under*
explicit `env_vars` so the pipeline's localhost MCP overrides still win.

### 1b. The same swallow, mid-optimization

Fixing the env vars did not close this off, because the swallow is structural.
`llm_agent._convert_tool_union_to_tools` catches **every** exception from
`toolset.get_tools()`, logs `Failed to get tools from toolset ...` at WARNING, and returns
`[]`. Any transient MCP failure therefore costs that single invocation its entire toolset,
and the agent answers toolless — which under GEPA is scored as **a bad prompt**. A network
blip is laundered into evidence about the instruction.

Measured on one optimize run against the Cloud Run MCP servers: a burst of 12 failed
`tools/list` attempts as generation 1 fanned out, 10 rescued by ADK's own one-shot retry
(`retry_on_errors` on `McpToolset.get_tools`), **2 invocations left toolless**. All 12 were
`tools/list` — never a tool *call*.

Ruled out along the way, so nobody re-checks them: the Cloud Run services have session
affinity on with `minScale=3`; an idle session survived 150s of nothing with no failure, so
the "Cloud Run drops idle connections in ~2 minutes" note in CLAUDE.md did not reproduce;
and there is no `header_provider` or auth credential, so prewarm and runtime resolve to the
same session key. What is left is a teardown/recreate race inside ADK's session pool —
`create_session()` hands back a session that a concurrent task then replaces.

**Fixed by removing the round trip rather than by racing it better.** `McpToolset`'s
`tool_list_cache_ttl_seconds` (set to 300s in both `registry.py` files) serves `tools/list`
from cache, so an invocation cannot lose its tools to a session blip it never touched. ADK
ignores `notifications/tools/list_changed`, so the cost is staleness up to the TTL — fine
for a fixed tool set. The cache lives on the toolset *instance*, which works here only
because GEPA's `agent.clone()` is a shallow copy and every candidate shares the object.

`_ToolsetFailureCounter` in `wrangler/optimize/optimizer.py` counts the give-up warning for
the length of the run and prints a summary, so a degraded optimization says so.

**The lesson:** ADK's one-shot retry meant the loud signal (12 log lines) and the real
damage (2 cases) differed by 6×. When a library retries internally, count the *give-up*
message, not the *attempt* message, or you will report a catastrophe and fix the wrong
thing.

## 2. The startup checks had never run, once, in production

The build package's `app.py` ended with:

```python
try:
    asyncio.run(_startup_checks())
except Exception:      # "cannot be called from a running event loop"
    pass
```

Under GEAP the module is imported *from inside a running event loop*, so `asyncio.run()`
raised immediately, the coroutine was never awaited, and the only trace was a
`RuntimeWarning` nobody reads. The health check written specifically to catch defect 1
had been dead since the day it was added.

**The lesson:** an `except` that names an expected-and-tolerable condition is the most
dangerous kind, because it reads as considered. Here the tolerated condition was the one
that *always* happened. If a guard's happy path is untested, assume it is the path never
taken. Fixed with a daemon thread that owns its own loop.

## 3. One unsupported metric zeroes out an entire eval run

**Symptom:** `create_evaluation_run()` returned `SUCCEEDED` with **0 scored cases**.

**The cascade, in four layers:**

1. An unversioned `types.RubricMetric.HALLUCINATION()` is resolved *client-side* through
   the SDK's `METRIC_LATEST_SPEC_NAME` table. In `google-cloud-aiplatform` 1.165.1 that
   points at `hallucination_v2` / `safety_v3` / `instruction_following_v2`.
2. The us-central1 eval service does not serve those versions. It rejects them
   **per metric**: `Unsupported predefined metric: hallucination_v2`.
3. `types.EvaluationItemResult` is `extra='forbid'` and its `CandidateResult` has no
   `error` field — so that one per-metric error makes the **whole GCS result file**
   unparseable.
4. `_convert_gcs_to_evaluation_item_result` swallows the `ValidationError` and returns an
   empty result. Run state stays `SUCCEEDED`.

So a single unsupported metric silently discards every score for every case, including
the four metrics that worked fine.

**The lesson:** a client-side "latest" table is a version negotiation with no handshake —
the client can be ahead of the server, and the failure surfaces nowhere near the cause.
Pin versions explicitly. The symptom of *over*-pinning is the opposite and much louder
error (the service reporting a v1 name as retired), so pinning is the safe direction.

**How it was settled:** rather than bisecting, one probe run submitted all 8 candidate
`(metric, version)` pairs at once and read the **raw GCS JSON** directly, bypassing the
pydantic model that cannot represent the error. That is the technique worth keeping —
when a client library's own types cannot express the failure, go under the library.

## 4. The predefined `tool_use_quality` metric penalizes correct tool use

Already in CLAUDE.md, restated here for the pattern: the predefined metric is
reference-free and auto-generates its rubrics **blind to the agent's available tools**,
so for a correctly tool-using agent it produces inverted rubrics
(`NO_TOOL_CALL_AS_EXPECTED`, `INFORMS_USER_OF_INABILITY`) and floors the score near
0.33–0.42.

Two hours went to hunting a trajectory-capture bug that did not exist. **The lesson:** a
score that is plausible-but-low reads as an agent problem. Before optimizing against a
metric, verify it can return 1.0 for known-good input — a metric with a ceiling below 1.0
is a broken instrument, and GEPA will happily spend a whole budget chasing it.

## 5. GEAP answers 200 with an empty event stream from a booting worker

**Symptom:** `stream_query` / `async_stream_query` against a healthy engine returns HTTP
200, zero events, no error — intermittently, roughly half the time under light load.

**Cause:** GEAP routes a request to a worker that has not finished starting up. The
ReasoningEngine logs show it plainly: worker `1237` logged `Application startup complete`
and served `POST /api/stream_reasoning_engine 200 OK` **in the same second**, while warm
worker `1169` handling the neighbouring request logged a real `rawPredict` to the model.
Startup is ~8s here — three MCP handshakes. The request is consumed during boot and never
reaches the agent.

**It is not a cold-start-from-idle problem.** New workers appeared mid-run under steady
back-to-back load (PIDs 956, 997, 1023, 1169, 1237 across two minutes), so
`GEAP_MIN_INSTANCES` narrows the window but cannot close it. **Callers must treat an empty
stream as retryable.** `wrangler/tools/traffic.py` retries twice with a fresh session;
that took one engine from 0/3 to 5/6 queries producing traces.

**A dead end worth not repeating:** this first looked like "sync `stream_query` is broken,
`async_stream_query` works" — an early async call succeeded where three sync calls had
failed. Alternating the two methods against one engine showed them identical
(2,2 / 0,0 / 2,2 / 0,0). **The lesson:** before concluding that A works and B does not,
check that the thing varying is A-vs-B and not time. An intermittent fault will happily
frame whichever variable you happened to change, and a small consecutive sample is exactly
how it does that.

**Blast radius — larger than it first looked.** Batch eval goes through server-side
`client.evals.run_inference`, which I initially assumed put it out of reach. It does not.
The empty stream reaches eval as a four-layer cascade, and each layer is quiet:

1. The agent run returns `[]`. The SDK cannot parse it and stores its own complaint **as
   the response text**: `{"error": "Failed to parse agent run response [] to agent data:
   list index out of range"}`. A non-empty string — so `_retry_failed_cases`, which
   checked for a *dict* with an `error` key, waved it straight through.
2. That row reaches scoring with no `agent_data`.
3. The custom `tool_use_quality` LLMMetric's `prompt_template` references `{agent_data}`,
   so it fails to render: `Variable agent_data is required but not provided`.
4. That one per-metric error makes the whole `EvaluationItemResult` unparseable
   (`extra='forbid'`, exactly as in defect 3), so the case is dropped from **every**
   metric — including the four that scored fine.

Net effect: on a 5-case smoke run, 2–3 cases were silently discarded and the reported
score was an average over whichever cases happened to hit a warm worker. The run still
said `SUCCEEDED`.

Fixed with a shared `_is_failed_response()` in `wrangler/eval/evaluator.py` that also
recognises an error payload stored as a JSON *string*, used by the failure detector, the
recovery check (which had the same dict-only bug and counted a still-broken retry as
recovered), and the pre-scoring row cleaner.

**The lesson:** an error that has been serialised into a data field stops looking like an
error. `"error" in response` is a type-dependent test, and the type changed somewhere up
the stack without anyone deciding it should.

## 6. GEPA optimized against a criterion nailed to zero

Defect 3 again, in the path nobody re-checked. Batch eval was pinned to explicit metric
versions when defect 3 was fixed; **GEPA's criteria were not**, because GEPA does not pick
the version — ADK does, inside `SafetyEvaluatorV1`, which asks for the *unversioned*
`PrebuiltMetric.SAFETY`. The SDK resolves that to `safety_v3`, us-central1 rejects it, and
every case returns `400 Unsupported predefined metric: safety_v3`.

The part that makes it a *silent* failure rather than a loud one is the interaction with
our own patch 4, which coerces a `None` score to `0.0` so a missing metric cannot crash the
run. Combined, GEPA saw a perfectly well-formed `safety_v1 = 0.00` on every candidate. It
never errors, never stalls, and spends its whole budget trying to move a number that is not
connected to anything.

Fixed as patch 6 — see [adk-patch-status.md](adk-patch-status.md).

**Two lessons.** First: *a fix scoped to "the eval path" does not cover the other eval
path.* Grep for the pattern, not the file you were debugging. Second, and more general:
**a defensive coercion and a broken input compose into a plausible number.** Patch 4 was
right to stop a `None` from crashing the run, but `0.0` is indistinguishable from a real
score. A sentinel that cannot be mistaken for data — or at minimum a counter of how often
the coercion fired — would have surfaced this in the first generation.
