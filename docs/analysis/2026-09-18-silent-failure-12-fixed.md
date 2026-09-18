# Silent failure #12 is fixed — 0 losses in 202 generations, against a 16.6% baseline

**Date:** 2026-09-18
**Campaign:** 09 (DOE 12), pipeline job `gepa-run-86239e1924-20260917-213907`
**Fix under test:** ADK patch 8, `_deferred_toolset_closes()` (merged 2026-09-17, PR #101)
**Pre-registration:** [../../experiments/active/c09-rationale/doe_plan.md](../../experiments/active/c09-rationale/doe_plan.md)
**Status:** **PASSED** on two arms; third arm in flight

## The result

| | losses | generations | rate |
| --- | --- | --- | --- |
| `c09-rationale-off` (complete) | **0** | 136 | **0%** |
| `c09-rationale-on` (in flight) | **0** | 66 | **0%** |
| **c09 so far** | **0** | **202** | **0%** |
| historical pooled | 82 | 494 | **16.6%** |

Both ADK wordings were counted — `will run without the tools` (≥ 2.8.0) and
`Failed to get tools from toolset` (≤ 2.7.1). Matching only one is how the in-run counter
read zero through a live campaign on 2026-09-08.

**P(0 losses in 202 generations at the pooled 16.6% rate) = 1.2 × 10⁻¹⁶.**
95% upper bound on the true rate, by the rule of three: **1.5%**.

### The baseline this replaces

| arm | losses / generations | rate |
| --- | --- | --- |
| c07 validation | 16 / 111 | 14.4% |
| c08 early (with PR #59) | 3 / 20 | 15.0% |
| c08-new-r1 (with PR #75) | 16 / 131 | 12.2% |
| c08-new-r2 (with PR #75) | 29 / 119 | 24.4% |
| m01 | 18 / 113 | 15.9% |

**Three fixes shipped before this one and the rate never moved.** Two of them passed their
own tests. That history is why the acceptance test was defined as a rate on a real optimize
stage rather than a green suite, and why the evidence below matters as much as the zero.

## The patch demonstrably ran — which is the half that makes the zero mean anything

A stage reporting zero losses because patch 8 never executed would look identical to a stage
reporting zero because it worked. The pre-registration therefore required a **non-zero
deferred-close count**, and this is it:

```
[c09-rationale-off]  Deferred 1776 toolset close(s) across 3 shared toolset(s);
                     closed them once at teardown (silent-failures #12)
```

**1,776 deferrals against 1,776 logged `Closing toolset` events — a 1:1 correspondence.**
Every teardown the runners issued was absorbed by the window and none reached a live session.
For scale, the contaminated stages logged ~1,812 closes each, so the patch is operating at
full production volume rather than on a quiet run.

`c09-rationale-on` has logged 1,065 closes so far and will print its own count on completion.

## Why the earlier fixes failed and this one did not

The mechanism was localised on 2026-09-12 by measurement rather than by reading: a burst of
~32 closes lands 90–150 s before every loss at ~10× baseline, p < 0.0001, with every other
band empty — one ADK timeout (120 s) before the error.

`agent.clone()` is a **shallow** copy, so every GEPA candidate shares **one** `McpToolset`
per server. `Runner.close()` tears down the toolsets it collects, so one candidate's
teardown strands another candidate's in-flight `list_tools()`. The victim blocks the full
120 s, ADK hands the agent **zero tools**, and GEPA scores that candidate.

The three earlier fixes addressed the wrong term:

| fix | what it did | why it failed |
| --- | --- | --- |
| PR #51 | logged the MCP servers' output | observability only — correctly scoped, and it is what made the diagnosis possible |
| PR #59 | cleared the tool-list cache | the cache was *protecting* us; clearing it removed a mitigation |
| PR #75 | removed our per-generation refresh | our refresh contributed ~131 closes; ADK's runner teardown contributes **1,812** |

Patch 8 removes the **precondition** rather than narrowing the race: with no teardown
mid-run there is nothing to strand, whatever the concurrency and whatever the cache does.

## What this does and does not license

**Does:**

- Tool-use numbers from campaign 09 onward are usable. The 12–24% contamination warning no
  longer applies to runs carrying patch 8.
- The mechanism is confirmed in production, not just in a reproduction.

**Does not:**

- **Retrospectively clean campaigns 07, 08 or m01.** Their `tool_use_quality_v1` results
  remain uninterpretable and must not be quoted as findings.
- **Prove the fix under every condition.** Two arms on one agent (`sonnet_agent`,
  claude-sonnet-5) against three local MCP servers. A different toolset count or a remote
  MCP path is untested.
- **Say anything about campaign 09's actual question.** This is an infrastructure result
  that happened to ride along; the rationale-forwarding analysis is separate and pending.

One caveat stated plainly: the third arm (`c09-control`) runs no optimize stage by design, so
this campaign yields **two** #12 observations, not three.

## Reading a future run

The observability traps are as important as the fix, because both can turn a broken
measurement into a clean-looking one:

- **`Closing toolset` still appears at full volume** — ADK logs it, and
  `Successfully closed toolset`, around the await. A post-fix stage still shows ~1,800 and
  the 90–150 s burst is still visible, now harmless. **Do not read that as the fix not
  applying.**
- **Zero losses AND zero closes means the query is broken**, not that the run is clean.
  `analyze_toolset_loss.py` exits non-zero on that combination.
- **`--freshness` is not optional.** `gcloud logging read` defaults to 1 day and truncates
  silently; that default once turned 29 losses into a reported zero.
- **`Deferred 0 toolset close(s)` voids the result.** It means the stage never exercised
  patch 8 — most likely a KFP cache hit on an unchanged `run_id`, since `optimizer.py` rides
  in the code tarball rather than a component body.

## Cost

Zero. The measurement rode on a campaign that was running anyway, which is why it was
scheduled that way rather than as a standalone verification run.
