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

**Not migrating yet, deliberately.** Two reasons:

1. `agentplatform.Client` exposes `runtimes`, `evals`, `sessions`, `sandboxes`,
   `prompt_optimizer` — and **no `agent_engines`**. So this is not a client
   rename with the same sub-API underneath; `agent_engines` → `runtimes` is a
   real interface change that needs its own investigation, not a find-replace.
2. ADK 2.8.0 has not migrated. It still imports `vertexai.preview.rag` itself,
   and the GEAP builder still probes `import google.adk, vertexai.agent_engines`
   when deciding whether to install its default ADK requirements. Moving ahead
   of ADK splits us across two clients for no benefit.

**Revisit when ADK ships a release that uses `agentplatform`.** That is the
signal; the `FutureWarning` alone is not, because it has no removal date
attached and the replacement does not yet cover the surface we need.

Related: [adk-patch-status.md](adk-patch-status.md), [repo-traps.md](repo-traps.md).
