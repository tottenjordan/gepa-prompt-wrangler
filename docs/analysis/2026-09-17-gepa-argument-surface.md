# What ADK exposes of GEPA, and what it drops

**Date:** 2026-09-17 · **gepa** as installed · **ADK** 2.8.0

`gepa.optimize()` takes **46 arguments**. ADK's `GEPARootAgentPromptOptimizer` forwards
**8** (`gepa_root_agent_prompt_optimizer.py:285-294`). This is what the other 38 are, which
ones matter for the problems we actually have, and the one thing that turned out to matter
more than any of them.

---

## The headline is not an argument at all

**The reflection model — the thing that writes every candidate prompt — never sees why a
rubric failed, even though the judge wrote it down and ADK is holding the text.**

`LocalEvalSampler._extract_eval_data()` builds the reflective dataset and emits, per metric:

```python
{"metric_name": ..., "score": round(score, 2), "eval_status": ...}
```

A name, a rounded float, a status. Meanwhile the object it is reading from carries:

```
EvalMetricResult.details.rubric_scores[] -> RubricScore(rubric_id, score, rationale)
                                                                        ^^^^^^^^^
```

`rationale` is a populated per-rubric explanation. **ADK drops it.** So our reflector is told
*"instruction_following_v1: 0.71"* and never *"asked for one line, returned six"*.

This matters more than the argument surface because of what the literature says drives GEPA:
the metric's textual feedback is read straight into the reflection prompt, and *"a metric
that returns only pass or fail starves the reflection step"* — the quality of that feedback
shapes results as much as the reflector model does.

It also offers a mechanism for two things we have measured and not explained:

- **criterion up, holdout down, five arms of five.** The reflector can watch the criterion's
  number move. It has no diagnostic text for anything, so it optimises the number it can see.
- **prompts growing 78 → 3,873 characters.** With no diagnosis of *why* a case failed, adding
  more instructions is the only move available. Verbosity is what blind search looks like.

**This is fixable by us.** The rationale is present in the objects; only the extraction drops
it. A 6th `_patch_adk` entry overriding `_extract_eval_data`, or an upstream ADK change.

---

## The 38 dropped arguments

| category | n | verdict |
| --- | --- | --- |
| observability | 13 | mostly irrelevant — we preserve `run_dir` now |
| search behaviour | 10 | **one real opportunity: `use_merge`** |
| reflection / proposal | 6 | the length-regularization hooks; premise unsupported |
| reproducibility / infra | 4 | **`seed` is a trap — see below** |
| budget / stopping | 2 | `max_reflection_cost` worth having, cheaply |
| n/a — the adapter supplies these | 3 | `task_lm`, `evaluator`, `val_evaluation_policy` |

### `use_merge` — off here, and the only knob the literature ties to our problem

Our installed gepa defaults `use_merge=False`. The published guidance says it defaults to
`True` and recommends keeping it on: merge proposes a candidate combining two Pareto-frontier
parents that win on *different* examples, at a cost of one re-evaluation per attempt, capped
by `max_merge_invocations` (default 5).

The reason to care: **GEPA+Merge is reported to produce prompts up to 9.2× shorter while
scoring higher.** Prompt length is the mechanism we hypothesised and could not support from
our own arm-level data (`r` flipped sign by cohort). Merge is the intervention the literature
actually backs, it is off, and it is one argument.

### `seed` — reachable, and it would not do what you want

The obvious use is campaign 07's **12.3× run-to-run spread**, which `num_runs` cannot touch
because it averages the evaluation of one optimized prompt, not the choice of prompt.

**A seed will not make our runs reproducible, and DOE 02 is why.** The judge is
non-deterministic on byte-identical responses — it disagrees with itself on **64/64 cases**
for `instruction_following_v1`. GEPA's selection is driven by those scores, so two
seed-identical runs diverge at the first Pareto comparison regardless. Fixing the seed
removes one source of divergence and leaves the dominant one.

Worth wiring for the partial determinism, worth **not** expecting reproducibility from.
`score_repeats` (shipped 2026-09-17) attacks the actual term.

### `cache_evaluation` — measured, and it is 3%

The docstring promises it "saves metric calls", which on a judge-bound stage sounds like the
biggest lever here. Measured against the surviving run instead of assumed:

```
  minibatch (candidate, example) evaluations : 159
  distinct pairs                             : 143
  redundant, i.e. cacheable                  :  16  (10% of minibatch, 3% of 468 total)
```

Take it — it is free and `evaluation_cache` is `None` today — but it is not a lever.

### `max_reflection_cost` — a spend cap we do not have

Undocumented in the signature, but it caps the expensive half directly. With the writer now
on `claude-opus-4-8` at $5/$25 per 1M, a cap is cheap insurance against a pathological run.

### The rest, briefly

**Search behaviour.** `candidate_selection_strategy='pareto'` and `skip_perfect_score=True`
are already the validated defaults — Pareto beats select-best by up to 8.17%, so leave them.
`frontier_type`, `batch_sampler`, `module_selector`, `acceptance_criterion` are tuning
surface with no evidence we need them.

**Reflection / proposal.** `reflection_prompt_template` and `custom_candidate_proposer` are
the length-regularization hooks. **We looked and the premise did not hold** — see
[the survey](2026-09-16-writer-model-survey.md). Do not reach for these before the rationale
fix, which is upstream of them: constraining a blind proposer's output length is treating the
symptom.

**Observability.** 13 arguments, and the need they addressed is largely met: since
2026-09-16 the pipeline preserves GEPA's `run_dir`, which carries candidates, per-candidate
scores and the search tree. `callbacks`/`logger` would buy live progress during a nine-hour
stage; nice, not needed.

---

## Recommendations, in order

1. **Forward the rubric rationale to the reflector.** Biggest by some distance, fixable
   locally, and the one change with a mechanism for both of our standing observations. Patch
   `_extract_eval_data` to include `details.rubric_scores[].rationale`. **Measure it the way
   DOE 02 measured everything else** — the acceptance test is the holdout delta on a real
   optimize stage, not that the text appears.
2. **Turn `use_merge` on.** One argument, off by default here, and the only knob the
   literature ties to shorter prompts and better scores.
3. **Wire `seed` and `max_reflection_cost`** while touching the config — both cheap. Record
   in the same commit that the seed does **not** buy reproducibility while the judge is
   non-deterministic, or someone will expect it to.
4. **Set `cache_evaluation=True`** and expect ~3%.
5. **Leave the other 33 alone.** The defaults are the validated ones, and the observability
   block is already covered by preserving `run_dir`.

**Sequencing matters here.** 1 and 2 both plausibly act on prompt length and quality, so
shipping them together would confound them. The rationale fix first, measured; then merge.

## Sources

- [gepa-ai/gepa](https://github.com/gepa-ai/gepa) · [gepa.optimize() API](https://mintlify.wiki/gepa-ai/gepa/api/optimize) · [GEPA guides](https://gepa-ai.github.io/gepa/guides/)
- [DSPy GEPA overview](https://dspy.ai/api/optimizers/GEPA/overview/) · [GEPA in depth](https://dspy.ai/diving-deeper/gepa-in-depth/) · [GEPA advanced](https://dspy.ai/api/optimizers/GEPA/GEPA_Advanced/)
- [GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning](https://arxiv.org/abs/2507.19457)
