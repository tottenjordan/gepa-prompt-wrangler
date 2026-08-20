# Session Notes — Index

Durable findings from working sessions: things that outlive the conversation and are
**not** recoverable from the code, git history, or [CLAUDE.md](../../CLAUDE.md).

One topic per file. Keep this index under 200 lines — put detail in the topic notes.

## Notes

| Note | What it covers |
|------|----------------|
| [adk-patch-status.md](adk-patch-status.md) | Per-patch status of the 5 ADK monkey-patches against the installed ADK, with upstream issue outcomes. **Read before any ADK bump.** |
| [model-lifecycle.md](model-lifecycle.md) | Model retirement dates, current Vertex model IDs, and where model IDs are hardcoded. **Gemini 2.5 retires 2026-10-16.** |
| [adk-judge-model.md](adk-judge-model.md) | Whether the LLM-as-judge model can move off Gemini 2.5 (yes), and the two non-obvious reasons to validate before doing it. |
| [toolchain-baseline.md](toolchain-baseline.md) | Measured lint/type/test baseline as of 2026-08-20, and what infrastructure does not exist yet. |
| [repo-traps.md](repo-traps.md) | Non-obvious footguns: gitignored lockfile, broken CLI path, duplicated config, contradictory env guidance. |

## Active Plans

| Plan | Status |
|------|--------|
| [../plans/2026-08-20-repo-modernization.md](../plans/2026-08-20-repo-modernization.md) | Proposed — awaiting execution |

## Conventions for These Notes

- Check for an existing note on the topic and **update it** rather than adding a
  duplicate. Delete notes that turn out to be wrong or stale.
- Notes record what was true **when written** — each carries a "verified on" date. If a
  note names a file, flag, or command, re-verify it still exists before acting on it.
- Do not restate what the repo already records. Capture the thing you would otherwise
  rediscover the hard way.
