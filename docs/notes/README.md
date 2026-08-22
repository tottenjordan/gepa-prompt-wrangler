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
| [silent-failures.md](silent-failures.md) | Eight failures that reported success: MCP tools missing at deploy, startup checks that never ran, batch eval scoring 0 cases, a tool-use metric floored at ~0.42, GEAP returning 200 with an empty event stream from a booting worker, GEPA optimizing against a safety score pinned at zero, the autorater emitting a tool call so the case is dropped from every metric, and OTel span batches dropped under load beneath online eval (fixed, and now detectable with `trace-health`). Also carries the log queries that found them, and the three refuted hypotheses behind the traffic-generator redesign. |

## Active Plans

| Plan | Status |
|------|--------|
| [../plans/2026-08-20-repo-modernization.md](../plans/2026-08-20-repo-modernization.md) | **Complete.** Phases 0–3 done. The final task — measure a real prompt change — landed 2026-08-22: a three-arm sweep (sonnet/flash/pro, identical seed and budget) produced GEPA's first genuine optimization since May. Flash returned its seed unchanged and so serves as a control, putting the noise floor at +0.039. Results and limits in [model-lifecycle.md](model-lifecycle.md). |

## Conventions for These Notes

- Check for an existing note on the topic and **update it** rather than adding a
  duplicate. Delete notes that turn out to be wrong or stale.
- Notes record what was true **when written** — each carries a "verified on" date. If a
  note names a file, flag, or command, re-verify it still exists before acting on it.
- Do not restate what the repo already records. Capture the thing you would otherwise
  rediscover the hard way.
