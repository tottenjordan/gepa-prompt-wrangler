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
| [engine-lifecycle.md](engine-lifecycle.md) | How 80 Agent Engines accumulated unnoticed and how they are reaped. The policy gives every signal a veto — **only 48 of 80 were labelled ours, and three unlabelled ones were the busiest in the project**, so an age- or name-based sweep would have deleted someone else's live work. Also: cost is `min_instances`, not count; probe traffic is self-generated and does not protect; deletes are rate-limited per minute. 2026-08-24 teardown took 80 → 38 engines and 61 → 33 warm instances. Also documents the **deploy-time health gate** and why its threshold and reroll budget are the numbers they are. |
| [engine-inventory-2026-08-24.md](engine-inventory-2026-08-24.md) | Per-engine disposition snapshot taken before that teardown, so a deletion traces to a decision. |
| [silent-failures.md](silent-failures.md) | Nine failures that reported success: MCP tools missing at deploy, startup checks that never ran, batch eval scoring 0 cases, a tool-use metric floored at ~0.42, GEAP returning 200 with an empty event stream from a booting worker, GEPA optimizing against a safety score pinned at zero, the autorater emitting a tool call so the case is dropped from every metric, and OTel span batches dropped under load beneath online eval (fixed, and now detectable with `trace-health`). Also carries the log queries that found them, and the three refuted hypotheses behind the traffic-generator redesign. Plus #9: cases that infer fine but vanish during scoring — the same extra=forbid boundary, reached by a path the earlier fix missed. |

## Analyses

| Analysis | What it covers |
|----------|----------------|
| [../analysis/2026-08-31-mcp-flakiness.md](../analysis/2026-08-31-mcp-flakiness.md) | The "MCP flakiness" was **not MCP** — the servers returned 200 on 3,000+ requests. A 30s client probe budget was blown by containers taking a median 109s (max 834s) to start against a healthy 6.3s, and the `FATAL: agent cannot use tools` message was false. After the fix: 124 workers, zero failures. Also records what it exposed: redeploying rerolled the health lottery and shipped a 0/30 engine, and **opus failed six consecutive deploys** (1% event) — not the lottery, and not startup. |
| [../analysis/2026-08-23-geap-empty-stream-doe.md](../analysis/2026-08-23-geap-empty-stream-doe.md) | 960 requests, four engines, two models, with a per-request join. GEAP returns 200 with no inference for **31.7%** of requests — and the cold-worker cause this repo believed for two days is **refuted**: all 948 joined requests were served by workers that had already finished booting. Also kills the "~8s startup" and "empty responses wait for the boot" claims. Rate is per-engine and varies 4%–68%. |
| [../analysis/2026-08-22-first-optimization-sweep.md](../analysis/2026-08-22-first-optimization-sweep.md) | The first sweep that produced a real prompt change. Three arms, identical seed and budget; flash returned its seed unchanged and so became an unplanned control, putting the noise floor at +0.039. Sonnet clears it on four metrics, pro on one — and pro *regresses* on instruction-following, structurally. Total cost $1.14. Follow-up explains the instruction_following divergence: it is a **holdout** GEPA never optimizes, and its only pressure (the instruction_adherence rubric) was gated at 0.85 for sonnet but 0.50 for pro. |

## Escalations

| Escalation | Status |
|------------|--------|
| [../escalations/2026-08-23-geap-empty-stream.md](../escalations/2026-08-23-geap-empty-stream.md) | **Ready to file, not yet filed.** Agent Engine returns 200 with an empty stream and runs no inference, 31.7% of requests. The ask is narrow: do not use 200 for a request the service did not serve — 1.2% of requests already return `400 Service Unavailable`, so the path exists. |

## DOE Campaigns

Pre-registered experiments — question, design, n and stopping rule written down *before*
collecting. Index and rules in [../doe/README.md](../doe/README.md).

| Campaign | Status |
|----------|--------|
| [../doe/01-engine-lottery.md](../doe/01-engine-lottery.md) | **Complete 2026-08-24.** Ten byte-identical engines ranged **0%–100%** reach, so the spread is a deployment lottery. Sharper: of 626 worker processes, only 3.2% ever both succeeded and failed — a worker is persistently good or persistently bad, and an engine's rate is its bad fraction (r=0.954 against requests-per-worker). Redeploying in place redraws it (0%→50%, 6%→56%), so a deploy-time health gate works. |
| [../doe/02-judge-variance.md](../doe/02-judge-variance.md) | Not started. Splits the noise floor into judge vs agent non-determinism — never separated — and settles the `tool_use_quality` JSON hardening by scoring identical responses. |
| [../doe/03-noise-floor.md](../doe/03-noise-floor.md) | Not started. Re-measures the floor across `num_runs`; the 0.034 figure at 3 runs was computed through positionally-mispaired averaging. Produces a `minimum_detectable_effect()` the reporter can call. |
| [../doe/04-gepa-budget-and-criteria.md](../doe/04-gepa-budget-and-criteria.md) | Not started, expensive (~90 h). What a GEPA budget buys, and whether the instruction-following regression is a configuration asymmetry or the cost of leaving a metric out of the criteria. |

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
