# Silent failure #12, measured: the close burst is 90–150 s before every loss

**Date:** 2026-09-12
**Run:** campaign 08 validation arm `c08-new-r1`, optimize stage, 572 min, 131 generations
**Custom job:** `1679178152259092480`
**Script:** [`scripts/analyze_toolset_loss.py`](../../scripts/analyze_toolset_loss.py)
**Supersedes the mechanism sections of** [silent-failures #12](../notes/silent-failures.md)

## The number that matters

| campaign | losses / generations | rate |
| --- | --- | --- |
| 07 | 16 / 111 | 14% |
| 08 earlier arm, with PR #59 | 3 / 20 | 15% |
| **08 this arm, with PR #75** | **16 / 131** | **12%** |

**Three fixes have shipped for #12 and the rate has not moved.** PR #51 fixed the
observability, and it is the only one that did what it claimed.

## What was previously claimed, and what was wrong with it

| # | Claim | Status |
| --- | --- | --- |
| 1 | ADK's tool-list cache survives `close()`, so re-warms were fake (PR #59) | **False.** `McpToolset.close()` clears the cache as its first statement. The test asserted it against a *fake* toolset |
| 2 | The per-generation session refresh is the cause (PR #75) | **Insufficient.** Our refresh contributed ~131 closes; ADK's runner teardown contributes **1,812**. Removing ours removed 7% of the closes and 0% of the failures |
| 3 | "All 17 hangs began 6–27 s after a re-warm" | **A correlation with one caller, not with the cause.** The re-warm called `get_tools()`; *any* `get_tools()` can hang |

Claim 2 is the instructive one. It was not wrong about the mechanism — it was wrong about
the *magnitude*, and nobody counted. One `grep -c` would have shown 1,812 closes remaining
after removing 131.

## The measurement

For each failure burst, count `Closing toolset` events in disjoint 30-second bands
before it, against a permutation baseline of 1,500 draws from random instants in the
same stage.

```
seconds before failure   observed   baseline   enrichment
    0 -  30 s                 0.2       1.57       0.2x
   30 -  60 s                 0.0       1.66       0.0x
   60 -  90 s                 0.0       1.64       0.0x
   90 - 120 s                17.2       1.61      10.7x   #################
  120 - 150 s                15.2       1.56       9.8x   ###############
  150 - 180 s                 0.0       1.63       0.0x
  180 - 240 s                 0.0       3.15       0.0x
```

A burst of ~32 toolset closes lands in a **single 60-second window, 90–150 s before every
loss**, at ~10× baseline, p < 0.0001. **Every other band is empty** — including the 90
seconds immediately before the error, and everything earlier than 150 s.

ADK's MCP call timeout is **120 s**. So the hung `list_tools()` started ~120 s before the
error was logged — precisely inside the close burst — and then nothing happened at all
until the deadline fired. The empty bands either side are not noise; they are the shape of
the mechanism.

## The mechanism

1. GEPA finishes a batch of candidate evaluations. Each `Runner.close()` closes the
   toolsets it holds — 604 runner closes × 3 toolsets = **1,812** closes in one stage,
   arriving in bursts.
2. `agent.clone()` is a **shallow** copy, so every candidate shares **one** `McpToolset`
   per server. A close issued by one candidate's runner tears down the session another
   candidate is reading.
3. The victim's `list_tools()` never completes. It sits on a dead session for the full
   120 s.
4. `asyncio.wait_for` → `TimeoutError` → `CancelledError` → `ConnectionError` with an
   empty message, because `str(CancelledError())` is `""`.
5. The agent evaluates **with no tools**, scores near zero on tool use, and that score
   enters the objective GEPA is searching against.

This is why the servers logged 3,626 × 200 OK with zero errors: nothing is wrong with the
servers. The client is reading a session that its own process closed.

## What the earlier reproduction got wrong

[`scripts/repro_mcp_refresh_hang.py`](../../scripts/repro_mcp_refresh_hang.py) measured
0/18 hangs with the cache at 300 s and a racing `close()`, which is the production
setting — so it appeared to exonerate close-racing at the config we run.

It raced **one** close against **one** `get_tools()`. Production races a burst of ~32
closes against concurrent `get_tools()` calls from many runners sharing one toolset. The
reproduction under-modelled both the volume and the sharing, so its negative result was
about the reproduction, not about production.

**The rewrite needs the real topology:** one shared `McpToolset`, N short-lived runners
each calling `close()` on it, and a `get_tools()` in flight across the burst.

## A near-miss worth recording

The first pass of this analysis used a **60-second** window, because the timeout was
assumed rather than read. At 60 s the same data says closes are *depleted* before failures
— 0.1×, p = 0.98 — which reads as a clean exoneration of close-racing, and was nearly
written up as one.

The true timeout is 120 s, and it is stated in silent-failures #12, in this repo, in the
section directly above where the wrong conclusion would have gone.

The band table makes this failure mode hard to repeat: a single-window test collapses to
one number that cannot show it is looking in the wrong place, whereas an empty 0–90 s band
next to a 10× spike at 90–150 s is self-evidently a *location*. `analyze_toolset_loss.py`
therefore takes the timeout as an argument and says in its docstring what happens if it is
wrong.

## Fix direction

Not yet implemented — this document is the diagnosis.

The target is step 2: **a shared toolset that any runner may close.** Three options:

| option | cost |
| --- | --- |
| Make `close()` a no-op on the shared toolsets during optimize, closing once at the end | Narrow, matches the existing `_patch_adk` style. **Recommended** |
| Give each candidate its own toolset | Deep-copies `agent.clone()`; N× sessions against three local servers |
| Stop GEPA closing runners between candidates | Upstream behaviour, not ours to change |

Whatever ships, **the acceptance test is the rate on a real optimize stage, not a
reproduction** — that is the lesson of PR #59 and PR #75, both of which passed their own
tests and changed nothing. Expect `will run without the tools` at 0 against the 12–15%
baseline, and count it with `analyze_toolset_loss.py` rather than by eye.

## Also: the run's own failure counter reports zero

ADK's `_ToolsetFailureCounter` matches `"Failed to get tools from toolset"`. This run
emitted that string **0** times and `"will run without the tools"` **16** times, so the
pipeline's self-reported degradation was **0 while 16 losses sat in the log**.

This was first recorded on 2026-09-08 and is still live. Grep both strings; the analysis
script does.

## Impact on campaign 08

Unchanged from the pre-registration, which recorded this in advance precisely so it could
not be argued about afterwards: `tool_use_quality_v1` is **not interpretable** in this
campaign in either direction, including "no change". The primary outcome
(Δ`instruction_following_v1`) and the secondary (Δ`safety_v1`) do not depend on it, and the
contamination is symmetric across arms.
