# Silent Failures

**Verified on:** 2026-08-20, during the Task 3.4 smoke test. #7 added 2026-08-21; #8 added
2026-08-21 from a log audit of the deployed engine.

Eight defects, six of them found in one sitting. None of them raised. Every one reported
success while producing worthless output — which is why they had survived so long. Grouped
here because the *shape* is the transferable part, not the individual bugs. #1–#7 are
fixed; **#8 is open.**

#8 is the one that argues for auditing logs on a schedule rather than after a failure:
every other defect here was found because something looked wrong, and #8 never will.

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

Measured end-to-end on a full optimize run against the Cloud Run MCP servers (2026-08-21,
18 generations, 102 metric calls, 89 min): **36 failed `tools/list` attempts, 29 rescued by
ADK's own one-shot retry** (`retry_on_errors` on `McpToolset.get_tools`), **7 invocations
left toolless** — roughly 7% of the run's metric calls scored an agent that had no tools.
All 36 were `tools/list`; never a tool *call*.

Two distinct shapes, not one. 32 were `anyio.BrokenResourceError` — the session's memory
stream already closed under it, which is the teardown race below. The other 4 were
`asyncio.TimeoutError` from `mcp_toolset._execute_with_session`'s own `asyncio.wait_for`
around `list_tools`, i.e. the server was reachable but slow. They arrive at
`_convert_tool_union_to_tools` identically and are swallowed identically, so a log grep for
one shape undercounts. Both are fixed by the same change, because both are round trips that
no longer happen.

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

**The lesson:** ADK's one-shot retry meant the loud signal (36 log lines) and the real
damage (7 cases) differed by 5×. When a library retries internally, count the *give-up*
message, not the *attempt* message, or you will report a catastrophe and fix the wrong
thing.

**Not yet re-measured with the fix in place.** The run above loaded `registry.py` before
the TTL was added, so its numbers are the *unfixed* baseline. The next full optimize run is
the one that shows whether 7 becomes 0 — `_ToolsetFailureCounter`'s summary line is the
number to read.

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

**Measured from the engine's own logs, 2026-08-21.** Over the 11-minute `eval_after`
window on engine `5638288480409747456`, the ReasoningEngine logged **40 `Application is
starting up` events against 31 `POST /api/stream_reasoning_engine` requests** — the engine
booted a worker slightly *more often* than it served a request. Over the full two-day
window: 164 startups, 113 requests, 7 SIGTERMs. So worker creation is not a startup
transient that settles; it is the steady state under this load pattern, and the exposure
window is open for most of a run. No crash indicators anywhere in the logs (no traceback,
no worker-exit, no OOM) — these are ordinary scale-ups, which is exactly why nothing
surfaces them.

**`GEAP_MIN_INSTANCES=2` was then measured, and it does not help.** Engine redeployed
2026-08-21 04:09 with `minInstances: 2` confirmed live on the deployment spec, then given
32 traffic queries over 35 minutes:

| | Before (`minInstances` unset) | After (`minInstances: 2`) |
| --- | --- | --- |
| Startups per stream request | 40 / 31 = 1.3 | 100 / 75 = 1.3 |

Identical. The floor keeps *containers* warm; it does nothing about GEAP spawning a fresh
**worker process** per request, which is the thing that actually eats the request. The
sharpest number: **75 stream requests were served across 68 distinct PIDs**, and only 18
of them (24%) had a `Received response from Claude` from that same PID. Roughly
three-quarters of attempts never reached the model. Keep the setting — it is cheap and
harmless — but do not count it as a mitigation, and do not spend more on it.

*Caveat on the PID arithmetic:* a PID is unique within a container, not across them, and
low PIDs (15–23) recur in every fresh container. So distinct-PID counts are a floor, and
the same-PID match can pair a request in one container with a response in another —
meaning the true reach rate is at most 24%, not at least.

**The traffic generator is the worst case for this, by design.** It opens a new session
with a fresh user id per query, so each query is eligible for a different worker. Compare
the same engine on the same day: the `eval_after` batch-eval window did 31 stream requests
and logged **50** Claude responses (1.6 per request — multi-turn tool use working
normally), while the traffic run did 75 and logged **19** (0.25). Same defect, but the
tool whose entire job is generating traces is the one most exposed to the thing that
prevents them. Its two retries are load-bearing, not belt-and-braces.

