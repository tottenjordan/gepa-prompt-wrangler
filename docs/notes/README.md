# Session Notes — Index

Durable findings from working sessions: things that outlive the conversation and are
**not** recoverable from the code, git history, or [CLAUDE.md](../../CLAUDE.md).

One topic per file. Keep this index under 200 lines — put detail in the topic notes.

## Notes

| Note | What it covers |
|------|----------------|
| [adk-patch-status.md](adk-patch-status.md) | Per-patch status of the ADK monkey-patches against the installed ADK, with upstream issue outcomes. **Read before any ADK bump.** |
| [model-lifecycle.md](model-lifecycle.md) | Retirement dates, the Claude sampling-parameter cutoff, what the registry does *not* cover, and the **2026-08-20 judge A/B** that moved GEPA scoring to `gemini-3.5-flash`. |
| [adk-judge-model.md](adk-judge-model.md) | Why only the GEPA path reads `judge_model` at all — batch eval cannot send one. Predicted the risks the judge A/B then measured. |
| [toolchain-baseline.md](toolchain-baseline.md) | Measured lint/type/test baseline as of 2026-08-20, and what infrastructure does not exist yet. |
| [repo-traps.md](repo-traps.md) | Non-obvious footguns: gitignored lockfile, duplicated config, why `GOOGLE_CLOUD_LOCATION` can't express the per-model location rule, Agent Registry services invisible to `gcloud run services list`, `TODO` goldens. |
| [silent-failures.md](silent-failures.md) | Five failures that reported success: MCP tools missing at deploy, startup checks that never ran, batch eval scoring 0 cases, a tool-use metric floored at ~0.42, and GEAP returning 200 with an empty event stream from a booting worker. |

## Active Plans

| Plan | Status |
|------|--------|
| [../plans/2026-08-20-repo-modernization.md](../plans/2026-08-20-repo-modernization.md) | Phases 0–2 done. Phase 3: 3.1–3.3 done; **3.4 in progress** — the smoke test surfaced four silent failures ([silent-failures.md](silent-failures.md)), all fixed; deploy + eval-before now pass, remaining stages not yet run. |

## Conventions for These Notes

- Check for an existing note on the topic and **update it** rather than adding a
  duplicate. Delete notes that turn out to be wrong or stale.
- Notes record what was true **when written** — each carries a "verified on" date. If a
  note names a file, flag, or command, re-verify it still exists before acting on it.
- Do not restate what the repo already records. Capture the thing you would otherwise
  rediscover the hard way.
