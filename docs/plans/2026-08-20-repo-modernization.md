# Repo Modernization & Model Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring GEPA Prompt Wrangler from "working but unguarded" to a standardized,
CI-enforced framework, and migrate off the Gemini 2.5 models that retire 2026-10-16.

**Architecture:** Four sequenced phases. Phase 0 installs the safety net (lockfile, CI,
ruff, ty, coverage) so every later change is verified. Phase 1 fixes correctness bugs
found during audit. Phase 2 collapses ~30 files' worth of hardcoded model IDs into a
single registry module. Phase 3 flips the default models onto that registry and deletes
the dead cloudpickle deploy path. Centralizing before migrating means the model swap is
a one-file change instead of a 30-file sweep.

**Tech Stack:** Python 3.11+, uv, ruff, ty, pytest, Google ADK 2.7.1, Vertex AI /
Agent Engine (GEAP), KFP v2.

**Standards:** All work follows [CODE_STANDARDS.md](../../CODE_STANDARDS.md).

---

# Part I — Status Report

Audit performed 2026-08-20 against commit `119f603`. Last commit was 2026-06-24, so the
repo sat idle ~2 months. Supporting measurements are in
[docs/notes/toolchain-baseline.md](../notes/toolchain-baseline.md).

## Strengths

**The test suite is genuinely good.** 356 tests, all passing, 44 seconds, no network and
no GCP calls. 4,407 test LOC against 9,450 source LOC. After two months idle it is still
green — that is the single best signal about this codebase, and it makes every change in
this plan verifiable.

**Dependencies are current, not rotted.** `google-adk` 2.7.1 is the latest PyPI release.
`google-cloud-aiplatform` 1.165.1, `google-genai` 2.19.0, `kfp` 2.17.0, `anthropic`
0.125.0 are all at or near current. The 16 packages `uv pip list --outdated` reports are
all transitive and pinned by the Google SDKs. **This repo does not have a dependency
upgrade problem.** It has a dependency *control* problem, which is different and covered
below.

**Architecture is clean and the domain knowledge is captured.** Six subpackages with a
real dependency order (`core/` at the base, nothing circular). CLAUDE.md is unusually
good — the `tool_use_quality` floor writeup and the source-based GEAP deployment
constraints are the kind of hard-won detail that is normally lost.

**Source-based GEAP deployment works.** The migration away from cloudpickle was the
right call and the constraints are documented.

## Weaknesses

**No CI. Nothing verifies anything.** There is no `.github/` directory. 356 passing
tests are only ever run when a human remembers. Four PRs merged to `main` with zero
automated gates.

**No lint or type configuration, ever.** Ruff has never run here: 423 findings against
its *default* rule set, 47 of 57 files unformatted. Ty has never run: 117 diagnostics.
Most are noise, but the noise is hiding real bugs — see Phase 1.

**`uv.lock` is gitignored.** The lockfile exists locally and is excluded from git. Every
clone, CI run, and Docker build re-resolves from loose floors. This is how fastmcp
2.x→3.x, pandas 2.x→3.x and pytest 8→9 were absorbed without a decision. It also means
the pipeline Docker cache key — `md5(pyproject.toml)[:12]` — does not change when
resolved dependencies change, so two images with the same tag can differ.

**Three answers to "what Python is this?"** `.venv` is 3.14.6, system is 3.12.3,
`requires-python` says `>=3.11`, and no `.python-version` pins anything.

**~2,300 LOC untested.** `pipeline/components.py` (902), `reporting/report_sections.py`
(798), `orchestration/runner.py` (408), plus `deploy_pipeline.py`, `charts.py`,
`dag.py`. `components.py` is the worst risk: KFP serializes each component in isolation,
so breakage only surfaces in a live pipeline run.

**Documentation drift.** CLAUDE.md claimed 316 tests (actual: 356 — now corrected).
CLAUDE.md says the cloudpickle deploy path is unused; it is still wired into
`wrangler deploy`. `.env.example` contradicts every other doc on `GOOGLE_CLOUD_LOCATION`.

**Committed project identifiers.** `hybrid-vertex` and project number `934903580331`
appear in 8 committed files. For a framework meant to be reused, that is a blocker.

## Needs Immediate Attention

### 1. Gemini 2.5 retires 2026-10-16 — ~8 weeks out 🔴

`gemini-2.5-flash` and `gemini-2.5-pro` are scheduled for retirement on Vertex AI on
**2026-10-16**. Retired Vertex model IDs return **404**. They appear in 30+ files.

Critically, `gemini-2.5-flash` is the **default judge model** — both here and inside ADK
2.7.1 itself. When it 404s, GEPA optimization and the multi-judge ensemble break, not
just agent inference. (Batch eval is the exception; it never sends a judge id.)
Successors: `gemini-3.6-flash`, `gemini-3.1-pro-preview`, `gemini-3.1-flash-lite`.
Details and caveats in [docs/notes/model-lifecycle.md](../notes/model-lifecycle.md).

**The judge is a separate decision from the agent model.** There is no ADK restriction on
judge model version, but swapping it silently re-scores every experiment and the judge
response parser does not filter thought parts. Evidence in
[docs/notes/adk-judge-model.md](../notes/adk-judge-model.md); the plan handles the agent
flip in Task 3.2 and the judge in a separate, measured Task 3.2b.

Note Google calls these "earliest possible" dates, and its own pages disagree (Oct 16 in
release notes vs Oct 20 on the lifecycle page). Plan against Oct 16.

### 2. ADK Patch 5 is now a regression 🔴

