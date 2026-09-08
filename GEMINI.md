# GEMINI.md

Context file for Gemini CLI. **The guidance itself lives in
[CLAUDE.md](CLAUDE.md)** — this file imports it rather than restating it.

@CLAUDE.md

---

## Why this file is a pointer and not a copy

Gemini CLI reads `GEMINI.md` the way Claude Code reads `CLAUDE.md`, so the
obvious move is to keep a copy here. Don't. Every serious defect this repo has
recorded in the last month came from two copies of the same thing drifting
apart:

- **`wrangler/core/models.py` and `examples/multi_model_agents/config.py`** both
  define `resolve_model()`. A fix applied to one worked locally and shipped
  broken to every deployed agent. `tests/test_shared_source_drift.py` now
  compares them by *behaviour*, because comparing by source needed an allowlist
  that would eventually let a real difference through.
- **A second CLI.** Seven online-eval commands lived behind their own dispatcher
  and never appeared in `wrangler --help`, so `trace-health` — the diagnostic
  for a real span-drop failure — was unfindable. It is now a shim over the one
  implementation.
- **A guide that drifted from the code.** `docs/online_eval_guide.md` accumulated
  14 references to a module path deleted in a package reorganisation. Every
  documented command failed with `No module named`.

A 500-line duplicate of CLAUDE.md would be the largest instance of that pattern
in the repo, and the least likely to be noticed: nothing fails when a docs copy
goes stale, it just quietly teaches the wrong thing. `tests/test_agent_context.py`
fails if this file grows its own guidance instead of importing.

If `@CLAUDE.md` import is unsupported by your tooling, read
[CLAUDE.md](CLAUDE.md) directly — it is the single source of truth, and
[CODE_STANDARDS.md](CODE_STANDARDS.md) is authoritative for tooling and
conventions.

## The one thing worth repeating here

Because it is the most expensive mistake available in this repo, and a reader
who skims may not reach it:

**A green test suite is not evidence that a change works.** The suite is
deliberately hermetic — no network, no GCP — so it mocks the Vertex SDK. On
2026-09-08 it stayed green through two separate breakages that made live
inference fail outright, and through a dependency set that could not resolve at
all. Before trusting a change to deployment, eval, or dependencies, run
`uv run wrangler preflight` and deploy one probe engine. See CLAUDE.md's
"Pipeline Pitfalls" and [docs/notes/silent-failures.md](docs/notes/silent-failures.md).
