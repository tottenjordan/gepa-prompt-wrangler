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

## `GOOGLE_CLOUD_LOCATION` could never have expressed the location rule

**Recurred and then fixed properly on 2026-08-20.** It was first "fixed" by correcting
`.env.example` from `${GCP_REGION}` to `global`. Within the same day the working `.env`
was back to `${GCP_REGION}`, and Claude broke again with

```
Publisher Model .../locations/us-central1/publishers/anthropic/models/claude-sonnet-4-6
is not servable in region us-central1
```

That recurrence is the finding. **A single process-wide env var cannot encode a
per-model fact.** This repo routes five tiers in one process — lite/flash/pro are Gemini
3.x (global-only), sonnet/opus are Claude (global-only), and Gemini 2.x wants a region.
No one value of `GOOGLE_CLOUD_LOCATION` is right for all of them, so any correct setting
is one edit away from being wrong, and nothing catches the edit. Worse, GEAP treats the
variable as **restricted** and can serve it back regionally regardless of what the
deployment config asked for — so even a correct `.env` did not survive deployment.

The durable form is `wrangler/core/models.py:model_location()`, which pins the location
*into each model object* (Claude: a full `locations/global` resource path, which ADK
parses in preference to the env var; Gemini 3.x: `client_kwargs={"location": ...}`,
forwarded to `google.genai.Client`). The env var remains only as a fallback for paths
that bypass `resolve_model()`.

Two things worth carrying forward:

- The failure is **silent at import**. Nothing complains until the first inference call,
  which in a pipeline can be twenty minutes in.
- `deploy.py` had already worked around this for Claude by rewriting bare ids to a global
  resource path on the way into the build package. A workaround at one call site is a
  signal the rule belongs in the resolver, not evidence the problem is handled — Gemini
  3.x had no equivalent cover and was quietly exposed the whole time.

`.env.example` was also missing `SEARCH_MCP_SERVER`, `BOOKING_MCP_SERVER`,
`EXPENSE_MCP_SERVER` and their `_URL` variants (fixed 2026-08-20).

## Agent Registry–managed Cloud Run services do not appear in `gcloud run services list`

The `wrangler-search-mcp` / `-booking-mcp` / `-expense-mcp` services back the multi-model
agents' MCP tools. They are managed by **Agent Registry**, and `gcloud run services list`
does not show them. I read that empty list as "the services were deleted", concluded the
`.env` URLs were stale, and rewrote both `.env` files to point at hosts that did not
exist — turning a working setup into a broken one.

They were live the whole time. **Absence from `gcloud run services list` is not evidence
of deletion.** Probe the host instead:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://wrangler-search-mcp-...run.app/mcp
```

A bare `GET` returns **406**, not 404 — that is a healthy MCP endpoint refusing a
non-MCP request, and it is the cheapest positive signal available. For a real check,
POST an MCP `initialize`; a live server answers with `serverInfo`.

The generalizable version: before concluding a resource is gone, find a probe that
returns a *positive* signal. A tool that lists nothing may simply not be the tool that
lists that kind of thing.

## Test count in CLAUDE.md was stale (fixed 2026-08-20)

CLAUDE.md said 316; the suite was 356. Corrected, then it went stale twice more in the
same day (369, 401). **Fixed by removing the number** rather than updating it — nothing
enforces a count in prose, so quoting one guarantees it will be wrong. The same applies
to any other figure a doc quotes about the code.

## `wrangler run --max-concurrent` is silently ignored on the default path

`cli.py` threads `max_concurrent` into `WranglerPipeline(...)`, and that constructor
only runs on the legacy branch (`--resume-from` or `--from-phase > 0`). The default
`run` path goes through `Experiment` + `stage_*` functions, which never see the value.
So `wrangler run manifest.yaml -c 4` is accepted, prints nothing, and evaluates
sequentially anyway.

Click cannot warn about this — the option parses fine; it just lands in a variable
nobody reads. The general shape: **an option that is only consumed inside one branch of
the command body looks supported from `--help`.** Grep for the parameter name before
trusting a flag; if it appears once in `cli.py` and once in a constructor that a
conditional guards, it does nothing on the path you are on.

Two other `run` options are in the same legacy-only category (`--resume-from`,
`--from-phase`) but those *are* the branch condition, so they are self-evident.
`--version`, `--name`, `--pair`, `--num-runs` and `--dry-run` all work on the default
path. Documented in README's Options block as of 2026-08-20.

## `wrangler inspect` emits literal `TODO` strings

`wrangler/tools/inspector.py:217-237` generates eval-case scaffolding whose `prompt`,
`expected_response`, and tool args are the literal string `"TODO"`. Intentional
scaffolding, but a generated evalset will run and score against `"TODO"` goldens if
nobody fills them in. Nothing validates that they were.
