# Migrate to `agentplatform.Client`

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-08-agentplatform-migration.md` and commit it — plan mode could only write to the scratch plan path.

**Goal:** Move every client-side Vertex call from the deprecated `vertexai` surfaces to `agentplatform`, without touching the GEAP-side build package.

**Architecture:** Introduce one shared client factory, migrate call sites to it, then move the three tools off the legacy module-level `agent_engines`. Each task is independently testable and independently revertable.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`, `google-cloud-aiplatform` 2.1.0 (which vendors `agentplatform`).

---

## Context

`google-cloud-aiplatform` 2.1.0 deprecates `vertexai.Client`:

```
FutureWarning: The vertexai.Client class is deprecated.
Please use agentplatform.Client instead.
```

It fires on **our** code — `wrangler/core/deploy.py:597` — not inside a library, so it is ours to fix. `agentplatform` is not a separate PyPI package; it ships vendored inside `google-cloud-aiplatform` 2.1.0, so nothing needs installing.

There is a second, quieter motivation. Three tools still use the **module-level** `vertexai.agent_engines.<fn>` surface. That surface's `create()` already changed shape at 2.1.0 — it dropped `source_packages`, `requirements_file`, `entrypoint_module`, `entrypoint_object`, `class_methods`, `agent_framework` and `labels`. `deploy.py` was unaffected only because it happens to use the `Client` surface. The tools only call `get`/`list`/`delete`, which did not move, but they sit on the surface that changes without notice.

