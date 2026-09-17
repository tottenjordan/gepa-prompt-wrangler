---
name: tool-use-report
description: Use when analysing how agents used their MCP tools during a run in this repo — tool-use quality scores, toolset losses, declared-versus-actually-called tools, or whether a tool-use number is trustworthy at all.
---

# Tool use report

## Overview

A tool-use report answers one question: **did the agent actually have and use its tools, and
is the resulting score measuring the prompt or measuring an infrastructure failure?**

That framing is not academic. Every `tool_use_quality_v1` number this repo produced before
2026-09-17 carries a **12–24% contamination rate** from silent failure #12 — GEPA scored
candidates whose agent had been handed *zero tools* by a session teardown. The score looked
like a bad prompt. It was a closed socket.

**So the first section of a tool-use report is never the scores. It is whether the tools were
there.**

## When to use

- Reading `tool_use_quality_v1` for a campaign arm
- A tool-use score looks low, or suspiciously bimodal
- Asked about tool usage, tool adoption, or which tools an agent reaches for
- Validating that a tool-related fix worked (e.g. silent failure #12's acceptance test)

## Step 1: is the run contaminated? (do this before reading any score)

```bash
uv run python scripts/analyze_toolset_loss.py --job-id <numeric job id> --freshness 90d
```

**`--freshness` is not optional.** `gcloud logging read` defaults to **1 day** and truncates
silently — that default once turned 29 real toolset losses into a reported zero, and the
script now exits non-zero rather than calling such a run clean.

Grep **both** wordings; ADK reworded the warning at 2.8.0 and matching one spelling is how
the in-run counter read zero through a live campaign:

- `Failed to get tools from toolset` (≤ 2.7.1)
- `will run without the tools` (≥ 2.8.0)

| observation | reading |
| --- | --- |
| losses = 0 **and** `Closing toolset` events > 0 | clean run |
| losses = 0 **and** closes = 0 | **broken query**, not a clean run — widen `--freshness` |
| losses > 0 | contamination rate = losses / generations. Quote it beside every tool-use number |

**`Closing toolset` no longer means a toolset was closed.** Since patch 8 (2026-09-17) the
close is deferred, but ADK still logs `Closing toolset` and `Successfully closed toolset`
around the await. A post-fix run still shows ~1,812 of them and the 90–150 s burst is still
visible — **now harmless.** The signals are the printed `Deferred N toolset close(s)` count
and the loss count, not the close count.

## Step 2: what the agent could have called

The three MCP toolsets are declared in `examples/multi_model_agents/registry.py` and the
generated `_REGISTRY_PY_TEMPLATE` in `wrangler/core/deploy.py` — **keep them in sync**;
`tests/test_shared_source_drift.py` enforces it.

| toolset | port (local, in-container) | env vars |
| --- | --- | --- |
| search | 8001 | `SEARCH_MCP_SERVER`, `SEARCH_MCP_URL` |
| booking | 8002 | `BOOKING_MCP_SERVER`, `BOOKING_MCP_URL` |
| expense | 8003 | `EXPENSE_MCP_SERVER`, `EXPENSE_MCP_URL` |

**Declared ≠ available ≠ called**, and a report that conflates them is the one that blames
the prompt. Distinguish:

- **Declared** — in the registry for that agent
- **Available** — `get_tools()` succeeded for that invocation (Step 1 tells you)
- **Called** — appears in the captured trajectory

## Step 3: read the scores, with the metric's history attached

`tool_use_quality_v1` in this repo is a **custom `types.LLMMetric`**, not the predefined
rubric metric. The predefined one is reference-free, auto-generates rubrics blind to the
agent's tools, and **caps a correctly tool-using agent near 0.33–0.42** by rewarding *not*
calling tools. If a report shows tool use pinned in that band, suspect the metric before the
agent — see CLAUDE.md, "tool_use_quality floor".

**The judge prompt was re-baselined on 2026-09-17** (JSON-hardened; criteria byte-identical).
Effects measured over five passes each:

| | original | hardened |
| --- | --- | --- |
| cases scored | 312/320 — 8 lost | **320/320 — 0 lost** |
| sd of the mean | 0.0100 | **0.0043** |

**Per-case `tool_use_quality_v1` comparisons must not cross that date** — 14.3% of cases
re-score, above the hardened prompt's own 7.5% self-disagreement. Aggregate comparisons may.

## Step 4: figures

PaperBanana (`paperbanana-figures` skill), not raw matplotlib. Worth drawing:

1. **Toolset losses per generation** with the 90–150 s close-burst band marked — this is the
   figure that localised #12 and it reads instantly
2. Tool-use score distribution per arm, with the 0.33–0.42 metric-artifact band shaded
3. Coverage for `tool_use_quality_v1` per pass, if `score_repeats > 1`

## Step 5: write it

Use `references/report-template.md`. Output goes where `campaign-result-report` says:
`experiments/active/<name>/reports/` for a campaign, `docs/analysis/` for cross-cutting work.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Reading tool-use scores before checking for toolset losses | Step 1 first, always |
| `analyze_toolset_loss.py` without `--freshness` | Defaults to 1 day and truncates silently |
| Grepping one ADK wording | Match both; the wording changed at 2.8.0 |
| Reading surviving `Closing toolset` lines as "the fix did not apply" | They persist by design since patch 8 |
| Trusting the in-run counter alone on an old container | `_ToolsetFailureCounter` read zero through a live campaign before the 2026-09-08 fix |
| Comparing per-case tool-use across 2026-09-17 | Aggregate only across that boundary |
| Blaming the prompt for scores in 0.33–0.42 | That is the predefined-metric artifact, not the agent |
| Reporting a tool-use delta from campaigns 07/08 | Uninterpretable at 12–24% contamination. Say so rather than quoting it |

## Red flags

- **Zero losses and zero closes** — the query is wrong, not the run clean.
- **`Deferred 0 toolset close(s)`** on a post-fix run — patch 8 never engaged; any #12
  verdict from that stage is void.
- **Tool-use scores tightly clustered near 0.4** — metric artifact, check `_tool_use_metric()`.
- **A tool-use improvement that exactly tracks a coverage improvement** — you are measuring
  dropout recovery, not tool use.