### No client-side design avoids it — three hypotheses, all refuted

Measured 2026-08-21 against engine `5638288480409747456`. Every arm interleaved with
rotating order, because consecutive blocks let an intermittent fault frame whichever
variable you changed (the dead end above is the same lesson).

| Hypothesis | Arms | Result |
| --- | --- | --- |
| Session identity decides routing | new user+session / same user / same session | 1/12, 2/12, 1/12 — **flat** |
| Pacing: bursts find warm workers | sequential 1s apart vs concurrent | 5/30 (17%) vs 10/30 (33%), Fisher **p=0.23** |
| A warm-up burst primes workers | cold burst vs discarded warm-up then burst | 5/18 (28%) vs 2/18 (11%) — **worse** |

The concurrency arm looked like a 5× win at n=12 (5/12 vs 1/12) and collapsed to nothing
at n=30. Worth remembering before acting on the first encouraging split you see.

Why none of it works is visible in the logs: **37 worker boots for 36 requests.** GEAP
starts a worker per request regardless of who is asking or how they pace it, and the
request is consumed during the ~8s boot. Latency confirms it — empty responses take
5–15s, i.e. they wait for the boot and then return nothing, rather than failing fast.

**So the traffic generator was redesigned around the failure rather than against it**
(`wrangler/tools/traffic.py`): bounded concurrency (wall-clock, since the rate is not made
worse by it), 6 attempts instead of 3 (at ~1-in-4 per attempt, 3 lands ~58% and 6 lands
~82%), retry on transient exceptions instead of abandoning the query, and — the part that
matters — `summarize_run()` reports the **per-attempt rate** and warns below 50%. Retries
were quietly converting a 17% server-side success rate into a respectable-looking trace
count; the tool now says both numbers. First run after the change: 8/12 traces from 48
attempts, printing `Attempt rate: 17%` and the warning.

Query to re-measure after changing `GEAP_MIN_INSTANCES`:

```bash
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"
  AND resource.labels.reasoning_engine_id="ENGINE_ID"
  AND textPayload:"Application is starting up"' \
  --project=PROJECT --freshness=1d --limit=500 --format='value(timestamp)' | wc -l
```

Note the container logs everything at DEFAULT severity, so `severity>=WARNING` returns
nothing at all and looks like a clean bill of health. Filter on `textPayload`, not
severity.

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

## 7. The autorater calls a tool, and the case vanishes

**Symptom:** `wrangler eval <exp> after` reported `SUCCEEDED`, printed five metrics and a
respectable 0.901 overall — over **4 cases in run 1 and 3 in run 3**, out of 5. The
aggregated `eval_after.json` carries 4 `per_case` rows where `eval_before.json` has 5.
Nothing in the CLI output says a case was lost; you have to compare the array lengths.

**Cause.** Reading the raw GCS result (the defect-3 technique — go under the library)
shows two of five `candidateResults` carrying an `error` instead of metrics:

```json
{"code": 3, "message": "The model response did not complete successfully.\nFinish reason:
 UNEXPECTED_TOOL_CALL.\nFinish message: Unexpected tool call:
 print(default_api.search_hotels(city = \"Chicago\"))\n...
 Please adjust the model safety_settings, or try a different prompt."}
```

The agent under test is **Claude**, which does not emit `default_api.foo(...)` — that is
the Gemini SDK's Python-style call convention, and `search_hotels` is neither the deployed
tool name (`wrangler_search_mcp_search_hotels`) nor the eval's expected one
(`search_mcp_search_hotels`). So the model that produced it is the **autorater**: handed a
tool-using trajectory to score, it tried to call the tools itself, and Vertex rejected its
own judge's response.

From there it is defect 3's cascade verbatim: `CandidateResult` has no `error` field and
`EvaluationItemResult` is `extra='forbid'`, so one rater error makes the **whole GCS file**
unparseable, `_convert_gcs_to_evaluation_item_result` swallows the `ValidationError`, and
the per-case fetch returns nothing. The surviving score comes from server-side summary
metrics over whichever cases the rater managed not to trip on.

**Why it matters more than a lost case.** The dropped cases are not random — they are the
ones with the richest tool trajectories, i.e. exactly the cases that discriminate between
prompts. A prompt that provokes more tool use loses more cases and can score *higher* for
it. Any before/after comparison across differing case counts is measuring the dropout, not
the prompt.

