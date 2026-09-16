# Campaign 11 — Does the prompt writer sharing the scorer's model amplify the holdout regression?

**Status:** Pre-registered 2026-09-16. **GATED — do not run until the judge quota
escalation lands.** At the current 5 RPM this design costs 44–88 h and, at the arm counts
that fit, can only detect an amplification worth 60–80% of the entire effect. See
[Cost](#cost) before scheduling it.
**Blocked on:** [../escalations/2026-09-11-judge-quota.md](../escalations/2026-09-11-judge-quota.md)
**Follows:** [08-criteria-holdout.md](08-criteria-holdout.md) and
[../analysis/2026-09-16-campaign-08-result.md](../analysis/2026-09-16-campaign-08-result.md)

> **Update 2026-09-16, before this ran:** the writer was moved off the judge's model to
> `claude-opus-4-8`, so **the overlap this campaign measures is no longer shipped** and the
> campaign is now hypothetical as well as gated. Kept, because the power arithmetic below
> applies to any two-condition holdout design, not just this one.
>
> **Update 2026-09-16: the prompt-length alternative is unsupported, not promising.** It was
> briefly recommended ahead of a writer A/B; tested against the seven arms in our own bucket,
> `r(optimized_chars, Δ IF)` flips sign by subset and is −0.112 on the only comparable cohort.
> It should not silently become the next campaign. GEPA's `run_dir` is now preserved so the
> question can be revisited on candidate-level data.
>
> **If a slot opens after the quota lands, run the writer A/B first, not this.** Scored on
> the *criterion* rather than the holdout it is far better powered — `safety_v1` has a large
> effect (+0.149) and small variance (sd 0.0121), so n=2 detects **23%** of the effect where
> this design's n=2 detects only 79%. No DOE file for it yet: nothing is scheduled, and a
> pre-registration for an unscheduled campaign is dead weight.

## Question

GEPA runs two models. The **judge** scores candidates; the **optimizer model** reads the
eval failures and writes the next candidate prompt. PR #83 made both `gemini-3.5-flash`,
so **the model writing the prompts is now the model scoring them**.

Campaigns 07 and 08 ran them as *different* models — writer `gemini-2.5-flash`, scorer
`gemini-3.5-flash` — and measured, on five arms out of five, GEPA improving its criterion
(`safety_v1`) while degrading its holdout (`instruction_following_v1`).

**Does a writer that shares the scorer's preferences make that worse?** The mechanism would
be that it can target the measured score more precisely, buying criterion points at a
steeper cost in things nobody is scoring.

This matters beyond curiosity: writer == scorer is now the shipped default, so if it
amplifies the pathology then every future campaign is biased in a direction we have already
shown the instrument is prone to.

## Why campaigns 07 and 08 cannot serve as the baseline

The tempting design is to run writer == scorer arms and compare against the five recorded
writer ≠ scorer arms. **It does not work, for the same reason campaign 08's `old` condition
did not.**

Those arms ran on a substrate that has since moved: PR #59 (tool-list cache), #70 (redeploy
health gate), #75 (per-generation MCP refresh removed) and #83 itself. A difference against
recorded numbers could not be attributed to writer identity rather than to any of those.

Campaign 08 was rescoped on exactly this reasoning and still returned *unresolved*. Reusing
historical arms here would repeat a mistake this repo has now made once and declined once.

**So both conditions must run concurrently, in the same campaign, under the same load.**

## Design

Two conditions, `n` repeats each, one control per batch.

| arm | optimizer model | judge | role |
| --- | --- | --- | --- |
| `same` × n | `gemini-3.5-flash` | `gemini-3.5-flash` | writer == scorer (current default) |
| `diff` × n | a non-judge model | `gemini-3.5-flash` | writer ≠ scorer (the 07/08 substrate) |
| control × per batch | — (`skip_optimize`) | `gemini-3.5-flash` | floor |

Held identical: agent model (`claude-sonnet-5`, to match the four arms the power estimate
is derived from), seed prompt, `max_metric_calls`, `num_runs: 3`, eval set, health gate on
deploy and redeploy.

**One optimizing arm per batch**, forced rather than chosen: both conditions run the same
agent publisher, so `run_campaign.validate()` rejects pairing them. Same constraint that
made campaign 08 sequential.

### Choosing the `diff` arm's optimizer model is part of the design

Not free, and the obvious pick is wrong. `gemini-2.5-flash` — what 07/08 actually used —
**retires 2026-10-16** and cannot be used. So `diff` cannot reproduce the historical
substrate exactly; it can only be *a* different model.

**This contaminates the comparison unless handled.** PR #83 moved two things at once:

| | 07/08 | now |
| --- | --- | --- |
| writer capability | `gemini-2.5-flash` | `gemini-3.5-flash` |
| writer identity | ≠ scorer | **== scorer** |

A naive `same` vs `diff` contrast re-confounds capability with identity. The `diff` arm must
therefore use a model of **comparable capability to the judge** — a 3.x flash-tier id that
is not the judge — so that identity is the only thing varying. Pick it at design time from
the registry and record the reasoning here; do not default to whatever is cheapest.

`claude-sonnet-5` is attractive for `diff` (rpm 2000, separate publisher pool, no contention
with the judge) but needs the plumbing noted in `DEFAULT_OPTIMIZER_MODEL` — ADK passes a
Gemini-only `thinking_config`, and a bare Claude id lacks the required resource path. If
that plumbing lands first, it is the better `diff` arm and it also removes the optimizer
from the bottleneck pool.

## Pre-registration

**Primary outcome:** Δ`instruction_following_v1` (after − before), per arm.
**Effect:** mean Δ over `same` arms − mean Δ over `diff` arms. A **more negative** `same`
mean is the amplification hypothesis.

### The power arithmetic — read this before scheduling

Derived from the four `claude-sonnet-5` optimizing arms we have, across two campaigns:

| arm | Δ IF |
| --- | --- |
| c07-sonnet5 run A | −0.045 |
| c07-sonnet5 run B | −0.062 |
| c08-new-r1 | −0.068 |
| c08-new-r2 | −0.036 |

**mean −0.0527, sd 0.0148.** Minimum detectable difference between two conditions at 80%
power, α = 0.05 two-sided, `2.8 · sd · √(2/n)`:

| n per condition | optimizing arms | detectable difference | as a fraction of the effect (−0.053) | cost at ~11 h/arm |
| --- | --- | --- | --- | --- |
| 2 | 4 | **0.042** | 79% | ~44 h |
| 3 | 6 | **0.034** | 64% | ~66 h |
| 4 | 8 | **0.029** | 56% | ~88 h |
| 6 | 12 | **0.024** | 45% | ~132 h |

**This is why the campaign is gated.** At any arm count that fits in the current budget, it
can only detect an amplification that is most of the effect. A subtler one — which is the
likely case — lands in the unresolved band, and the campaign costs two to four nights to
say so. Campaign 08 has already demonstrated that outcome at this resolution.

`sd` is computed across two campaigns and therefore includes substrate drift; a
within-campaign sd would be smaller and these numbers correspondingly conservative. It is
still the only honest estimate available, because a within-campaign sd at n=2 has one
degree of freedom.

### Decision rule, fixed in advance

| Reading | Condition |
| --- | --- |
| **Amplified** | `same` mean is more negative than `diff` mean by more than the detectable difference for the n actually run, **and** the arms within each condition agree in sign |
| **Unresolved** | the difference falls inside that band — **not** evidence of no effect |
| **No amplification** | `diff` mean is more negative than `same` mean by more than the detectable difference |

**Gate:** run the controls first. If a control's own IF drift exceeds the within-condition
sd of 0.0148, stop and report that the substrate cannot resolve this. Campaign 08 tripped
exactly this gate at 0.022.

**Secondary, reported either way:** Δ`safety_v1`. If `same` buys *more* criterion gain as
well as more holdout loss, that is the amplification mechanism showing itself on both sides
and is the more informative result than the primary outcome alone.

## What each outcome would mean

| Outcome | Reading |
| --- | --- |
| Amplified | Writer == scorer is a defect in the instrument. Split them permanently, and treat every writer == scorer result as biased in a known direction |
| No amplification | The shared id is safe, and one confound can be struck off future designs. Also frees the optimizer to use the judge's model without argument |
| Unresolved | Most likely at feasible n. Report the number, do not re-derive the bar from the data judging it, and do not run it again without more resolution |
| Control gate tripped | The substrate cannot resolve this effect at all. Fix resolution before any further criteria or instrument work |

## Cost

| | current (judge 5 RPM) | post-escalation (60 RPM) |
| --- | --- | --- |
| 8 optimizing arms + controls | **~88 h**, four nights | **~25–40 h**, estimated |
| `main` frozen throughout | yes | yes |

The post-escalation figure is an **estimate and should be re-derived from a measured
optimize duration**, not trusted. Campaign 08's arm was 673 min of which 572 was optimize;
the non-optimize floor is ~101 min. Optimize is judge-bound, but a 12× RPM increase will not
give a 12× speed-up because latency and the agent's own calls do not scale with the quota.

**Do not schedule this before the quota lands.** The whole point of the gate is that the
same design is a bad experiment at 5 RPM and a good one at 60.

## Result

_Not yet run._
