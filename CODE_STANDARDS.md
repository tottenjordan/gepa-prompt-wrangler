# Code Standards

Standards for all code and environment changes in this repository. Read this before
writing code, adding dependencies, or changing the toolchain.

Related: [CLAUDE.md](CLAUDE.md) for architecture and domain conventions,
[docs/notes/README.md](docs/notes/README.md) for session notes and known traps.

---

## 1. Git & Commits

- **Never add `Co-Authored-By` trailers** to commits or PR bodies. No exceptions.
- Conventional-commit prefixes, matching existing history: `feat:`, `fix:`, `chore:`,
  `refactor:`, `test:`, `docs:`.
- Commit frequently, one logical change per commit. A commit should leave the test
  suite green.
- Work on a branch; `main` is merged via PR.
- Commit or push only when explicitly asked.

## 2. Package Management — `uv` only

**Never invoke bare `pip`, `python`, or `pytest`.** Everything goes through `uv`.

| Task | Command |
|------|---------|
| Install everything | `uv sync --all-groups` |
| Add a runtime dep | `uv add <pkg>` |
| Add a dev/test dep | `uv add --group dev <pkg>` |
| Remove a dep | `uv remove <pkg>` |
| Run anything | `uv run <cmd>` |
| One-off tool, not a dep | `uvx <tool>` or `uv run --with <pkg> <cmd>` |

Rules:

- **Do not hand-edit `[project.dependencies]`** — use `uv add` / `uv remove` so the
  lockfile stays in sync.
- **Never activate a virtualenv.** `uv run` handles it.
- Dev tooling lives in `[dependency-groups]` (PEP 735), never in
  `[project.optional-dependencies]`.
- **`uv.lock` is committed.** It is the reproducibility contract for CI, the pipeline
  Docker image, and GEAP deploys.
- Dependency **floors in `pyproject.toml` must reflect what the code actually
  requires**, not the version that happened to be installed years ago. When a floor is
  raised, raise it deliberately and say why in the commit message.

## 3. Lint & Format — `ruff` only

**Never use black, flake8, isort, pyupgrade, or pydocstyle.** `ruff` replaces all of them.

```bash
uv run ruff format .          # format
uv run ruff format --check .  # verify formatting (CI)
uv run ruff check .           # lint
uv run ruff check --fix .     # lint + autofix
```

- Configuration lives in `[tool.ruff]` in `pyproject.toml`. There is no `ruff.toml`.
- Never disable a rule repo-wide to silence one call site. Use a targeted
  `# noqa: RULE` with a reason comment, or fix the code.
- Formatting is not negotiable and not reviewed — let the formatter decide.

## 4. Type Checking — `ty`

**Never use mypy or pyright.**

```bash
uv run ty check wrangler/
```

- Config lives under `[tool.ty.environment]` and `[tool.ty.rules]` — **not** a bare
  `[tool.ty]` table.
- Add type hints to all new and modified function signatures. Use modern syntax:
  `str | None`, `list[str]`, `dict[str, float]` — never `Optional`, `List`, `Dict`.
- Google Cloud SDK objects (`AgentEngine`, ADK internals) are dynamically proxied and
  will produce false positives. Suppress those at the call site with a comment
  explaining why, rather than loosening a rule globally.

## 5. Testing — `pytest`

```bash
uv run pytest tests/ -v                 # full suite
uv run pytest tests/test_config.py -v   # one file
uv run pytest --cov=wrangler            # with coverage
```

- Tests live in `tests/`, named `test_<module>.py` mirroring `wrangler/<module>.py`.
- **Write the failing test first.** Run it, watch it fail for the expected reason, then
  implement. A test that has never failed has not been verified.
- No network, no GCP calls, no real model invocations in the suite. Mock at the SDK
  boundary; shared fixtures go in `tests/conftest.py`.
- Every bug fix gets a regression test that fails on the old code.
- **Never lower the coverage floor to make a change land.** Add tests instead.

## 6. Python Version

- `requires-python = ">=3.11"`.
- `.python-version` pins the local interpreter; CI tests the full supported range.
- Do not use syntax or stdlib APIs newer than the declared floor.

## 7. Third-Party Monkey-Patches

The GEPA optimizer patches ADK internals (`wrangler/optimize/optimizer.py:_patch_adk`).
This is load-bearing and fragile:

- Every patch must carry a comment naming **the upstream issue** and **the ADK version
  it was verified against**.
- **Re-verify every patch on any ADK version bump.** Upstream fixes make patches
  redundant, and redundant patches can silently overwrite newer upstream behavior.
  See [docs/notes/adk-patch-status.md](docs/notes/adk-patch-status.md).
- A patch that overrides a whole method must be re-derived from the current upstream
  source, not carried forward blindly.

## 8. Model IDs & GCP Resources

- **No hardcoded model IDs in logic.** Model names, cost tables, and rate limits belong
  in one registry module, not scattered across modules, manifests, and sampler configs.
- **No hardcoded project IDs, project numbers, or bucket names** in committed code.
  Read them from the environment with a documented key in `.env.example`.
- Every model added to the registry needs its cost, rate limit, and retirement date
  recorded. See [docs/notes/model-lifecycle.md](docs/notes/model-lifecycle.md).
- All GCP resources carry the label `{"solution": "promp-wrangler"}`.

## 9. Secrets

- `.env` is gitignored and stays that way. `.env.example` documents every key with
  placeholder values only.
- Never commit a real project ID, service-account address, key, or endpoint.
- Secret Manager is the source of truth for pipeline runs.

## 10. Documentation

- Update [CLAUDE.md](CLAUDE.md) when architecture, conventions, or hard-won constraints
  change.
- Session findings that outlive the conversation go in `docs/notes/` — one topic per
  file, linked from [docs/notes/README.md](docs/notes/README.md).
- Do not document a file, flag, or command without verifying it currently exists.

---

## Pre-Commit Checklist

```bash
uv run ruff format .
uv run ruff check --fix .
uv run ty check wrangler/
uv run pytest tests/
```

All four must pass before committing. Hooks (`prek`) enforce this locally; CI enforces
it on every PR.

## Explicit Non-Goals

- Migrating to a `src/` layout — the flat `wrangler/` package is settled.
- Replacing `hatchling` with `uv_build` — not worth churning the build config.
- Chasing 100% coverage. Cover behavior and regressions, not lines.