`_patch_adk()` overrides `RubricBasedEvaluator.convert_auto_rater_response_to_score`
with a copy derived from ADK 2.2. Upstream fixed
[#6072](https://github.com/google/adk-python/issues/6072) on 2026-07-31, and 2.7.1's
implementation now does **two things our override throws away**:

- **`rubric_id`-based matching** — upstream matches on rubric ID first and only falls
  back to text matching. Our override does text matching only, discarding the reliable path.
- **Empty-response guard** — upstream returns an empty verdict list with a warning; our
  override feeds a possibly-empty string into the parser.

Upstream's `_normalize_text` also now does exactly what our `_fuzzy_normalize` does
(NFKC, smart chars, whitespace, decoration stripping), making that half purely redundant.

**This is silently corrupting rubric scores right now**, which for a prompt-optimization
tool means GEPA may be optimizing against wrong signal. Full per-patch analysis and a
re-verification probe: [docs/notes/adk-patch-status.md](../notes/adk-patch-status.md).

### 3. Ten optimized prompts are silently unreachable 🔴

Every file in `examples/multi_model_agents/prompts/` defines the key `"wrangler_v5"`
**three times** in its `OPTIMIZED` dict. Python keeps the last. Across 5 files that is
**10 GEPA-optimized prompts that cannot be loaded**, and `wrangler --version wrangler_v5`
silently resolves to whichever happens to be last in the file.

For a prompt-optimization framework, losing optimized prompts and having ambiguous
version tags is a core-competency failure. Ruff's `F601` found it in seconds — which is
the argument for Phase 0 in one bullet.

### 4. `wrangler deploy` uses the deployment path CLAUDE.md calls broken 🟠

`cli.py:83` and `runner.py:363,382` call `deploy_agent()` / `update_agent()` — the
cloudpickle path CLAUDE.md documents as failing on GEAP. `wrangler run manifest.yaml`
works; `wrangler deploy manifest.yaml` takes the broken route with no warning.

### 5. `.env.example` produces a broken setup 🟠

It sets `GOOGLE_CLOUD_LOCATION=${GCP_REGION}` (`us-central1`). Everything else in the
repo says it must be `global` for Gemini 3.x and Claude. It is also missing the six MCP
server variables the multi-model agents require — the same ones whose absence makes
`examples/multi_model_agents/config.py` raise `KeyError` on import.

## Dependency Update Plan

The headline is that **there is little to upgrade and a lot to pin.** Direct deps are
current; the risk is uncontrolled resolution.

**Step 1 — Take control (Phase 0).** Un-gitignore and commit `uv.lock`. Raise the floors
in `pyproject.toml` to match reality and record why: `pandas>=2.0.0` while 3.0.5 is
installed and `pytest>=8.0.0` while 9.1.1 is installed are floors asserting compatibility
nobody has tested. Add `.python-version`. Change the Docker cache key to
`md5(pyproject.toml + uv.lock)` so the image tracks actual dependencies.

**Step 2 — Add the missing dev tooling (Phase 0).** `ruff`, `ty`, `pytest-cov`, `prek`
into `[dependency-groups]`. Not upgrades; these were never present.

**Step 3 — Do not chase transitives.** All 16 outdated packages (`mcp` 1.29→2.0,
`openai` 2.54→3.3, `protobuf` 6.33→7.35, `kubernetes` 30.1→36.0, opentelemetry
1.42→1.44) are constrained by the Google SDKs. Forcing them means fighting the
resolver. They move when `google-adk` and `google-cloud-aiplatform` move.

**Step 4 — Treat `google-adk` bumps as a ritual, not a bump.** ADK is the one dependency
that has repeatedly broken this repo, and five monkey-patches ride on its internals.
Every bump runs the probe in
[docs/notes/adk-patch-status.md](../notes/adk-patch-status.md) before anything else.
Two patches (1/2 and 3) are still needed *despite their upstream issues being closed* —
#6071's fix is not in the 2.7.1 release. Trust the probe, not the issue tracker.

**Step 5 — Ongoing.** Dependabot weekly, grouped, for direct deps only. `pip-audit` in
CI. Both land in Phase 0.

## Recommended Next Steps

Sequenced by the four phases below. Beyond this plan's scope, worth considering later:

- **`agentplatform` client migration.** ADK 2.7.1 emits a deprecation warning that
  `vertexai.preview.rag` is superseded by the `agentplatform` client. Not urgent, will be.
- **Deduplicate the two `config.py` files.** `wrangler/core/config.py` and
  `examples/multi_model_agents/config.py` have diverged from line 1. Phase 2's registry
  is the natural seam to fix this behind.
- **Coverage for `pipeline/components.py`.** Largest untested surface, highest blast radius.
- **`agents/example_agent` and templates** should be validated by CI so the BYOA path
  cannot silently rot.

---

# Part II — Implementation Plan

Phases are sequenced and should land in order. Phase 0 is a prerequisite for everything
else — it is what makes the rest verifiable.

## Working agreement

- Branch per phase: `chore/phase-0-toolchain`, `fix/phase-1-correctness`, etc.
- Commit after every task. Each commit leaves `uv run pytest tests/` green.
- **No `Co-Authored-By` trailers.** See [CODE_STANDARDS.md](../../CODE_STANDARDS.md) §1.
- Every command below is `uv run` / `uv add`. Never bare `pip` or `python`.

---

# Phase 0 — Toolchain Foundation

Nothing here changes runtime behavior. It installs the safety net.

### Task 0.1: Commit the lockfile

**Files:**
- Modify: `.gitignore`

**Step 1: Remove `uv.lock` from `.gitignore`**

Delete these two lines:

```
# uv
uv.lock
```

**Step 2: Verify the lockfile is current**

Run: `uv lock --check`
Expected: `Resolved N packages` with no error. If it reports the lockfile is outdated,
run `uv lock` and note the diff in the commit message.

**Step 3: Confirm git now sees it**

Run: `git status --short uv.lock`
Expected: `?? uv.lock`

**Step 4: Commit**

```bash
git add .gitignore uv.lock
git commit -m "chore: commit uv.lock for reproducible installs

The lockfile was gitignored, so every clone, CI run, and Docker build
re-resolved from loose floors. This is how fastmcp 2.x->3.x, pandas
2.x->3.x, and pytest 8->9 landed without a decision."
```

---

### Task 0.2: Pin the Python version

**Files:**
- Create: `.python-version`

**Step 1: Decide the pin**

`requires-python` is `>=3.11`; the local `.venv` is on 3.14.6 and the suite passes there.
Pin the floor for local development so nobody accidentally depends on newer syntax; CI
will test the range.

**Step 2: Create the file**

`.python-version`:
```
3.11
```

**Step 3: Re-sync and confirm the suite still passes on 3.11**

```bash
uv sync --all-groups
uv run python --version
uv run pytest tests/ -q
```
Expected: `Python 3.11.x`, then `356 passed`.

> **If tests fail on 3.11:** something already depends on 3.12+ syntax. Do not paper over
> it — find it, and either fix it or raise `requires-python` deliberately with a note in
> the commit message.

**Step 4: Commit**

```bash
git add .python-version
git commit -m "chore: pin local Python to 3.11 to match requires-python"
```

---

### Task 0.3: Add dev tooling dependencies

**Files:**
- Modify: `pyproject.toml` (via `uv add`, not by hand)

**Step 1: Add the tools**

```bash
uv add --group dev ruff ty pytest-cov
```

**Step 2: Verify they resolve and run**

```bash
uv run ruff --version
uv run ty --version
```
Expected: version strings for both.

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add ruff, ty, pytest-cov to dev dependency group"
```

---

### Task 0.4: Configure ruff, ty, and coverage

**Files:**
- Modify: `pyproject.toml`

**Step 1: Append configuration**

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["experiments", "outputs", "_geap_build_pkg"]

[tool.ruff.lint]
select = ["ALL"]
ignore = [
    "D",       # docstring style — not adopting pydocstyle wholesale
    "COM812",  # conflicts with the formatter
    "ISC001",  # conflicts with the formatter
    "ANN",     # annotations enforced by ty, not ruff
    "T201",    # print() is the CLI's output mechanism
    "TRY003",  # long messages in exceptions are fine here
    "EM",      # exception message string literals
    "FIX",     # TODO comments are tracked, not banned
    "TD",      # TODO formatting
]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101", "PLR2004", "SLF001"]        # asserts, magic values, private access
"wrangler/pipeline/components.py" = ["PLC0415"]  # KFP requires function-local imports
"scripts/*" = ["INP001"]                          # standalone scripts, not a package

[tool.ty.environment]
python-version = "3.11"

[tool.ty.rules]
possibly-unresolved-reference = "error"
unused-ignore-comment = "warn"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=wrangler --cov-report=term-missing"
```

Note: no `--cov-fail-under` yet. Task 0.7 sets it from the measured baseline.

**Step 2: Confirm the config parses**

```bash
uv run ruff check --statistics wrangler/ | tail -5
uv run ty check wrangler/ 2>&1 | tail -3
```
Expected: both run and report findings. A config error would say so instead.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: configure ruff (select=ALL), ty, and pytest coverage"
```

---

### Task 0.5: Format the codebase

Formatting-only. Separate commit so it never obscures a behavior change in review.

**Step 1: Snapshot test results before**

```bash
uv run pytest tests/ -q | tail -1
```
Expected: `356 passed`. Write the number down.

**Step 2: Format**

```bash
uv run ruff format .
```
Expected: `~47 files reformatted, ~10 files left unchanged`

**Step 3: Confirm behavior is unchanged**

```bash
uv run pytest tests/ -q | tail -1
```
Expected: `356 passed` — identical to Step 1.

> If the count changed, formatting exposed a real bug (almost always a test depending on
> source formatting). Stop and investigate before committing.

**Step 4: Commit**

```bash
git add -A
git commit -m "style: apply ruff format across the codebase

Formatting only, no behavior change. Test count unchanged at 356."
```

---

### Task 0.6: Apply safe lint autofixes

**Step 1: Apply autofixes**

```bash
uv run ruff check --fix .
```

**Step 2: Review the `F401` unused-import removals carefully**

```bash
git diff --stat
git diff wrangler/core/__init__.py wrangler/reporting/__init__.py
```

> **Watch out:** `__init__.py` files legitimately re-export names, and ruff reads those
> as unused imports. `wrangler/core/__init__.py` (8 lines) and
> `wrangler/reporting/__init__.py` (3 lines) are almost certainly re-export shims. If an
> import was removed from one, restore it and add it to `__all__` instead.

**Step 3: Run the suite**

```bash
uv run pytest tests/ -q | tail -1
```
Expected: `356 passed`

**Step 4: See what is left**

```bash
uv run ruff check --statistics .
```
Expected: a much smaller list, dominated by findings that need human judgment
(`BLE001` blind-except, `LOG015` root-logger, `DTZ005` naive datetimes,
`PLW1510` unchecked subprocess). **Do not bulk-fix these.** They are real and they are
Phase 1 / follow-up work.

**Step 5: Commit**

```bash
git add -A
git commit -m "style: apply ruff safe autofixes (imports, f-strings, placeholders)"
```

---

### Task 0.7: Establish the coverage floor

**Step 1: Measure**

```bash
uv run pytest tests/ --cov=wrangler --cov-report=term | tail -25
```
Record the total percentage.

**Step 2: Set the floor just below the measured value**

In `pyproject.toml`, extend `addopts` — substituting the real number, rounded **down** to
the nearest 5:

```toml
addopts = "--cov=wrangler --cov-report=term-missing --cov-fail-under=<MEASURED_ROUNDED_DOWN>"
```

The floor is a ratchet: it only ever goes up. See
[CODE_STANDARDS.md](../../CODE_STANDARDS.md) §5.

**Step 3: Verify it passes**

```bash
uv run pytest tests/ -q | tail -2
```
Expected: `356 passed`, coverage check passes.

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "test: set coverage floor from measured baseline"
```

---

### Task 0.8: Add CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --all-groups --frozen
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run ty check wrangler/

  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
          python-version: ${{ matrix.python-version }}
      - run: uv sync --all-groups --frozen
      - run: uv run pytest tests/ -v

  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uvx pip-audit --strict
```

`--frozen` makes CI fail if `uv.lock` is out of sync with `pyproject.toml`. That is the
whole point of Task 0.1.

**Step 2: Reproduce each CI step locally before pushing**

```bash
uv sync --all-groups --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check wrangler/
uv run pytest tests/ -v
```

> **Expect `ty check` to fail here** — the baseline is 117 diagnostics. Task 0.9 deals
> with that. Do not push a red CI; complete 0.9 first, then push 0.8 and 0.9 together.

**Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint, multi-version test, and dependency audit workflow"
```

---

### Task 0.9: Make `ty check` pass

117 diagnostics, most of them false positives from dynamically-proxied Google SDK
objects. Fix real ones; suppress false ones **at the call site with a reason**.

**Files:**
- Modify: `wrangler/tools/traffic.py:123,126`
- Modify: `wrangler/tools/inspector.py:109`
- Modify: `wrangler/optimize/optimizer.py:235-237`
- Modify: `wrangler/eval/online_monitors.py:53`
- Modify: others as the run reports

**Step 1: Fix the real ones first — implicit `Optional`**

These are genuine and ruff flags them too (`RUF013`). In
`wrangler/optimize/optimizer.py`:

```python
def optimize(
    agent_module_path: str,
    evalset_path: str | None = None,
    sampler_config_path: str | None = None,
    eval_data_path: str | None = None,
    agent_name: str = "",
```

Apply the same treatment at `wrangler/eval/online_monitors.py:53`.

**Step 2: Fix the real one in `inspector.py:109`**

`spec.loader` is genuinely `Loader | None`. Narrow it:

```python
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load agent module from {path}")
spec.loader.exec_module(module)
```

**Step 3: Suppress the dynamic-proxy false positives**

`AgentEngine` methods are generated from the `class_methods` list at runtime, so no
static type exists. In `wrangler/tools/traffic.py`:

```python
# AgentEngine proxies ADK class_methods at runtime; ty cannot see them.
session = agent.create_session(user_id=user_id)  # ty: ignore[unresolved-attribute]
```

**Step 4: Iterate to zero**

```bash
uv run ty check wrangler/
```
Repeat until it reports no diagnostics. For each remaining item decide: real bug → fix;
dynamic SDK surface → suppress with a reason comment. **Never loosen a rule globally to
clear a call site** ([CODE_STANDARDS.md](../../CODE_STANDARDS.md) §4).

**Step 5: Full local CI reproduction**

```bash
uv run ruff format --check . && uv run ruff check . && uv run ty check wrangler/ && uv run pytest tests/ -q
```
Expected: all four clean, `356 passed`.

**Step 6: Commit**

```bash
git add -A
git commit -m "fix: resolve ty diagnostics — real implicit-Optional bugs, suppress SDK dynamic proxies"
```

---

### Task 0.10: Add local hooks and a task runner

**Files:**
- Create: `.pre-commit-config.yaml`
- Create: `Makefile`

**Step 1: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets

  - repo: local
    hooks:
      - id: ty
        name: ty check
        entry: uv run ty check wrangler/
        language: system
        pass_filenames: false
```

**Step 2: Write the `Makefile`**

```makefile
.PHONY: dev lint format test check

dev:
	uv sync --all-groups

format:
	uv run ruff format .

lint:
	uv run ruff format --check . && uv run ruff check . && uv run ty check wrangler/

test:
	uv run pytest tests/ -v

check: lint test
```

**Step 3: Install and verify the hooks**

```bash
uvx prek install
uvx prek run --all-files
```
Expected: all hooks pass.

> **Verify the hook is actually installed** — `ls .git/hooks/pre-commit`. A config file
> with no installed hook is a very convincing no-op. Note the result in
> [docs/notes/repo-traps.md](../notes/repo-traps.md) if it misbehaves.

**Step 4: Commit**

```bash
git add .pre-commit-config.yaml Makefile
git commit -m "chore: add prek hooks and Makefile task runner"
```

---

### Task 0.11: Add Dependabot

**Files:**
- Create: `.github/dependabot.yml`

**Step 1: Write the config**

```yaml
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      google-sdks:
        patterns:
          - "google-*"
      dev-tools:
        patterns:
          - "ruff"
          - "ty"
          - "pytest*"
    open-pull-requests-limit: 5

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
```

Grouping the Google SDKs matters — `google-adk` and `google-cloud-aiplatform` move
together and must be reviewed together.

**Step 2: Commit**

```bash
git add .github/dependabot.yml
git commit -m "chore: add grouped Dependabot config for uv and actions"
```

---

### Task 0.12: Fix the Docker cache key

**Files:**
- Modify: `wrangler/pipeline/deploy_pipeline.py`

**Step 1: Find the tag computation**

```bash
grep -n "md5\|pyproject.toml" wrangler/pipeline/deploy_pipeline.py
```

**Step 2: Write a failing test**

In `tests/test_pipeline.py`:

```python
def test_image_tag_changes_when_lockfile_changes(tmp_path, monkeypatch):
    """The image tag must track uv.lock, not just pyproject.toml.

    Otherwise two builds with identical tags can contain different packages.
    """
    from wrangler.pipeline.deploy_pipeline import _compute_image_tag

    pyproject = tmp_path / "pyproject.toml"
    lock = tmp_path / "uv.lock"
    pyproject.write_text("[project]\nname='x'\n")
    lock.write_text("version = 1\n")

    tag_before = _compute_image_tag(pyproject, lock)
    lock.write_text("version = 2\n")
    tag_after = _compute_image_tag(pyproject, lock)

    assert tag_before != tag_after
```

**Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_pipeline.py::test_image_tag_changes_when_lockfile_changes -v`
Expected: FAIL — `_compute_image_tag` does not exist, or takes one argument.

**Step 4: Implement**

Extract the tag logic into a named function that hashes both files:

```python
def _compute_image_tag(pyproject_path: Path, lock_path: Path) -> str:
    """Image tag = md5 of pyproject.toml + uv.lock, truncated to 12 chars.

    Both inputs matter: pyproject alone does not change when resolved
    dependency versions change.
    """
    h = hashlib.md5()  # noqa: S324 — cache key, not a security primitive
    h.update(pyproject_path.read_bytes())
    if lock_path.exists():
        h.update(lock_path.read_bytes())
    return h.hexdigest()[:12]
```

Update the existing caller to use it.

**Step 5: Run the test**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS

**Step 6: Update the docs**

In `CLAUDE.md`, the Pipeline Architecture and Docker Image sections both say
`md5(pyproject.toml)[:12]`. Change both to `md5(pyproject.toml + uv.lock)[:12]`.

**Step 7: Commit**

```bash
git add wrangler/pipeline/deploy_pipeline.py tests/test_pipeline.py CLAUDE.md
git commit -m "fix: include uv.lock in Docker image cache key

pyproject.toml alone does not change when resolved dependency versions
change, so two images could share a tag with different packages."
```

---

**Phase 0 exit criteria:** `make check` passes clean. CI is green on a PR. `uv.lock`,
`.python-version`, ruff/ty config, hooks, and Dependabot are committed.

---

# Phase 1 — Correctness Fixes

Real bugs found during audit. Each gets a regression test.

### Task 1.1: Remove the harmful ADK Patch 5 override

The highest-value fix in this plan. Background:
[docs/notes/adk-patch-status.md](../notes/adk-patch-status.md).

**Files:**
- Modify: `wrangler/optimize/optimizer.py:101-178`
- Test: `tests/test_optimizer.py`

**Step 1: Confirm the upstream state on the installed ADK**

```bash
uv run python -c "
import inspect
from google.adk.evaluation import rubric_based_evaluator as rbe
up = inspect.getsource(rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score)
print('rubric_id matching upstream:', 'rubric_by_id' in up)
print('fuzzy normalize upstream:', 'NFKC' in inspect.getsource(rbe._normalize_text))
"
```
Expected: both `True`. If either is `False`, **stop** — the ADK version differs from what
this plan was written against, and the patch may still be needed.

**Step 2: Write the failing test**

In `tests/test_optimizer.py`:

```python
def test_patch_adk_preserves_upstream_rubric_id_matching():
    """Patch 5 must not clobber upstream's rubric_id-based matching.

    ADK 2.7.1 (issue #6072, fixed 2026-07-31) matches rubric verdicts by
    rubric_id first, falling back to normalized text. An override derived
    from ADK 2.2 does text-only matching and silently discards the more
    reliable path, corrupting the scores GEPA optimizes against.
    """
    import inspect

    from google.adk.evaluation import rubric_based_evaluator as rbe

    from wrangler.optimize.optimizer import _patch_adk

    _patch_adk()

    src = inspect.getsource(rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score)
    assert "rubric_by_id" in src, (
        "convert_auto_rater_response_to_score was replaced by an override that "
        "lacks rubric_id matching"
    )
```

**Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_optimizer.py::test_patch_adk_preserves_upstream_rubric_id_matching -v`
Expected: FAIL — the override is installed, so `rubric_by_id` is absent.

**Step 4: Delete the override**

In `wrangler/optimize/optimizer.py`, delete the entire Patch 5 block — from the
`# Patch 5: Fuzzy rubric text matching` comment (line ~101) through
`_rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score = _patched_convert`
(line ~176). That removes `_SMART_CHARS`, `_fuzzy_normalize`, `_orig_convert`, and
`_patched_convert`, plus the now-unused `import re as _re` and `import unicodedata as _ud`.

Update the trailing log line:

```python
log.info("ADK patches applied (patches 1-4; patch 5 removed — upstream #6072 fixed in ADK 2.7.1)")
```

**Step 5: Run the test**

Run: `uv run pytest tests/test_optimizer.py -v`
Expected: PASS

> **Note on the substring fallback:** the deleted override had one behavior upstream
> lacks — a unique-candidate substring match. If rubric-match warnings reappear in GEPA
> logs after this change, re-add *only* that fallback on top of the current upstream
> implementation. Do not restore the whole method.

**Step 6: Document the remaining patches**

Add a version-verification comment above `_patch_adk`:

```python
def _patch_adk():
    """Apply ADK patches for GEPA compatibility.

    Verified against google-adk 2.7.1 on 2026-08-20.

    Patch 1/2 — eval_case/eval_set extra="forbid" (issue #5906). Issue is
        CLOSED but extra="forbid" is still present on 8 classes at 2.7.1.
        Still required.
    Patch 3 — LocalEvalService null guard (issue #6071). Issue CLOSED
        2026-08-06 but the fix is NOT in the 2.7.1 release. Still required;
        re-check at 2.8.x.
    Patch 4 — LocalEvalSampler score coercion + logging. Local
        instrumentation, not an upstream workaround.
    Patch 5 — REMOVED. Upstream fixed #6072 in 2.7.1 and went further
        (rubric_id matching). Keeping it was a regression.

    Re-run the probe in docs/notes/adk-patch-status.md on every ADK bump.
    """
```

**Step 7: Full suite**

Run: `uv run pytest tests/ -q`
Expected: `357 passed`

**Step 8: Commit**

```bash
git add wrangler/optimize/optimizer.py tests/test_optimizer.py
git commit -m "fix: remove ADK patch 5 — it clobbers upstream rubric_id matching

ADK 2.7.1 fixed issue #6072 and added rubric_id-based verdict matching plus
an empty-response guard. Our override, derived from ADK 2.2, did text-only
matching and discarded both, silently corrupting the rubric scores GEPA
optimizes against."
```

---

### Task 1.2: Recover the 10 unreachable optimized prompts

**Files:**
- Modify: `examples/multi_model_agents/prompts/flash_prompts.py:240,281`
- Modify: `examples/multi_model_agents/prompts/lite_prompts.py:179,196`
- Modify: `examples/multi_model_agents/prompts/opus_prompts.py:202,233`
- Modify: `examples/multi_model_agents/prompts/pro_prompts.py:224,270`
- Modify: `examples/multi_model_agents/prompts/sonnet_prompts.py:163,183`
- Test: `tests/test_prompt_registry.py`

Each file defines `"wrangler_v5"` three times in `OPTIMIZED`. Python keeps the last, so
two optimized prompts per file are unreachable.

**Step 1: Write the failing test**

In `tests/test_prompt_registry.py`:

```python
import ast
from pathlib import Path

import pytest

PROMPT_FILES = sorted(
    Path("examples/multi_model_agents/prompts").glob("*_prompts.py")
)


@pytest.mark.parametrize("path", PROMPT_FILES, ids=lambda p: p.name)
def test_no_duplicate_prompt_version_keys(path):
    """Duplicate keys in OPTIMIZED silently discard optimized prompts.

    Python keeps only the last value for a repeated dict key, so a repeated
    version tag means earlier GEPA outputs are unreachable and the version
    tag is ambiguous.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "OPTIMIZED" for t in node.targets
        ):
            continue
        keys = [
            k.value
            for k in node.value.keys
            if isinstance(k, ast.Constant)
        ]
        duplicates = {k for k in keys if keys.count(k) > 1}
        assert not duplicates, f"{path.name}: duplicate version keys {duplicates}"
```

**Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_prompt_registry.py::test_no_duplicate_prompt_version_keys -v`
Expected: FAIL for all 5 files, each reporting `{'wrangler_v5'}`.

**Step 3: Renumber the duplicates**

For each file, inspect the three `wrangler_v5` blocks and order them by their
`"timestamp"` field (each entry carries one). Rename so that:

- the **earliest** keeps `"wrangler_v5"`
- the next becomes `"wrangler_v6"`
- the latest becomes `"wrangler_v7"`

```bash
grep -n '"wrangler_v5"\|"timestamp"' examples/multi_model_agents/prompts/sonnet_prompts.py
```

> **Preserve every prompt body verbatim.** These are GEPA outputs that cost real
> optimization budget to produce. This task recovers them; it must not edit them.

**Step 4: Run the test**

Run: `uv run pytest tests/test_prompt_registry.py -v`
Expected: PASS

**Step 5: Check for references to the renamed tags**

```bash
grep -rn "wrangler_v5" --include=*.py --include=*.yaml --include=*.json . | grep -v '.venv\|outputs/\|experiments/'
```

Anything pointing at `wrangler_v5` was resolving to the *last* block, which is now
`wrangler_v7`. Update those references and say so in the commit message.

**Step 6: Confirm ruff agrees**

```bash
uv run ruff check --select F601 .
```
Expected: `All checks passed!`

**Step 7: Commit**

```bash
git add examples/multi_model_agents/prompts/ tests/test_prompt_registry.py
git commit -m "fix: recover 10 unreachable optimized prompts from duplicate version keys

Each prompts file defined wrangler_v5 three times; Python kept only the
last, making two GEPA-optimized prompts per model unloadable and the
version tag ambiguous. Renumbered by timestamp to v5/v6/v7. Prompt bodies
unchanged."
```

---

### Task 1.3: Fix `.env.example`

**Files:**
- Modify: `.env.example`

**Step 1: Correct the location and add the missing MCP variables**

```bash
# --- GCP Configuration ---
GCP_PROJECT_ID=your-project-id
PROJECT_NUMBER=9999999999
GCP_REGION=us-central1
GCP_STAGING_BUCKET=your-project-id-wrangler-staging

# --- Vertex AI ---
GOOGLE_CLOUD_PROJECT=${GCP_PROJECT_ID}
# MUST be "global" — Gemini 3.x and all Claude models use the global endpoint.
# Only Gemini 2.x uses regional endpoints, and those retire 2026-10-16.
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_VERTEXAI=1

# --- MCP servers (required for examples/multi_model_agents) ---
# Agent Registry resource names
SEARCH_MCP_SERVER=wrangler-search-mcp
BOOKING_MCP_SERVER=wrangler-booking-mcp
EXPENSE_MCP_SERVER=wrangler-expense-mcp
# Direct Cloud Run URLs used by deployed agents
SEARCH_MCP_URL=https://wrangler-search-mcp-xxxxx.us-central1.run.app/mcp
BOOKING_MCP_URL=https://wrangler-booking-mcp-xxxxx.us-central1.run.app/mcp
EXPENSE_MCP_URL=https://wrangler-expense-mcp-xxxxx.us-central1.run.app/mcp

# --- BigQuery log sink ---
DATASET_NAME=gepa_wrangler_logs
SINK_NAME=gepa-agent-traces
```

**Step 2: Verify every key is actually read somewhere**

```bash
for k in GCP_PROJECT_ID PROJECT_NUMBER GCP_REGION GCP_STAGING_BUCKET \
         GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_LOCATION GOOGLE_GENAI_USE_VERTEXAI \
         SEARCH_MCP_SERVER BOOKING_MCP_SERVER EXPENSE_MCP_SERVER \
         SEARCH_MCP_URL BOOKING_MCP_URL EXPENSE_MCP_URL \
         DATASET_NAME SINK_NAME; do
  n=$(grep -rl "$k" --include=*.py --include=*.sh . 2>/dev/null | grep -vc '.venv')
  echo "$k: $n files"
done
```
Expected: every key `>= 1`. A key nobody reads should be deleted, not documented.

**Step 3: Commit**

```bash
git add .env.example
git commit -m "fix: .env.example set GOOGLE_CLOUD_LOCATION=global and add MCP vars

It set the location to the regional value, which breaks Gemini 3.x and all
Claude models, and omitted the six MCP variables the multi-model agents
require."
```

---

### Task 1.4: Make `examples/multi_model_agents/config.py` importable without env

**Files:**
- Modify: `examples/multi_model_agents/config.py`
- Test: `tests/test_config.py`

Bare `os.environ["SEARCH_MCP_SERVER"]` subscripts make the module raise `KeyError` on
import when unset. `build_source_package()` already rewrites these for deployment — the
source should not need rewriting.

**Step 1: Write the failing test**

```python
def test_examples_config_imports_without_mcp_env(monkeypatch):
    """The examples config must import cleanly with no MCP env set.

    build_source_package() rewrites these subscripts to .get() for
    deployment; the source itself should not need that rewrite.
    """
    import importlib
    import sys

    for var in ("SEARCH_MCP_SERVER", "BOOKING_MCP_SERVER", "EXPENSE_MCP_SERVER"):
        monkeypatch.delenv(var, raising=False)

    sys.path.insert(0, "examples/multi_model_agents")
    sys.modules.pop("config", None)
    try:
        importlib.import_module("config")  # must not raise
    finally:
        sys.path.pop(0)
        sys.modules.pop("config", None)
```

**Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_config.py::test_examples_config_imports_without_mcp_env -v`
Expected: FAIL with `KeyError: 'SEARCH_MCP_SERVER'`

**Step 3: Fix the three subscripts**

```python
SEARCH_MCP_SERVER = os.environ.get("SEARCH_MCP_SERVER", "")
BOOKING_MCP_SERVER = os.environ.get("BOOKING_MCP_SERVER", "")
EXPENSE_MCP_SERVER = os.environ.get("EXPENSE_MCP_SERVER", "")
```

**Step 4: Run the test**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Simplify the build-time rewrite**

`build_source_package()` in `wrangler/core/deploy.py` rewrites these subscripts. With the
source fixed, the rewrite is a no-op — but leave it in place as a safety net and add a
comment noting the source no longer requires it. CLAUDE.md's "Critical constraints" item
6 should be updated to say the same.

**Step 6: Commit**

```bash
git add examples/multi_model_agents/config.py tests/test_config.py wrangler/core/deploy.py CLAUDE.md
git commit -m "fix: examples config must import without MCP env vars set

Bare os.environ[...] subscripts made the module raise KeyError on import,
so local runs and tests failed where deployment worked."
```

---

### Task 1.5: Remove committed project identifiers

**Files:**
- Modify: `examples/multi_model_agents/config.py`
- Modify: `examples/multi_model_agents/scripts/*.sh` (5 files)
- Modify: `examples/multi_model_agents/README.md`, `CLAUDE.md`

**Step 1: Find every occurrence**

```bash
grep -rn "hybrid-vertex\|934903580331" --include=*.py --include=*.yaml --include=*.md --include=*.sh . | grep -v '.venv\|outputs/\|experiments/'
```
Expected: 11 occurrences across 8 files.

**Step 2: Write the failing guard test**

In `tests/test_config.py`:

```python
def test_no_hardcoded_project_identifiers():
    """Committed code must not contain a real GCP project id or number.

    This repo is meant to be reusable; a hardcoded project silently points
    a new user's runs at someone else's infrastructure.
    """
    import subprocess

    banned = ("hybrid-vertex", "934903580331")
    tracked = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh", "*.yaml", "*.yml"],
        capture_output=True, text=True, check=True,
    ).stdout.split()

    offenders = [
        f for f in tracked
        if any(b in Path(f).read_text(errors="ignore") for b in banned)
    ]
    assert not offenders, f"hardcoded project identifiers in: {offenders}"
```

Note this guards code and config, not markdown — docs may reference a project by name in
prose.

**Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_config.py::test_no_hardcoded_project_identifiers -v`
Expected: FAIL listing `config.py` and the shell scripts.

**Step 4: Replace the defaults**

In `examples/multi_model_agents/config.py`:

```python
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
```

In each shell script, replace the literal with a required-variable pattern:

```bash
PROJECT_ID="${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set — see .env.example}"
PROJECT_NUMBER="${PROJECT_NUMBER:?PROJECT_NUMBER must be set — see .env.example}"
```

**Step 5: Run the test and shellcheck the scripts**

```bash
uv run pytest tests/test_config.py -v
uvx --from shellcheck-py shellcheck examples/multi_model_agents/scripts/*.sh
```
Expected: test PASS. Fix any shellcheck errors introduced.

**Step 6: Update the docs**

In CLAUDE.md, the IAM grant snippet hardcodes the service account and project. Change to
placeholders:

```bash
SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
for SVC in wrangler-search-mcp wrangler-booking-mcp wrangler-expense-mcp; do
  gcloud run services add-iam-policy-binding "$SVC" \
    --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
    --member="serviceAccount:$SA" --role="roles/run.invoker" --quiet
  gcloud run services update "$SVC" \
    --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
    --session-affinity --quiet
done
```

**Step 7: Commit**

```bash
git add -A
git commit -m "chore: remove hardcoded project id and number from committed code

Adds a guard test so they cannot come back."
```

---

**Phase 1 exit criteria:** `make check` clean, all new regression tests passing, no
`F601` findings, no committed project identifiers.

---

# Phase 2 — Model Registry

Collapse ~30 files of hardcoded model IDs into one module. This is the prerequisite that
makes Phase 3 a one-file change. Behavior must not change in this phase.

### Task 2.1: Create the registry module

**Files:**
- Create: `wrangler/core/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

```python
"""Tests for the central model registry."""
import pytest

from wrangler.core.models import (
    MODELS,
    ModelSpec,
    blended_cost,
    get_batch_config,
    resolve_model,
)


def test_every_model_has_complete_metadata():
    """A model without cost, rate limit, or retirement date is a landmine."""
    for name, spec in MODELS.items():
        assert spec.input_cost > 0, f"{name}: missing input cost"
        assert spec.output_cost > 0, f"{name}: missing output cost"
        assert spec.rpm > 0, f"{name}: missing rate limit"
        assert spec.family in ("gemini", "claude"), f"{name}: unknown family"


def test_retired_models_are_flagged():
    """Models past their retirement date must be marked, not silently served."""
    import datetime as dt

    today = dt.date(2026, 8, 20)
    for name, spec in MODELS.items():
        if spec.retirement_date and spec.retirement_date <= today:
            assert spec.retired, f"{name} is past retirement but not flagged"


def test_gemini_2x_resolves_to_plain_string():
    """Gemini 2.x uses regional endpoints and is passed through as a string."""
    assert resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_unknown_model_raises():
    """An unregistered model id must fail loudly, not silently cost money."""
    with pytest.raises(KeyError, match="not-a-model"):
        blended_cost("not-a-model")
```

**Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: wrangler.core.models`

**Step 3: Implement the registry**

```python
"""Central model registry — the single source of truth for model metadata.

Every model id used anywhere in this repo must be registered here with its
cost, rate limit, and retirement date. Nothing else should hardcode a model
string. See CODE_STANDARDS.md section 8.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

BLENDED_INPUT_WEIGHT = 4
BLENDED_OUTPUT_WEIGHT = 1


@dataclass(frozen=True)
class ModelSpec:
    """Everything the framework needs to know about a model.

    Costs are USD per 1M tokens. `rpm` drives inference throttling.
    `retirement_date` is the earliest announced shutdown; see
    docs/notes/model-lifecycle.md.
    """

    name: str
    family: str  # "gemini" | "claude"
    input_cost: float
    output_cost: float
    rpm: int
    retirement_date: dt.date | None = None
    retired: bool = False
    notes: str = ""


MODELS: dict[str, ModelSpec] = {
    # --- Gemini 2.x — RETIRING 2026-10-16, regional endpoints ---
    "gemini-2.5-flash": ModelSpec(
        "gemini-2.5-flash", "gemini", 0.15, 0.60, 100,
        retirement_date=dt.date(2026, 10, 16),
        notes="Successor: gemini-3.6-flash",
    ),
    "gemini-2.5-pro": ModelSpec(
        "gemini-2.5-pro", "gemini", 1.25, 10.0, 80,
        retirement_date=dt.date(2026, 10, 16),
        notes="Successor: gemini-3.1-pro-preview",
    ),
    # --- Gemini 3.x — global endpoint ---
    "gemini-3.1-flash-lite": ModelSpec(
        "gemini-3.1-flash-lite", "gemini", 0.25, 1.5, 5,
    ),
    "gemini-3.5-flash": ModelSpec(
        "gemini-3.5-flash", "gemini", 1.50, 9.0, 5,
    ),
    "gemini-3.1-pro-preview": ModelSpec(
        "gemini-3.1-pro-preview", "gemini", 4.0, 18.0, 5,
        notes="Preview id — expect churn; may be repointed after GA",
    ),
    # --- Claude — global/multi-region endpoints ---
    "claude-sonnet-4-6": ModelSpec("claude-sonnet-4-6", "claude", 3.0, 15.0, 2000),
    "claude-opus-4-6": ModelSpec(
        "claude-opus-4-6", "claude", 5.0, 25.0, 800,
        retirement_date=dt.date(2027, 2, 5),
    ),
    "claude-opus-4-7": ModelSpec("claude-opus-4-7", "claude", 5.0, 25.0, 800),
    "claude-opus-4-8": ModelSpec("claude-opus-4-8", "claude", 5.0, 25.0, 800),
    "claude-fable-5": ModelSpec("claude-fable-5", "claude", 10.0, 50.0, 800),
}


def get_spec(model: str) -> ModelSpec:
    """Return the spec for `model`, raising KeyError if unregistered."""
    if model not in MODELS:
        raise KeyError(
            f"Model {model!r} is not registered in wrangler.core.models.MODELS. "
            f"Add it with cost, rate limit, and retirement date."
        )
    return MODELS[model]


def blended_cost(model: str, custom_costs: dict[str, float] | None = None) -> float:
    """Estimated cost per 1M tokens assuming a 4:1 input:output token ratio."""
    if custom_costs is not None:
        inp, out = custom_costs["input"], custom_costs["output"]
    else:
        spec = get_spec(model)
        inp, out = spec.input_cost, spec.output_cost
    weight = BLENDED_INPUT_WEIGHT + BLENDED_OUTPUT_WEIGHT
    return (BLENDED_INPUT_WEIGHT * inp + BLENDED_OUTPUT_WEIGHT * out) / weight


def get_batch_config(model: str) -> tuple[int, float, int]:
    """Return (batch_size, delay_seconds, max_workers) based on the model's RPM."""
    rpm = MODELS[model].rpm if model in MODELS else 90
    if rpm <= 10:
        return 4, 15.0, 4
    if rpm <= 100:
        return 16, 5.0, 10
    return 64, 0.0, 20


def resolve_model(model_str: str):
    """Resolve a model string to an ADK-compatible model object.

    Gemini 2.x works on regional endpoints — passed through as a plain string.
    Gemini 3.x uses the native Gemini class; Claude uses the native Claude
    class. Both read GOOGLE_CLOUD_LOCATION from the environment, which must
    be "global".
    """
    if model_str.startswith(("gemini-2", "models/")):
        return model_str
    if model_str.startswith("claude"):
        from google.adk.models.anthropic_llm import Claude

        return Claude(model=model_str)
    from google.adk.models.google_llm import Gemini

    return Gemini(model=model_str)
```

> **Behavior note:** `get_batch_config` previously matched by *substring* over
> `RATE_LIMITS`. The registry matches exactly. Substring matching meant
> `"gemini-2.5-flash-lite"` could pick up `"gemini-2.5-flash"`'s limit. Exact matching
> with an explicit 90 RPM default is the intended behavior; the old lookup order was
> dict-insertion-dependent and therefore fragile.

**Step 4: Run the tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add wrangler/core/models.py tests/test_models.py
git commit -m "feat: add central model registry with cost, rate limit, retirement date"
```

---

### Task 2.2: Point `core/config.py` at the registry

**Files:**
- Modify: `wrangler/core/config.py:21-95`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_config_reexports_registry_not_its_own_tables():
    """config.py must delegate to the registry, not keep a parallel table."""
    import wrangler.core.config as cfg
    from wrangler.core.models import MODELS

    assert set(cfg.MODEL_COSTS) == set(MODELS), (
        "MODEL_COSTS has drifted from the registry"
    )
```

**Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_config.py::test_config_reexports_registry_not_its_own_tables -v`
Expected: FAIL or PASS-by-accident. If it passes, temporarily add a model to the registry
to confirm the test can actually detect drift, then remove it.

**Step 3: Replace the tables with derived views**

In `wrangler/core/config.py`, delete the `MODEL_COSTS` and `RATE_LIMITS` literals and the
local `blended_cost`, `get_batch_config`, `resolve_model` definitions. Replace with:

```python
from .models import (
    MODELS,
    blended_cost,
    get_batch_config,
    resolve_model,
)

# Back-compat views over the registry. Prefer importing from .models directly.
MODEL_COSTS = {
    name: {"input": spec.input_cost, "output": spec.output_cost}
    for name, spec in MODELS.items()
}
RATE_LIMITS = {name: spec.rpm for name, spec in MODELS.items()}

__all__ = [
    "MODEL_COSTS",
    "RATE_LIMITS",
    "blended_cost",
    "get_batch_config",
    "resolve_model",
    # ... existing exports
]
```

**Step 4: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: all pass. Existing importers of `config.MODEL_COSTS` / `config.resolve_model`
keep working unchanged.

**Step 5: Commit**

```bash
git add wrangler/core/config.py tests/test_config.py
git commit -m "refactor: derive config model tables from the registry"
```

---

### Task 2.3: Add a guard against new hardcoded model IDs

**Files:**
- Test: `tests/test_models.py`

**Step 1: Write the guard test**

```python
def test_no_unregistered_model_ids_in_wrangler():
    """Every model id appearing in wrangler/ must be in the registry.

    Without this, model ids creep back into individual modules and the next
    retirement becomes another 30-file sweep.
    """
    import re
    from pathlib import Path

    from wrangler.core.models import MODELS

    pattern = re.compile(r"[\"'](gemini-[\w.\-]+|claude-[\w.\-]+)[\"']")
    allowed = set(MODELS) | {"models/"}
    offenders = []

    for path in Path("wrangler").rglob("*.py"):
        if path.name == "models.py":
            continue
        for match in pattern.finditer(path.read_text()):
            if match.group(1) not in allowed:
                offenders.append(f"{path}: {match.group(1)}")

    assert not offenders, (
        "Unregistered model ids found — add them to wrangler/core/models.py:\n"
        + "\n".join(offenders)
    )
```

**Step 2: Run it**

Run: `uv run pytest tests/test_models.py::test_no_unregistered_model_ids_in_wrangler -v`
Expected: FAIL initially, listing hardcoded ids in `evaluator.py`, `multi_judge.py`,
`converter.py`, `stages.py`, `dag.py`, `deploy_pipeline.py`, `inspector.py`,
`prompt_registry.py`.

**Step 3: Replace each hardcoded default with a registry constant**

Add named defaults to `wrangler/core/models.py`:

```python
# Named roles — change these to change the framework's defaults.
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"
DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
DEFAULT_ANALYSIS_MODEL = "gemini-2.5-pro"
```

Then in each offending module, replace the literal:

```python
from ..core.models import DEFAULT_JUDGE_MODEL

def some_fn(judge_model: str = DEFAULT_JUDGE_MODEL) -> ...:
```

Work module by module, running `uv run pytest tests/ -q` after each. **Do not change the
values** — Phase 2 must be behavior-preserving.

**Step 4: Verify the guard passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Full suite**

Run: `uv run pytest tests/ -q`
Expected: all pass, no behavior change.

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: route all wrangler model ids through the registry

Adds a guard test so new hardcoded ids fail CI. Values unchanged — this is
behavior-preserving groundwork for the Gemini 2.5 migration."
```

---

**Phase 2 exit criteria:** `wrangler/` contains no model id outside
`wrangler/core/models.py`; the guard test enforces it; behavior unchanged.

---

# Phase 3 — Model Migration & Legacy Removal

### Task 3.1: Register the Gemini 3.x successors and Claude 5 family

**Files:**
- Modify: `wrangler/core/models.py`
- Test: `tests/test_models.py`

**Step 1: Confirm current pricing before writing numbers down**

Do not copy the cost figures below on faith — verify each against the
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing) at
implementation time and correct them. Wrong costs silently corrupt every cost chart the
reporter produces.

**Step 2: Add the entries**

```python
    # --- Gemini 3.x successors ---
    "gemini-3.6-flash": ModelSpec(
        "gemini-3.6-flash", "gemini", 0.0, 0.0, 5,  # VERIFY COSTS
        notes="Successor to gemini-2.5-flash",
    ),
    # --- Claude 5 family ---
    "claude-opus-5": ModelSpec(
        "claude-opus-5", "claude", 0.0, 0.0, 800,  # VERIFY COSTS
        notes="Deprecates temperature/top_p/top_k; thinking on by default",
    ),
    "claude-sonnet-5": ModelSpec(
        "claude-sonnet-5", "claude", 0.0, 0.0, 2000,  # VERIFY COSTS
        retirement_date=dt.date(2026, 12, 24),
    ),
```

**Step 3: Handle the Opus 5 sampling-parameter deprecation**

`wrangler/core/factory.py:19,94` sets `temperature` unconditionally (default `1.0`).
Opus 5 rejects `temperature`, `top_p`, and `top_k`.

Add to `ModelSpec`:

```python
    supports_sampling_params: bool = True
```

Set `supports_sampling_params=False` on `claude-opus-5`, then make the factory
model-aware.

**Step 4: Write the failing test**

```python
def test_sampling_params_omitted_for_models_that_reject_them():
    """Opus 5 rejects temperature/top_p/top_k — sending them is an API error."""
    from wrangler.core.models import get_spec

    assert not get_spec("claude-opus-5").supports_sampling_params
```

Add a matching test in `tests/test_factory.py` asserting the built agent config for
`claude-opus-5` carries no `temperature` key.

**Step 5: Run, implement, re-run**

Run: `uv run pytest tests/test_models.py tests/test_factory.py -v`
Expected: FAIL, then PASS after the factory change.

**Step 6: Commit**

```bash
git add wrangler/core/models.py wrangler/core/factory.py tests/
git commit -m "feat: register Gemini 3.6 Flash and the Claude 5 family

Opus 5 deprecates temperature/top_p/top_k, so ModelSpec now carries
supports_sampling_params and the factory omits them accordingly."
```

---

### Task 3.2: Flip the agent default; validate the judge separately

**The judge model is not just another model id.** Verified against ADK 2.7.1 on
2026-08-20 — see [docs/notes/adk-judge-model.md](../notes/adk-judge-model.md) for the
full evidence. Summary:

- There is **no ADK constraint** forcing a 2.5 judge. `JudgeModelOptions.judge_model` is
  a plain `str` with no validator, and `LLMRegistry` resolves `gemini-.*` to the `Gemini`
  class for every version. Google's own docs use `gemini-flash-latest` in every example.
- **But** ADK 2.7.1 still *defaults* every judge and optimizer to `gemini-2.5-flash`, and
  `llm_as_judge_utils.py:88` concatenates all text parts with **no `part.thought`
  filter** — so a thinking-heavy judge can leak reasoning prose into the strict
  Property/Rationale/Verdict parser.

So: migrate the agent default freely, but treat the judge as a change that must be
**measured, not assumed**.

**Files:**
- Modify: `wrangler/core/models.py` (the `DEFAULT_*` constants)

**Step 1: Change the agent and analysis defaults only**

```python
DEFAULT_AGENT_MODEL = "gemini-3.6-flash"           # was gemini-2.5-flash
DEFAULT_ANALYSIS_MODEL = "gemini-3.1-pro-preview"  # was gemini-2.5-pro

# Judge stays on 2.5 until Task 3.2b proves a successor scores equivalently.
# This MUST change before 2026-10-16 — see docs/notes/adk-judge-model.md.
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"
```

This is the payoff from Phase 2: a two-line change instead of a 30-file sweep.

**Step 2: Add a failing deadline guard**

In `tests/test_models.py`:

```python
def test_judge_model_is_not_retired():
    """The judge default must not be a model past its retirement date.

    gemini-2.5-flash is the ADK default and retires 2026-10-16. When it 404s,
    GEPA optimization AND batch eval both stop working, not just inference.
    """
    import datetime as dt

    from wrangler.core.models import DEFAULT_JUDGE_MODEL, get_spec

    spec = get_spec(DEFAULT_JUDGE_MODEL)
    if spec.retirement_date:
        assert spec.retirement_date > dt.date.today(), (
            f"{DEFAULT_JUDGE_MODEL} retired on {spec.retirement_date}. "
            f"Complete Task 3.2b."
        )
```

This turns the deadline into a build failure rather than a surprise outage.

**Step 3: Run the suite**

Run: `uv run pytest tests/ -q`
Expected: pass. Tests asserting a literal `"gemini-2.5-flash"` should be updated to
reference the `DEFAULT_*` constants, so the next migration does not touch tests at all.

**Step 4: Commit**

```bash
git add wrangler/core/models.py tests/
git commit -m "feat: default agent and analysis models to Gemini 3.x

Judge deliberately stays on gemini-2.5-flash until Task 3.2b measures a
successor. Adds a guard test that fails once the judge default is past its
retirement date."
```

---

### Task 3.2b: Choose the judge model by measurement

Do not skip this and do not guess. A judge change silently re-scores everything.

**Step 1: Pick candidates**

- `gemini-flash-latest` — ADK's documented pattern. Rides forward automatically, so it
  never hits a retirement cliff. Cost: scores can shift under you without a code change,
  which is bad for reproducible experiment comparisons.
- `gemini-3.6-flash` — pinned successor to 2.5-flash. Reproducible, but needs a manual
  bump at each retirement.
- `gemini-3.1-flash-lite` — cheapest, but registered at **5 RPM** vs 2.5-flash's 100.
  Through `get_batch_config` that shifts batching from `(16, 5.0s, 10)` to
  `(4, 15.0s, 4)` — roughly **4x slower** judging. Verify your real project quota before
  taking this; the 5 came from the existing table, not from a measurement.

**Recommendation:** pin `gemini-3.6-flash`. This repo's whole purpose is comparing prompt
variants across runs, and a self-updating judge undermines that.

**Step 2: Run the same evalset under the old and new judge**

Change only the judge model between the two runs — same agent, same prompt, same evalset.

```bash
uv run wrangler eval manifests/pipeline_smoke_manifest.yaml before --agent-name judge-2p5
# edit the judge model in the sampler config, then:
uv run wrangler eval manifests/pipeline_smoke_manifest.yaml before --agent-name judge-3p6
```

**Step 3: Check for the thought-leakage failure mode**

This is the specific risk from `llm_as_judge_utils.py:88`. In the run logs, look for:

- `RUBRIC MATCH FAILURE` warnings (emitted by ADK Patch 4's instrumentation)
- `Rubric ... not found in the rubrics provided to the metric` from ADK
- rubric scores that are `None` or suspiciously uniform

If any appear, set `judge_model_config` explicitly in the sampler config rather than
inheriting defaults tuned for 2.5:

```json
"judge_model_options": {
  "judge_model": "gemini-3.6-flash",
  "num_samples": 5,
  "judge_model_config": {
    "temperature": 0.0,
    "thinking_config": {"include_thoughts": false}
  }
}
```

`temperature: 0.0` also reduces verdict variance, which is worth having regardless.

**Step 4: Compare the score distributions**

Per-metric mean and spread across the evalset. Expect *some* drift — that is normal and
not a failure. What disqualifies a candidate is: rubric match failures, `None` scores, or
drift large enough to flip pass/fail against your configured thresholds.

**Step 5: Adopt or reject**

If the candidate is clean, set `DEFAULT_JUDGE_MODEL` to it and remove the Task 3.2 Step 1
comment. If not, try the next candidate. **Do not adopt a judge that produces rubric
match failures** — that is precisely the silent-signal-corruption problem Task 1.1 fixed.

**Step 6: Record the re-baseline**

Old and new scores are not comparable. Write the new baseline, the judge model, and the
date into [docs/notes/model-lifecycle.md](../notes/model-lifecycle.md).

**Step 7: Commit**

```bash
git add wrangler/core/models.py examples/ tests/
git commit -m "feat: migrate judge model to <chosen> after A/B validation

Verified no rubric match failures and score drift within threshold margins
against the smoke evalset. Baseline recorded in docs/notes/model-lifecycle.md."
```

---

### Task 3.3: Migrate manifests and sampler configs

**Files:**
- Modify: `manifests/*.yaml` (4 files)
- Modify: `examples/multi_model_agents/agents/*/sampler_config.json` (6 files)
- Modify: `templates/*/manifest.yaml` (2 files)

**Step 1: Find every occurrence**

```bash
grep -rn "gemini-2.5" manifests/ templates/ examples/multi_model_agents/agents/*/sampler_config.json
```

**Step 2: Update the judge models to whatever Task 3.2b validated**

Do this only after Task 3.2b picked a judge. Do not guess a value here.

Per CLAUDE.md, `sampler_config.json` files are the **single source of truth** for GEPA
criteria and are used verbatim. Change only the judge model field; leave criteria,
thresholds, and `max_metric_calls` alone.

Note each sampler config pins `judge_model` **twice** (once per metric block, roughly
lines 8 and 30) — 12 values across the 6 files. A one-per-file sweep will miss half of
them. Verify with:

```bash
grep -c '"judge_model"' examples/multi_model_agents/agents/*/sampler_config.json
```
Expected: `2` for each file, and zero `gemini-2.5` matches afterwards.

**Step 3: Bump every `cache_bust` value**

Pipeline components cache on input parameter values. A model change with an unchanged
`cache_bust` will silently reuse results computed with the old model.

```bash
grep -rn "cache_bust" manifests/ examples/
```
Increment each one.

**Step 4: Validate the configs still parse**

Run: `uv run pytest tests/test_eval_data.py tests/test_converter.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add manifests/ templates/ examples/multi_model_agents/agents/
git commit -m "feat: migrate manifests and sampler configs to Gemini 3.x judges

Bumps cache_bust everywhere so the pipeline does not serve results computed
with the retired models."
```

---

### Task 3.4: Smoke-test the migration end to end

**Step 1: Run the smoke manifest**

```bash
uv run wrangler run manifests/pipeline_smoke_manifest.yaml
```
5 eval cases, ~25-30 minutes.

**Step 2: Check for the failure modes this migration introduces**

- No `404` on any model id
- No `RUBRIC MATCH FAILURE` warnings (Patch 5 removal, Task 1.1)
- `tool_use_quality_v1` near 1.00, not floored at ~0.42 (CLAUDE.md's known artifact)
- Wall-clock not catastrophically worse from the judge RPM drop (Task 3.2b Step 1)

**Step 3: Record the re-baselined scores**

Judge-model changes move every score. Old and new reports are **not comparable**. Write
the new baseline into `docs/notes/model-lifecycle.md` under a "post-migration baseline"
heading, with the date and the manifest used.

**Step 4: Commit any fixes, then tag the milestone**

```bash
git commit -m "fix: <whatever the smoke test surfaced>"
```

---

### Task 3.5: Delete the cloudpickle deploy path

**Files:**
- Modify: `wrangler/cli.py:74-84`
- Modify: `wrangler/orchestration/runner.py:363,382`
- Modify: `wrangler/core/deploy.py:21-32,390-490`
- Test: `tests/test_deploy.py`

**Step 1: Write the failing test**

```python
def test_cli_deploy_uses_source_based_path(monkeypatch):
    """`wrangler deploy manifest.yaml` must not use the cloudpickle path.

    The pickle path fails on GEAP because cloudpickle captures module
    references that do not exist server-side. It was still wired into the
    manifest branch of the deploy command.
    """
    called = {}

    import wrangler.core.deploy as deployer

    monkeypatch.setattr(
        deployer, "deploy_agent_from_source",
        lambda *a, **k: called.setdefault("source", True) or "engine-123",
    )

    def _fail(*a, **k):
        raise AssertionError("cloudpickle deploy_agent() must not be called")

    monkeypatch.setattr(deployer, "deploy_agent", _fail, raising=False)
    # ... invoke the CLI deploy command against a fixture manifest
    assert called.get("source")
```

**Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_deploy.py::test_cli_deploy_uses_source_based_path -v`
Expected: FAIL with `cloudpickle deploy_agent() must not be called`

**Step 3: Migrate the three call sites**

Replace `deployer.deploy_agent(agent, display_name=...)` with
`deployer.deploy_agent_from_source(...)` at `cli.py:83` and `runner.py:363`, and
`update_agent` with `update_agent_from_source` at `runner.py:382`. Match the argument
shapes the source-based functions expect — check their signatures first:

```bash
grep -n "def deploy_agent_from_source\|def update_agent_from_source" -A 12 wrangler/core/deploy.py
```

**Step 4: Delete the dead code**

Remove from `wrangler/core/deploy.py`:
- the `REQUIREMENTS` list (lines ~23-32) — legacy-only, superseded by `_SOURCE_REQUIREMENTS`
- `deploy_agent()` (~line 390)
- `update_agent()` (~line 439)
- any now-unused `cloudpickle` import

**Step 5: Update the tests that exercised the deleted functions**

`tests/test_deploy.py` has at least four tests calling `deploy_agent` directly (lines
21, 35, 52, 69). Rewrite them against `deploy_agent_from_source`. **Do not just delete
them** — deployment is the least-recoverable operation in this repo and needs the
coverage.

**Step 6: Run everything**

```bash
uv run pytest tests/ -q
uv run ruff check .
```
Expected: all pass.

**Step 7: Update the docs**

In CLAUDE.md, delete the "Legacy functions" section under Source-Based GEAP Deployment
and the "deploy.py Requirements Lists" mention of `REQUIREMENTS`. Update
[docs/notes/repo-traps.md](../notes/repo-traps.md) to strike the resolved trap.

**Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete the cloudpickle deploy path

cli.py and runner.py still called deploy_agent()/update_agent() — the path
CLAUDE.md documents as broken on GEAP — so `wrangler deploy manifest.yaml`
took the failing route while `wrangler run` worked. All call sites now use
the source-based functions."
```

---

### Task 3.6: Refresh the documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`
- Modify: `docs/notes/*.md`

**Step 1: Reconcile CLAUDE.md with reality**

- Test count (Task 0.12 and 1.1 both changed it) — re-run and use the real number
- ADK patches: five → four, with the version-verification note
- Model resolution section: point at `wrangler/core/models.py`
- Docker cache key: `md5(pyproject.toml + uv.lock)[:12]`
- Legacy deploy functions: section removed
- Add a "Model Registry" section pointing at the registry as the single source of truth

**Step 2: Update README.md**

Check the model tables, the `GOOGLE_CLOUD_LOCATION` guidance at line 613, and any
`gemini-2.5` references in examples.

**Step 3: Update the notes**

Mark resolved items in [repo-traps.md](../notes/repo-traps.md), update
[adk-patch-status.md](../notes/adk-patch-status.md) to reflect four patches, and add the
post-migration baseline to [model-lifecycle.md](../notes/model-lifecycle.md).

**Step 4: Verify every command in the docs actually runs**

```bash
uv run wrangler --help
uv run wrangler pipeline --help
make check
```

**Step 5: Commit**

```bash
git add -A
git commit -m "docs: reconcile CLAUDE.md, README, and notes with the migration"
```

---

**Phase 3 exit criteria:** no `gemini-2.5` outside the registry's retirement entries;
smoke test green; cloudpickle path gone; docs accurate.

---

# Follow-Up (not scheduled here)

Deliberately out of scope, in rough priority order:

1. **Tests for `pipeline/components.py`** (902 LOC, untested, highest blast radius).
2. **Deduplicate the two `config.py` files** — the registry is the seam that makes this
   tractable.
3. **Triage the remaining ruff findings** — `BLE001` (24 blind excepts), `LOG015` (29
   root-logger calls), `DTZ005` (20 naive datetimes), `PLW1510` (8 unchecked
   subprocesses). All real, none urgent.
4. **`agentplatform` client migration** — ADK 2.7.1 deprecates `vertexai.preview.rag`.
5. **CI coverage for `agents/example_agent` and `templates/`** so the BYOA path cannot
   rot silently.
6. **Validate `wrangler inspect` output** — it emits literal `"TODO"` goldens that will
   happily be scored against.
