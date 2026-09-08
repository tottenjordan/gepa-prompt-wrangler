# GEPA Prompt Wrangler — Agent Context & Guidelines

Operating rules for an agent working in this repo. Architecture and domain
detail live in [CLAUDE.md](CLAUDE.md), imported below — **this file is the rule
set, not a second copy of the architecture.**

@CLAUDE.md

---

## 🚨 Critical Standards Reference

**Always read [CODE_STANDARDS.md](CODE_STANDARDS.md) before writing code or
changing the environment.** It is authoritative for tooling, commits,
dependencies and secrets. CLAUDE.md covers *what the system is*;
CODE_STANDARDS.md covers *how we write and ship it*; this file covers *what will
bite you*.

Session notes and known traps: [docs/notes/README.md](docs/notes/README.md).

## 🧪 A Green Test Suite Is Not Evidence

**This is the most expensive mistake available in this repo. Read it twice.**

The suite is deliberately hermetic — no network, no GCP — so it **mocks the
Vertex SDK**. On 2026-09-08 it stayed green (1100+ passing) through *three*
separate failures that broke live behaviour completely:

- a monkey-patch applied to the wrong package's module, silently disabling
  `EVAL_MAX_RETRIES`
- `SessionInput` built from the wrong package, making the eval service reject
  **every** case (0/3 scored)
- a dependency set that could not resolve at all, killing a campaign arm

**Never report a deployment, eval, or dependency change as working on the
strength of the suite alone.** Required evidence, in order of cost:

```bash
uv run wrangler preflight        # ~1s — resolves both dependency sets
uv run python scripts/deploy_probe_arms.py --arm mcp-claude --campaign <id>
uv run python -m wrangler.tools.boot_probe --arm mcp-claude=<engine-id> --n 12 --spacing 3
```

**Always reap the probe engine afterwards.** See 🗑️ below.

Two more silent failures worth knowing, because neither raises anything:

```bash
uv run wrangler evaluators trace-health   # dropped OTel span batches
uv run wrangler evaluators --help         # 6 more online-eval commands
```

- **Dropped span batches.** Online eval scores traces; when OTel drops batches
  under load, scoring silently sees fewer cases. `trace-health` exits non-zero
  when any engine is dropping, so it can gate a run rather than merely inform
  one. These seven commands were unreachable from `wrangler --help` until
  2026-09-08 — if a diagnostic seems to be missing, check that it is not just
  undiscoverable.
- **A stage reporting SUCCEEDED is not a stage that worked.** Grep an optimize
  run for `will run without the tools` *and* `Failed to get tools from toolset`
  — ADK reworded it at 2.8.0, and matching only the old string reported zero
  tool losses while five had occurred, feeding a corrupted objective into GEPA.

## 🛠️ Environment & Tooling Rules

- **Package management: `uv` only.** Never invoke bare `pip`, `python`, or
  `pytest`. Never activate a virtualenv — `uv run` handles it. Add dependencies
  with `uv add`, never by hand-editing `pyproject.toml`.
- **Lint and format: `ruff` only.** Never black, flake8, isort, pyupgrade or
  pydocstyle. Never disable a rule repo-wide to silence one call site.
- **Type checking: `ty` only.** Never mypy or pyright. Use `str | None`,
  `list[str]` — never `Optional`, `List`, `Dict`.
- **Git and PRs.** Branch, then open a PR. **NEVER commit directly to `main`,
  and NEVER merge until the user has explicitly approved it.** Never add
  `Co-Authored-By` trailers or "Generated with" lines to commits or PR bodies.
- **Before every commit:** `uv run pytest tests/ -q`, `uv run ruff format`,
  `uv run ruff check`, `uv run ty check wrangler/`.

## 📌 Dependency Pin Rules

- **A `>=` floor in anything a container installs is PROHIBITED** and is a test
  failure (`tests/test_pipeline_image_pins.py`). That means every Dockerfile
  *and* KFP `packages_to_install`, which is a pip install at pipeline runtime —
  `archive_agent_code` runs on stock `python:3.11` and resolves fresh on every
  run. A lockfile bump moves the image tag, the rebuild resolves floors fresh,
  and the container silently diverges from what CI tested. This is exactly how
  the optimize container ended up on an unverified ADK while running five
  monkey-patches against it.
- **Never read a version out of `uv.lock` without checking its marker.**
  `uv.lock` holds *two* entries for some packages either side of
  `python>=3.14`. Reading the wrong side set `litellm>=1.96.2` and killed a
  campaign arm. Use `importlib.metadata.version()` — the installed truth.
