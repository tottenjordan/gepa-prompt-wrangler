# Campaign 07 — What does a model tier actually cost per unit of quality?

**Status:** Ready to run · **Depends on:** Campaign 06 for the floor

## Question

Two, answered by the same runs:

1. What does each tier cost per unit of quality, on **real spend** rather than list price?
2. **Can a better prompt substitute for a bigger model?**

The full chain answers both at once, which is why this is one campaign and not
two: `eval_before` is every model on a generic prompt, `eval_after` is every
model on an optimized one. That is the 2×2 of {tier} × {prompt quality}, in
dollars.

## The four models

Chosen for cost spread within each publisher, and because each already resolves
through an existing agent module:

| tier | model | list $/M blended |
| --- | --- | --- |
| cheap Gemini | `gemini-3.1-flash-lite` | 0.50 |
| cheap Claude | `claude-sonnet-5` | 3.60 |
| expensive Claude | `claude-sonnet-4-6` | 5.40 |
| expensive Gemini | `gemini-3.1-pro-preview` | 6.80 |

A 13.6× spread end to end. `claude-sonnet-5` against `claude-sonnet-4-6` is the
practically useful pair: the registry says sonnet-5 is cheaper in both
directions, and whether it is *as good* is worth knowing.

**Opus is excluded.** It is disabled in every manifest — 15 gated deploys
produced nothing above 50% reach.

## Design

Full chain, same **generic seed prompt** for all four, so the model is the only
variable. Two arms at a time, **always one Anthropic and one Gemini**, so the
publishers' quotas do not contend.

- Batch 1: `claude-sonnet-5` ‖ `gemini-3.1-pro-preview`
- Batch 2: `claude-sonnet-4-6` ‖ `gemini-3.1-flash-lite`

Cost tier is **crossed with batch, not confounded with it** — if one batch is
unlucky (a bad engine draw, a quota event) it does not land entirely on the
cheap arms or entirely on the expensive ones.

`max_metric_calls: 600`. The arithmetic: deploy 0.3 h + eval 1.5 h + optimize
~7 h + redeploy 0.3 h + eval 1.5 h ≈ **10.6 h** per arm. 800 would not fit.

**Stagger the two pipelines by ~90 minutes.** The GEPA judge is
`gemini-3.5-flash` on both arms, so the optimize phases *do* share Gemini quota
even though the agent models do not. Varying the judge per-arm would confound
the comparison — it was set by an A/B — so stagger instead.

## Pre-registration

- Same seed, same eval set, same `num_runs` (whatever 06 recommends), health gate on.
- Report **every** arm, including any that returns its seed unchanged. That is a
  legitimate result and last time it was the only calibration a sweep had.
- Report coverage per arm. A delta computed on partial coverage is not a measurement.
- No arm is re-run because its number looks wrong.

## Outputs

- Measured $/run against quality, per model, at both prompt states.
- The marginal cost of a quality point: moving up a tier versus optimizing the prompt.
- Both cost bases side by side. A model can look cheap on list price and dear on
  spend — that inversion is the reason `measured_cost()` exists.

## Result

_Not yet run._