Background: [docs/notes/vertex-sdk-surfaces.md](../../gepa/gepa-prompt-wrangler/docs/notes/vertex-sdk-surfaces.md) (PR #46).

### What the research established

Measured against the installed 2.1.0, not assumed:

| question | finding |
| --- | --- |
| `AgentRuntimeConfig` vs `AgentEngineConfig` | **field-identical**, zero differences either way |
| `Runtime` vs `AgentEngine` return type | identical fields and methods |
| ADK methods (`async_stream_query`, `create_session`, …) | `runtimes.get()` binds **exactly** the same set as `agent_engines.get()`, verified live against engine `2846971505114349568` |
| `runtimes.list()` | yields `api_resource` with `name`, `display_name`, `labels`, `create_time` — everything `engines.py` reads |
| `evals` sub-client | agentplatform is a **superset** (adds `import_evaluation_set`, `list_evaluation_sets`, `delete_evaluation_set`) |
| `_evals_common`, `_gcs_utils` | both present; `AGENT_MAX_WORKERS` and `GcsUtils(api_client)` unchanged |
| `AdkApp` | present at `agentplatform.frameworks.adk`, with an extra `credential_service_builder` param (superset) |

**This corrects my earlier assessment in `vertex-sdk-surfaces.md`,** which said `agent_engines` → `runtimes` was "an interface change, not a rename". On the config and return types it *is* effectively a rename. The note must be updated as part of this work rather than left contradicting the code.

### The one real incompatibility

`_evals_common._execute_agent_run_with_retry` renamed its parameter `agent_engine` → `runtime`.

`evaluator.py:610-613` wraps it with `*args, **kwargs` passthrough and never names that parameter, so **the wrapper is unaffected**. Only `tests/test_sdk_private_surface.py` needs updating, because it asserts on the signature by module path.

### Why the build package is out of scope

`deploy.py:286` and `:478` embed `from vertexai.agent_engines import AdkApp` inside the generated `app.py` template. That code runs on the GEAP builder, emits **no** deprecation warning, and is the surface where a mistake costs a campaign — campaign 07 died there on 2026-09-08 and surfaced only as `Build failed ... or other dependencies`. Deliberately deferred.

---

## Task 1: One client factory, used everywhere

**Files:** Create `wrangler/core/clients.py` · Test `tests/test_clients.py` (new)

Four call sites construct a client today, each repeating project/location:
`core/deploy.py:597`, `eval/evaluator.py:512`, `eval/online_monitors.py`, `scripts/diagnose_tooluse.py`.
`deploy.py` already has the right shape in `_get_client()` — promote it rather than inventing something new.

**Step 1 — write the failing test.**

```python
def test_the_factory_returns_an_agentplatform_client():
    from wrangler.core.clients import agent_client
    c = agent_client()
    assert type(c).__module__.startswith("agentplatform"), (
        f"got {type(c).__module__}; vertexai.Client is deprecated at aiplatform 2.1.0"
    )

def test_constructing_a_client_emits_no_deprecation_warning():
    """The whole point. A FutureWarning here means the migration did not take."""
    import warnings
    from wrangler.core.clients import agent_client
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent_client()
    bad = [w for w in caught if "deprecated" in str(w.message).lower()]
    assert not bad, [str(w.message) for w in bad]

def test_no_module_constructs_vertexai_client_directly():
    """Guards against a new call site reintroducing it.

    AST-based, not a text grep: a comment or docstring naming vertexai.Client
    is legitimate, and a regex cannot tell prose from code. Same reason
    tests/test_models.py walks the AST.
    """
```

The third test walks the AST of every module under `wrangler/` and `scripts/`, flagging any `Call` whose func resolves to `vertexai.Client`. Model it on the existing walker in `tests/test_models.py`.

**Step 2 — run, watch all three fail.**

**Step 3 — implement `wrangler/core/clients.py`:**

```python
def agent_client():
    """The one Vertex client. agentplatform, not vertexai.Client.

    aiplatform 2.1.0 deprecates vertexai.Client in favour of this. Read the
    environment at call time, never into a module constant -- the pipeline
    components set GCP_PROJECT_ID and GCP_REGION *inside* the component body,
    after the tarball is extracted, so a module-level bind would capture a
    stale project. Same rule as core/models.py.
    """
    import agentplatform
    return agentplatform.Client(project=GCP_PROJECT_ID, location=GCP_REGION)
```

**Step 4 — repoint the four call sites.** In `deploy.py`, keep `_get_client()` as a thin delegate so the two `create`/`update` call sites are untouched by this task.

**Step 5 — verify:** `uv run pytest tests/ -q --no-cov`, `ruff check`, `ruff format`, `ty check wrangler/`.

**Step 6 — commit:** `feat: one agentplatform client factory, replacing four vertexai.Client sites`.

---

## Task 2: `agent_engines` → `runtimes` in deploy

**Files:** Modify `wrangler/core/deploy.py:879-980` · Test `tests/test_deploy.py`

Two calls change, plus the config type name. `agentplatform.types` exposes `AgentRuntimeConfig` and **not** `AgentEngineConfig`, so the rename is mandatory, not cosmetic.

```python
remote = _get_client().runtimes.create(config=config)                    # was .agent_engines.create
remote = _get_client().runtimes.update(name=engine_id, config=config)    # was .agent_engines.update
```

**Step 1 — failing test.** `tests/test_deploy.py` already mocks the SDK; extend the existing mock assertions to expect `runtimes`. Add one structural test asserting `deploy.py` contains no `.agent_engines.` attribute access, so a revert is caught.

**Step 2 — implement.** Change the two calls and, wherever the config object is built, `types.AgentEngineConfig` → `types.AgentRuntimeConfig`. **Do not change the dict keys** — they are field-identical, verified.

**Step 3 — verify.** Unit tests are necessary but **not sufficient here**: they mock the SDK, which is exactly why campaign 07's requirements defect reached production. The live check is in Verification below.

**Step 4 — commit:** `feat: deploy through runtimes, the agentplatform equivalent of agent_engines`.

---

## Task 3: Move the three tools off the module-level surface

**Files:** Modify `wrangler/tools/engines.py:293-326`, `wrangler/tools/boot_probe.py:263-266,338-344`, `wrangler/tools/traffic.py:35-36,227,250` · Test `tests/test_traffic.py`, `tests/test_engine_labels.py`

Each follows one pattern. Replace:

```python
import vertexai
from vertexai import agent_engines
vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
agent = agent_engines.get(resource)
```

with:

```python
from wrangler.core.clients import agent_client
agent = agent_client().runtimes.get(name=resource)
```

Three notes that matter:

- **`get` is keyword-only.** `runtimes.get(name=...)`, not positional. Same for `delete(name=..., force=True)`.
- **`vertexai.init()` goes away** — the client takes project and location directly. Do not replace it with `agentplatform.init()`; that reintroduces process-global state the client already carries explicitly, which is the same trap `GOOGLE_CLOUD_LOCATION` set in CLAUDE.md.
- **`engines.py` list loop:** `runtimes.list()` yields objects whose `api_resource` carries `name`, `display_name`, `labels`, `create_time` — verified. Check whether the existing loop reads attributes off the wrapper or off `api_resource` and adjust accordingly.
- **ADK method binding is unchanged**, verified live: `runtimes.get()` binds `async_stream_query`, `stream_query` and `create_session` exactly as `agent_engines.get()` does. `traffic.py`'s `async_stream_query` and `boot_probe.py`'s calls need no change.

**Commit:** `feat: tools read engines through runtimes, off the legacy module surface`.

---

## Task 4: Repoint the private-surface guard, and correct the note

**Files:** Modify `tests/test_sdk_private_surface.py` · Modify `docs/notes/vertex-sdk-surfaces.md`

`test_sdk_private_surface.py` guards `vertexai._genai._evals_common` and `_gcs_utils`. After Task 1, `evaluator.py` reaches those through `agentplatform`, so the guard is watching the wrong module — it would pass while the real dependency broke.

- Repoint every import to `agentplatform._genai`.
- Update `test_the_retry_function_still_accepts_max_retries`: the signature is now `(row, contents, runtime, max_retries)`. Assert on `max_retries` (which is what `evaluator.py` actually depends on) and add a comment recording that `agent_engine` was renamed to `runtime`, and that `evaluator.py`'s wrapper survives it only because it passes through `*args/**kwargs`.

Then correct `docs/notes/vertex-sdk-surfaces.md`. It currently says the migration is deferred because `agentplatform.Client` "has `runtimes` but no `agent_engines`, so it is an interface change rather than a rename". The measurements above show the configs and return types are identical. Rewrite that section to say what was actually found, and narrow the remaining deferral to the build package alone.

**Commit:** `fix: guard the private surface we actually use, and correct the deferral note`.

---

## Verification

Unit tests mock the SDK. On 2026-09-08 that let a broken requirements set reach GEAP and kill a campaign, so the suite is a gate, not the proof.

1. **Suite green:** `uv run pytest tests/ -q --no-cov` (1133+ passing), plus `ruff check`, `ruff format --check`, `ty check wrangler/`.
2. **The warning is gone.** Run a deploy path with `python -W error::FutureWarning` and confirm no `vertexai.Client` warning. This is the literal ask; verify it directly rather than inferring it from the tests.
3. **Live deploy, the only real test.** One probe engine end to end:
   ```bash
   uv run python scripts/deploy_probe_arms.py --arm mcp-claude --campaign apmigrate
   uv run python -m wrangler.tools.boot_probe --arm mcp-claude=<id> --n 12 --spacing 3 --block-size 12
   ```
   Expect a successful GEAP build and 12/12 reach. This exercises Task 2's `runtimes.create` and Task 3's `runtimes.get` + `async_stream_query` against the real service. **Reap the engine afterwards** — `lifecycle: ephemeral`, per CLAUDE.md.
4. **`wrangler engines list`** still shows the right inventory with labels and dispositions intact — that is Task 3's `runtimes.list()` path, and label reading is the part most likely to have moved.
5. **One batch eval** against an existing engine, confirming `client.evals.run_inference` and the `_evals_common` patch still work through `agentplatform`. Check `cases_scored == cases_total`.

## Risks

- **The tools' `list()` shape.** `runtimes.list()` was verified to expose `labels` and `display_name` on `api_resource`, but `engines.py` builds dispositions from several signals. If any read goes through a wrapper attribute rather than `api_resource`, it fails quietly as "no engines match" rather than raising. Verification step 4 exists for this.
- **`agentplatform` has no independent version.** It ships inside `google-cloud-aiplatform`, so it cannot be pinned separately and moves whenever that package does. `tests/test_pipeline_image_pins.py` already pins aiplatform exactly for the container; nothing extra is needed, but the coupling is worth knowing.
- **Deferring the build package leaves two clients in the tree.** `deploy.py` will import `agentplatform` for the client and still emit `vertexai.agent_engines` into the generated template. That is intentional and must be commented at both template sites, or someone will "tidy" it into a campaign-killing change.
- **No removal date is published** for `vertexai.Client`. This is not urgent work; it is cheap now and unbounded later.

## Out of scope

- The generated build package's `AdkApp` import (deploy.py:286, 478) — deferred deliberately; revisit when ADK itself moves.
- `wrangler/optimize/optimizer.py:184`'s `google.adk.dependencies.vertexai` shim — that is ADK's own indirection, not ours.
- `client_kwargs={"vertexai": True}` in `core/models.py` and `examples/multi_model_agents/config.py` — an unrelated `google-genai` flag that merely shares the word.
- `examples/multi_model_agents/deploy_agents.py` and `run_demo.py` — example scripts; migrate only if they break.