- **`litellm` stays capped `<1.86`.** Above it needs `jinja2>=3.1.6` while
  `google-adk[eval]` resolves 3.1.5 on the GEAP builder → `ResolutionImpossible`,
  surfaced only as `Build failed ... or other dependencies`.
- **Never bump `fastmcp` past 3.4.7.** ADK pins `mcp>=1.24,<2` on the extra that
  provides `McpToolset`; fastmcp 4.x moves to the mcp 2.x protocol and the
  servers become unreachable *while still reporting healthy*.
- **Python is 3.11 — `wrangler.tools.preflight.TARGET_PYTHON` is the one source
  of truth.** Import it; never redefine it. The three Cloud Run MCP images are
  the sole exception at 3.14, recorded with evidence in
  `tests/test_pipeline_image_pins.py:PYTHON_MISMATCH_ACCEPTED`. Adding an entry
  there means you resolved that image's pins on its own base — not that you
  noticed the mismatch and waved it through. `Dockerfile.pipeline` may never be
  exempted; it runs the ADK monkey-patches.

## 🤖 Vertex SDK Rules

- **Use `wrangler.core.clients.agent_client()`.** Constructing
  `vertexai.Client` is PROHIBITED — deprecated at aiplatform 2.1.0, and an
  AST guard in `tests/test_clients.py` fails on either spelling.
- **Agent Engine CRUD is `client.runtimes`, never `client.agent_engines`.**
  `get`/`delete` are keyword-only (`name=...`).
- **NEVER mix `vertexai` and `agentplatform` objects across a call boundary.**
  It type-checks fine and fails at runtime, and the mocked suite cannot see it.
  If the client is agentplatform's, then `types`, `_evals_common` and
  `_gcs_utils` must be too.
- `agentplatform` ships **vendored inside** `google-cloud-aiplatform`. Never
  test for it with `importlib.metadata.version()` — that raises while the
  import succeeds.

## 🔬 ADK Monkey-Patch Rules

`optimize/optimizer.py:_patch_adk()` applies five patches to ADK internals.

- **Never add or remove a patch without re-running the per-patch probe** in
  [docs/notes/adk-patch-status.md](docs/notes/adk-patch-status.md).
- **Re-probe when the Vertex SDK moves, not only ADK.** Patch 6 reads
  `METRIC_LATEST_SPEC_NAME` out of the SDK, so an aiplatform major can
  invalidate a patch while ADK sits still.
- **A redundant patch is not harmless.** Patch 5 was deleted after it silently
  overwrote newer upstream behaviour and corrupted the scores GEPA optimizes
  against.

## 🗑️ Engine Lifecycle Rules

- **Reap the engines you deploy.** This project reached 80 unnoticed, 61 holding
  warm instances. Deploy scratch engines with
  `labels={"lifecycle": "ephemeral", "campaign": "<id>"}` and delete them as the
  last step of the task, not as a later chore.
- **Never sweep by age or name.** Only 48 of those 80 were ours, and three
  *unlabelled* ones were the busiest in the project. `wrangler engines prune` is
  dry-run by default and deletes only what every signal agrees on.
- **Never pin an engine id in source.** Ids arrive at the call site; a missing
  one must fail clearly, never fall back to a checked-in default.

## 📊 Campaign & Measurement Rules

- **Every sweep carries a control arm** whose prompt does not change, run under
  identical conditions. Whatever it produces **is the noise floor**, and nothing
  may be reported as an improvement unless it exceeds it. A byte-identical
  prompt once produced +0.039 and +0.035, which would have read as a clean win.
- **Never reuse a floor measured on an earlier run.** Dropout varies with load
  and with how many arms run at once.
- **Report coverage beside every score.** A delta across a coverage gap measures
  dropout, not the prompt.
- **Never launch a full campaign unvalidated.** `scripts/validate_then_run.py`
  submits one arm and releases the rest only on SUCCEEDED.

## 📝 Project Notes & Knowledge

- Durable findings that outlive a session go in `docs/notes/<topic>.md`, one
  topic per file — not in commit messages, where nobody will find them.
- Add a one-line row to the index in
  [docs/notes/README.md](docs/notes/README.md); keep that index under 200 lines.
- Pre-registered experiments go in `docs/doe/`, with the hypothesis, statistic
  and decision rule written down **before** the data exists.
- **Never let a doc drift from the code.** A guide here accumulated 14
  references to a deleted module path, so every documented command failed. If
  you move a module, grep the docs.