### Which metric was calling tools — the first guess was wrong

This note originally implied the culprit was our custom `tool_use_quality` LLMMetric,
whose prompt renders the tool trajectory and which we therefore control. Scanning all 118
archived result files in `gs://$GCP_STAGING_BUCKET/eval-results/` says otherwise:

| Metric | Error | Count |
| --- | --- | --- |
| `hallucination_v1` | UNEXPECTED_TOOL_CALL | 3 |
| `final_response_quality_v1` | UNEXPECTED_TOOL_CALL | 3 |
| `tool_use_quality` | `agent_data` unrendered (#5) | 15 |
| `safety_v3` / `instruction_following_v2` / `hallucination_v2` | unsupported version (#3) | 45 |

The tool calls come from **predefined** metrics. That matters because it kills the
prompt-side fix: we do not own `hallucination_v1`'s prompt, and this SDK has **no
`AutoraterConfig`** (no such symbol in `vertexai._genai.types`), so the rater's generation
config is unreachable. Option (b) was never available. Recovery was the only lever.

Worth knowing anyway, if it ever becomes reachable: the published cause of this finish
reason is prompt-induced phantom tools — a model shown tool-call syntax it has no
declarations for imitates it. The rater is handed a trajectory full of `search_hotels(...)`
and produces `print(default_api.search_hotels(city = "unknown"))`. Don't render raw
tool-call syntax into a judge prompt if you have the choice.

### Fixed

`wrangler/eval/evaluator.py`, pinned by `tests/test_evaluator_metrics.py`:

- **`_scores_from_raw_result()`** parses the result payload as plain JSON, keeping every
  metric that scored and returning the names of those that errored. A `None` score is
  dropped, never coerced to `0.0` — see #6 for what that costs.
- **`_extract_per_case_via_api()`** falls back to the raw file when the SDK hands back an
  empty `EvaluationItemResult` next to a live `gcs_uri` — the exact signature of the failed
  parse. Only on that path, so the happy path costs no extra request.
- **`_metric_coverage()` / `_coverage_warning()`** report cases-per-metric and warn when
  they disagree, on both the single and averaged paths.

Measured on the three affected files from the 02:09–02:13 `eval_after` window, which
previously contributed nothing at all:

```
result_8780…: {tool_use_quality_v1: 0.5, safety_v1: 1.0, instruction_following_v1: 0.0}
result_2023…: {safety_v1: 1.0, tool_use_quality_v1: 0.8, instruction_following_v1: 0.0}
result_3920…: {safety_v1: 1.0, instruction_following_v1: 0.667}
```

**Not fixed: the underlying rater flake.** Cases still lose individual metrics; they no
longer lose *all* of them, and the gap is now stated rather than absorbed. Retrying
errored cases was deliberately deferred — the finish reason is non-deterministic, so a
retry would likely fill the gaps, but shipping it alongside the parser would have made it
impossible to tell which change moved the numbers.

**The read path no longer goes through `extra='forbid'`.** That boundary ate scores three
ways (#3, #5, #7); all three now degrade to a missing metric instead of a missing case.

**The lesson:** the same `extra='forbid'` cascade has now eaten scores three separate ways
(#3 unsupported metric version, #5 empty agent stream, #7 rater tool call). It is not
three bugs; it is one brittle boundary that converts *any* per-item error into total data
loss. Fix the boundary, not the latest thing to cross it.

## 8. OTel spans are dropped under load, and online eval scores what survives

**Symptom:** nothing. The run succeeds and the reports are written. Found only by reading
the deployed engine's logs directly.

**What the logs say.** Engine `5638288480409747456`, during the `eval_after` window on
2026-08-21:

```
ERROR:    Failed to export span batch due to timeout, max retries or shutdown.
```

Five times in eleven minutes, from five different worker PIDs, interleaved with
`Retrying (Retry(total=2 → 1 → 0)) after connection broken by 'SSLError(SSLEOFError(...))'`.
This is the OTel `BatchSpanProcessor` giving up on a batch. The spans in it are gone.

**Why it matters here specifically.** `wrangler/eval/` scores OTel traces in the online
evaluator path — that is the whole mechanism. A dropped span batch is not a missing log
line; it is missing *input data* for a scorer that has no way to tell "the agent did not
do this" from "the export failed". It fails in the direction of a lower score, and it
correlates with load, so the busiest runs lose the most evidence. It also silently
undercuts `wrangler/tools/traffic.py`, whose entire job is to generate traces.

**Note it is worst exactly when the worker is short-lived.** `BatchSpanProcessor` flushes
on an interval; a worker that boots, serves one request and is recycled (see #5 — 40 boots
per 31 requests) may never reach a successful flush. The two defects compound.

### Cause: the exporter's retry loop is structurally dead

I first wrote this up as "not obviously ours to fix — the exporter is configured by the
GEAP runtime." That was wrong in the way that matters. We do not *construct* it, but we
control every setting it reads, and one of those settings is what decides the outcome.

`AdkApp(enable_tracing=True)` makes the Vertex SDK
(`vertexai/agent_engines/templates/adk.py:443`) build

```python
span_exporter = OTLPSpanExporter(session=AuthorizedSession(...), endpoint=..., headers=...)
span_processor = BatchSpanProcessor(span_exporter=span_exporter)
```

— **no timeout, no batch settings on either**. So both fall through to the standard OTel
env vars, which we already inject via `_build_source_config`. The endpoint is
`https://telemetry.googleapis.com/v1/traces`, posted through a google-auth
`AuthorizedSession`.

Now the part that took reading the exporter source to see
(`opentelemetry/exporter/otlp/proto/http/trace_exporter/__init__.py:196`):

```python
deadline_sec = time() + self._timeout          # default 10s
for retry_num in range(_MAX_RETRYS):
    backoff_seconds = 2**retry_num * random.uniform(0.8, 1.2)
    resp = self._export(serialized_data, deadline_sec - time())   # <-- whole budget
```

**The first attempt is handed the entire deadline.** So if a POST hangs until the timeout,
`backoff_seconds > deadline_sec - time()` is true immediately and the batch is discarded
after exactly one try. The retry loop cannot execute. It is not that the retries failed —
they never ran.

The logs prove that is the path taken rather than a fast error: **zero**
`Transient error ... retrying in Ns` warnings, zero `Failed to export span batch code: ...`
(the non-retryable branch), zero `Exporter already shutdown`. Only the deadline branch,
every time.

**The SSL EOF flakes are a symptom of the same sick egress, not the cause.** They are on
`/v1/projects/-/serviceAccounts/.../allowedLocations` — a google-auth *regional access
boundary* lookup (`google/auth/_regional_access_boundary_utils.py`). That lookup runs on a
background refresh thread and its failures are caught and logged at DEBUG, so it cannot
raise into the export. It is visible only because that client is configured with
`Retry(total=2)` and logs each attempt; the exporter's session has no such retries and so
fails silently at the transport layer. Same weather, different instrument.

### Fix

`_OTEL_ENV_VARS` in `wrangler/core/deploy.py`, pinned by
`tests/test_deploy.py::TestOtelSpanExport`:

| Var | Was | Now | Why |
| --- | --- | --- | --- |
| `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` | 10s | **30s** | The budget one attempt gets. The only lever that lets a slow export land. |
| `OTEL_BSP_EXPORT_TIMEOUT` | 30000ms | **60000ms** | Must stay above the exporter timeout or the processor abandons a live attempt. Note the unit differs from the line above. |
| `OTEL_BSP_SCHEDULE_DELAY` | 5000ms | **2000ms** | Fewer spans queued when a short-lived worker is recycled (#5). |
| `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` | 512 | **128** | Smaller payloads post faster, which is what keeps an attempt inside the timeout. |

Only the first is aimed at the proven mechanism; the other three shrink the exposure that
feeds it. **Env vars apply at deploy time**, so an existing engine keeps its old settings
until redeployed — which is also why the fix could be verified at all.

A dead `OTEL_ENV_VARS` copy in `examples/multi_model_agents/config.py` was deleted rather
than synced: nothing read it, and a second copy of settings that look applied but are not
is precisely how the un-tuned values would have outlived this fix.

**The lesson:** "configured by the platform" and "out of our control" are different
claims, and I collapsed them. The object was built by someone else's code with no
arguments — which made *every* parameter ours, through the environment. Check what a
default constructor reads before concluding you cannot influence it.

**Still true regardless:** any online-eval result computed from a run with span-export
errors in its window is a lower bound, not a measurement, and the ERROR is invisible to
`severity>=WARNING` queries because the container logs everything at DEFAULT.
