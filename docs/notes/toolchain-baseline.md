# Toolchain Baseline

**Measured on:** 2026-08-20, before any modernization work. These are the "before"
numbers — use them to judge whether later changes actually improved anything.

Related: [../../CODE_STANDARDS.md](../../CODE_STANDARDS.md), [repo-traps.md](repo-traps.md).

---

## Test suite — healthy

```
uv run pytest tests/ -q  →  356 passed, 2 warnings in 44.20s
```

The suite is green, fast, and hermetic (no network, no GCP). This is the repo's
strongest asset after two months idle. 4,407 test LOC against 9,450 source LOC.

CLAUDE.md said "316 tests" — stale by 40. Updated to 356.

Warnings: a `_UnionGenericAlias` deprecation from `google.genai` (removal in Python
3.17) and a `BaseAgentConfig` deprecation from ADK. Neither is actionable by us.

## Lint — `ruff` was never run here

Not installed, no config, no `ruff.toml`, nothing in `pyproject.toml`. Against ruff's
**default** rule set (which is small — `E`/`F` only, plus what the statistics run
surfaced):

```
uvx ruff check wrangler/ tests/ scripts/ examples/  →  423 errors (259 autofixable)
uvx ruff format --diff wrangler/ tests/             →  47 of 57 files would be reformatted
```

Breakdown of the 423:

| Count | Rule | Note |
|-------|------|------|
| 116 | `I001` unsorted-imports | autofix |
| 97 | `F401` unused-import | autofix, but check `__init__.py` re-exports first |
| 58 | `F541` f-string-missing-placeholders | autofix |
| 29 | `LOG015` root-logger-call | real: logging through the root logger |
| 24 | `BLE001` blind-except | real: bare `except Exception` swallowing |
| 20 | `DTZ005` datetime-now-without-tz | real: naive timestamps in reports |
| 10 | `F601` multi-value-repeated-key-literal | **real bug smell** — duplicate dict keys |
| 9 | `RUF059` unused-unpacked-variable | |
| 8 | `F841` unused-variable | |
| 8 | `PLW1510` subprocess-run-without-check | real: silent subprocess failures |
| 8 | `RUF013` implicit-optional | real: `x: str = None` |
| 6 | `S110` try-except-pass | real: silent swallow |
| — | 15 more rules at ≤5 each | |

**The interesting one is `F601` (10 hits) — duplicate keys in dict literals means a
value is being silently discarded.** Triage that before mass-autofixing, because
autofix noise will bury it.

Note this is the *default* rule set. `select = ["ALL"]` per CODE_STANDARDS will surface
substantially more.

## Type checking — `ty` was never run here

```
uvx ty check wrangler/  →  117 diagnostics
```

| Count | Rule |
|-------|------|
| 79 | `invalid-argument-type` |
| 16 | `unresolved-attribute` |
| 7 | `invalid-assignment` |
| 4 | `unresolved-import` |
| 4 | `no-matching-overload` |
| 4 | `invalid-parameter-default` |
| 3 | `unsupported-operator` |

Expect a high false-positive rate. Confirmed examples:

- `AgentEngine.create_session` / `.stream_query` flagged as missing
  (`wrangler/tools/traffic.py:123,126`) — these are **dynamically proxied** via the
  SDK's `class_methods` list, so no static type exists. False positive.
- `spec.loader.exec_module` on `Loader | None` (`wrangler/tools/inspector.py:109`) —
  technically correct, needs a narrowing assert.

The 4 `invalid-parameter-default` hits likely overlap with ruff's 8 `RUF013`
implicit-optional findings (`x: str = None`) — those are real and worth fixing first.

## What does not exist

| Missing | Consequence |
|---------|-------------|
| `.github/` — **no CI at all** | Nothing verifies a PR. The 356 green tests are only ever run by hand. |
| `[tool.ruff]` / `[tool.ty]` config | No shared definition of "correct". |
| `ruff` / `ty` in `[dependency-groups]` | Only `pytest` and `pytest-asyncio` are dev deps. |
| Coverage config / `pytest-cov` | `.coverage` is gitignored but nothing generates it. |
| `prek` / pre-commit hooks | No local gate. |
| `.python-version` | Nothing pins the interpreter. |
| `Makefile` / task runner | Commands live only in CLAUDE.md prose. |
| Dependabot / `pip-audit` | No dependency vulnerability signal. |

## Environment drift

- **`.venv` runs Python 3.14.6**, the system interpreter is 3.12.3, and
  `requires-python` is `>=3.11`. Nothing pins this. Three different answers to "what
  Python is this?"
- **`pyproject.toml` floors are far below what is installed.** `pandas>=2.0.0` with
  **3.0.5** installed; `pytest>=8.0.0` with **9.1.1**. Both are major-version gaps. The
  floors assert compatibility that has never been tested.
- `uv.lock` is **gitignored** (see [repo-traps.md](repo-traps.md)) so nothing else
  constrains resolution either.

## Dependency currency — actually good

Direct dependencies are at or near latest as of 2026-08-20:

| Package | Installed |
|---------|-----------|
| `google-adk` | 2.7.1 (latest on PyPI) |
| `google-cloud-aiplatform` | 1.165.1 |
| `google-genai` | 2.19.0 |
| `anthropic` | 0.125.0 |
| `kfp` | 2.17.0 |
| `fastmcp` | 3.4.7 |
| `litellm` | 1.96.2 |
| `pandas` | 3.0.5 |
| `pytest` | 9.1.1 |

`uv pip list --outdated` shows 16 stale packages, all **transitive** and all pinned by
constraints from the Google SDKs (`mcp` 1.29→2.0, `openai` 2.54→3.3, `protobuf`
6.33→7.35, `kubernetes` 30.1→36.0, opentelemetry 1.42→1.44). Not independently
upgradable — they move when the Google SDKs move.

**The real dependency risk is not staleness, it is that major versions were absorbed
silently** (fastmcp 2.x→3.x, pandas 2.x→3.x, pytest 8→9) because the floors are loose
and the lockfile is not committed.

## Untested modules

Six modules have no corresponding `tests/test_<module>.py`, totalling ~2,300 LOC:

| Module | LOC |
|--------|-----|
| `wrangler/pipeline/components.py` | 902 |
| `wrangler/reporting/report_sections.py` | 798 |
| `wrangler/orchestration/runner.py` | 408 |
| `wrangler/pipeline/deploy_pipeline.py` | 320 |
| `wrangler/reporting/charts.py` | 248 |
| `wrangler/pipeline/dag.py` | 182 |

`tests/test_pipeline.py` (158 LOC) covers some pipeline surface, so
`components.py`/`dag.py` are not wholly untested — but `components.py` at 902 LOC is the
single largest gap, and KFP component isolation makes it the easiest place to break
something silently.
