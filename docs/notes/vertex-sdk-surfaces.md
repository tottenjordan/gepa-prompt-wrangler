# Two Vertex agent-engine surfaces, and which one to use

**Written 2026-09-08**, on the `google-cloud-aiplatform` 1.165.1 → 2.1.0 bump.

This repo talks to Agent Engine through **two different SDK surfaces**. Both
work at 2.1.0. They are not equivalent, and the difference already cost one
near-miss, so it is worth knowing which is which before touching either.

| surface | used by | status at 2.1.0 |
| --- | --- | --- |
| `vertexai.Client(...).agent_engines` | `core/deploy.py` | works; `vertexai.Client` emits a `FutureWarning` |
| `vertexai.agent_engines.<fn>` (module-level) | `tools/engines.py`, `tools/boot_probe.py`, `tools/traffic.py` | works for `get`/`list`/`delete`; `create` changed shape |

## The near-miss

`vertexai.agent_engines.create()` at 2.1.0 no longer accepts `source_packages`,
`requirements_file`, `entrypoint_module`, `entrypoint_object`, `class_methods`,
`agent_framework` or `labels`. Its parameters are now `agent_engine`,
`requirements`, `extra_packages` and friends — the older, pickle-shaped call.

`deploy.py` was already on `vertexai.Client(...).agent_engines.create(config=...)`,
where `types.AgentEngineConfig` still carries every one of those fields, so the
2.x bump was a drop-in. Had deploy gone through the module-level function, the
upgrade would have broken source-based deployment silently.

The tools only call `get`, `list` and `delete`, whose signatures did not move.
They are fine today, but they are on the surface that changes without notice.
**New code should go through `vertexai.Client`,** not the module-level
functions.

## The next migration: `agentplatform`

2.1.0 deprecates `vertexai.Client` itself:

```
FutureWarning: The vertexai.Client class is deprecated.
Please use agentplatform.Client instead.
```

It fires on `core/deploy.py:597`, so this is our code, not a library's internal
call.

`agentplatform` is **not a separate PyPI package** — it ships vendored inside
`google-cloud-aiplatform` 2.1.0, which is why `importlib.metadata.version(...)`
cannot find it while `import agentplatform` succeeds. Checking for it with
`metadata.version` reports "not installed" and is wrong.

**Migrated 2026-09-08** for all client-side code. An earlier draft of this note
deferred it, arguing that `agentplatform.Client` has `runtimes` and no
`agent_engines`, so the move was "an interface change rather than a rename".
**Measurement disproved that.** Correcting it rather than leaving it standing:

| checked against 2.1.0 | result |
| --- | --- |
| `AgentRuntimeConfig` vs `AgentEngineConfig` | field-identical, zero differences either way |
| `Runtime` vs `AgentEngine` | identical fields and methods |
| ADK binding on `get()` | `runtimes.get()` binds `async_stream_query`, `stream_query`, `create_session` exactly as `agent_engines.get()` |
| `evals` | agentplatform is a **superset** |
| `_evals_common`, `_gcs_utils` | both present |

`_build_source_config` returns a plain dict, so the config type name never
appears in our code — the two calls in `deploy.py` were the whole change.

`init` needs no choice at all: `vertexai.init` and `agentplatform.init` are the
*same bound method* on the same `google.cloud.aiplatform` initializer object,
verified `is`-identical. It is now imported from that canonical source, because
agentplatform's re-export falls back to `init = None` and so types as
non-callable.

### The one trap, and it is a quiet one

`vertexai._genai._evals_common` and `agentplatform._genai._evals_common` are
**different module objects**, and agentplatform's `evals.run_inference` calls
its own. `evaluator.py` monkey-patches `AGENT_MAX_WORKERS` and
`_execute_agent_run_with_retry` there so `EVAL_MAX_RETRIES` applies — the
mechanism that took eval coverage from 88% to 100%.

Moving the client without moving the patch makes it a **silent no-op**:
inference still runs, still succeeds, and quietly stops retrying. The unit
tests mock `run_inference`, so they never reach the retry path and would not
notice. This was live in the working tree before it was caught.
`test_sdk_private_surface.py` now asserts the patched module *is* the one the
client calls, plus a second test that the two modules are genuinely distinct,
so the first cannot silently go vacuous.

`_execute_agent_run_with_retry` also renamed its `agent_engine` parameter to
`runtime`. `evaluator.py`'s wrapper passes `*args, **kwargs` through and never
names it, so it survives untouched.

### Still on vertexai, deliberately

The generated build package's `from vertexai.agent_engines import AdkApp`
(`deploy.py`'s app template). It runs on the GEAP builder, emits no deprecation
warning, and is where a mistake costs a whole campaign — campaign 07 died there
on 2026-09-08 and surfaced only as `Build failed ... or other dependencies`.
`agentplatform.frameworks.adk.AdkApp` exists and is a superset (it adds
`credential_service_builder`), so the move is available whenever it is worth
the risk.

ADK 2.8.0 has not migrated either: it imports `vertexai.preview.rag`, and the
GEAP builder probes `import google.adk, vertexai.agent_engines` when deciding
whether to install its default ADK requirements. Neither blocks our client
code, but both are reasons the build package is not urgent.

Related: [adk-patch-status.md](adk-patch-status.md), [repo-traps.md](repo-traps.md).
