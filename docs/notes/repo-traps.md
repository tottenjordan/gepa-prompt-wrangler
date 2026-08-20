# Repo Traps

**Verified on:** 2026-08-20.

Non-obvious footguns — things that look fine, or look documented, but bite. Each one
cost real investigation time to find.

Related: [toolchain-baseline.md](toolchain-baseline.md),
[model-lifecycle.md](model-lifecycle.md), [adk-patch-status.md](adk-patch-status.md).

---

## `uv.lock` is gitignored

`.gitignore` line: `uv.lock`. `git ls-files uv.lock` returns nothing.

The 918 KB lockfile in the working tree is **local only**. Consequences:

- No reproducible install anywhere — CI, the pipeline Docker image, a fresh clone, or a
  teammate all re-resolve from the loose floors in `pyproject.toml`.
- This is how fastmcp 2.x→3.x, pandas 2.x→3.x, and pytest 8→9 arrived without anyone
  deciding to take them.
- The Docker image tag is `md5(pyproject.toml)[:12]`, so **the image cache key does not
  change when resolved dependency versions change.** Two builds with the same tag can
  contain different packages.

Committing the lockfile also fixes that last one implicitly — but the cache key should
be `md5(pyproject.toml + uv.lock)` to be correct.

## `wrangler deploy` uses the deployment path CLAUDE.md calls broken

CLAUDE.md says the cloudpickle path "failed because cloudpickle captures module
references … that don't exist on the GEAP server", and that `deploy_agent()` /
`update_agent()` "remain for backward compatibility only" and are "not used by the
pipeline or local workflow."

That last part is wrong. Live callers:

- `wrangler/cli.py:83` — the `wrangler deploy` CLI command, manifest branch
- `wrangler/orchestration/runner.py:363` — `deploy_agent()`
- `wrangler/orchestration/runner.py:382` — `update_agent()`

Only the *experiment-directory* branch of `wrangler deploy` (`cli.py:73`, via
`stage_deploy`) reaches the working source-based path. So `wrangler deploy manifest.yaml`
takes the broken route while `wrangler run manifest.yaml` works. The CLI gives no hint.

## Two `config.py` files that have drifted

CLAUDE.md flags that `wrangler/core/config.py` and `examples/multi_model_agents/config.py`
both define `resolve_model()` and must be kept in sync. What it does not say is **how far
apart they already are** — they diverge from line 1. The examples copy additionally:

- defaults the staging bucket to `-geap-staging`, not `-wrangler-staging` — **still
  true**, and the reason a run can write artifacts to a bucket you did not expect
- ~~hardcodes `GCP_PROJECT_ID` default to `"hybrid-vertex"`~~ — **fixed 2026-08-20.**
  The default is now `""`, and `tests/test_config.py::test_no_hardcoded_project_identifiers`
  fails the build if a real project id or number reappears in any tracked `.py`, `.sh`,
  `.yaml` or `.yml`. Markdown is deliberately exempt, so prose can still name a project
  when describing a real run.
- ~~reads `os.environ["SEARCH_MCP_SERVER"]` with bare subscript access~~ — **fixed
  2026-08-20.** These are `.get(..., "")` at the source now. The `build_source_package()`
  rewrite stays as a safety net for third-party agent configs.

The trap that generalizes: the deploy-time rewrite in `build_source_package()` made the
subscript bug invisible, because the only path anyone exercised was deployment. A bug
that a build step silently papers over is one nobody reports.

## `.env.example` contradicted CLAUDE.md on `GOOGLE_CLOUD_LOCATION` (fixed 2026-08-20)

**Fixed** — `.env.example` now sets `global` and carries the six MCP variables. Kept
here because the failure mode is worth recognizing: it is silent at import and only
surfaces as a model-not-found at the first inference call.

`.env.example:10` used to set `GOOGLE_CLOUD_LOCATION=${GCP_REGION}` → `us-central1`.

CLAUDE.md:49 and :86, README.md:613, `wrangler/core/config.py:86`, and
`wrangler/core/deploy.py:420` all say it must be **`global`** for Gemini 3.x and Claude.

Following `.env.example` verbatim gives a broken setup for every non-Gemini-2.x model —
which is most of them, and all of them after 2026-10-16.

It was also missing `SEARCH_MCP_SERVER`, `BOOKING_MCP_SERVER`, `EXPENSE_MCP_SERVER`
and their `_URL` variants, which CLAUDE.md lists as required for the multi-model
agents — the same vars whose absence used to make
`examples/multi_model_agents/config.py` raise on import.

## Test count in CLAUDE.md was stale

CLAUDE.md said 316; the suite was 356 at the time. Corrected 2026-08-20, and it has
moved again since (369 as of the Phase 1 work). Worth re-checking whenever CLAUDE.md
quotes a number — nothing enforces it.

## `wrangler inspect` emits literal `TODO` strings

`wrangler/tools/inspector.py:217-237` generates eval-case scaffolding whose `prompt`,
`expected_response`, and tool args are the literal string `"TODO"`. Intentional
scaffolding, but a generated evalset will run and score against `"TODO"` goldens if
nobody fills them in. Nothing validates that they were.
