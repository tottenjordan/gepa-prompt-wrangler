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

## `wrangler deploy` used the deployment path CLAUDE.md calls broken (fixed 2026-08-20)

**Fixed** — the cloudpickle functions are deleted, all call sites use
`deploy_agent_from_source()` / `update_agent_from_source()`, and
`tests/test_deploy.py::test_cloudpickle_entrypoints_are_gone` fails if the names come
back. Kept here for the pattern, which is the actually transferable part.

CLAUDE.md said `deploy_agent()` / `update_agent()` "remain for backward compatibility
only" and were "not used by the pipeline or local workflow." That last part was wrong:
the `wrangler deploy manifest.yaml` branch of the CLI and both the deploy and redeploy
phases of `WranglerPipeline.run()` all called them. Only the *experiment-directory*
branch of `wrangler deploy` (via `stage_deploy`) reached the working path — so
`wrangler deploy manifest.yaml` took the broken route while `wrangler run
manifest.yaml` worked, with no hint from the CLI.

**The pattern:** a doc that says "legacy, kept for compatibility" is a claim about
call sites, and nothing enforces it. When a working replacement lands beside a broken
original, the original does not stop being called just because the docs stop
mentioning it. Delete it, or add a test that asserts it is unreachable.

Two things the migration turned up that the phrase "swap the call" hides:

- The redeploy path used to carry the optimized prompt by mutating `agent.instruction`
  on a freshly imported agent object. Source-based deployment passes the text as an
  argument instead, so it is now possible to redeploy the *seed* prompt and get
  after-scores that silently mean nothing. Pinned by
  `test_redeploy_pair_sends_the_optimized_prompt`.
- `examples/multi_model_agents/` reads the model off `config.py` rather than off the
  imported agent, because the agent holds the model **resolved** to an ADK
  `Gemini()`/`Claude()` object while the build package wants the plain id string.

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

## Test count in CLAUDE.md was stale (fixed 2026-08-20)

CLAUDE.md said 316; the suite was 356. Corrected, then it went stale twice more in the
same day (369, 401). **Fixed by removing the number** rather than updating it — nothing
enforces a count in prose, so quoting one guarantees it will be wrong. The same applies
to any other figure a doc quotes about the code.

## `wrangler inspect` emits literal `TODO` strings

`wrangler/tools/inspector.py:217-237` generates eval-case scaffolding whose `prompt`,
`expected_response`, and tool args are the literal string `"TODO"`. Intentional
scaffolding, but a generated evalset will run and score against `"TODO"` goldens if
nobody fills them in. Nothing validates that they were.
