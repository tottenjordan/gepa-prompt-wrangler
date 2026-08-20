# Model Lifecycle & IDs

**Verified on:** 2026-08-20.

Why this note exists: the models this repo depends on have hard shutdown dates, and the
consequences of each migration are not visible from the code.

Related: [repo-traps.md](repo-traps.md), [adk-patch-status.md](adk-patch-status.md),
[adk-judge-model.md](adk-judge-model.md).

---

## The registry exists now

`wrangler/core/models.py` is the single source of truth: provider, cost, RPM, retirement
date, sampling-parameter support, and alias per model, plus the named-role defaults.
`tests/test_models.py` fails the build if a model id literal appears anywhere in
`wrangler/` outside it, if a default names an unregistered model, or if a default comes
within 30 days of retiring.

This note no longer needs to list where ids are hardcoded — the guard does that. The
guard only walks `wrangler/`, so everything below was migrated by hand on 2026-08-20 and
nothing enforces it staying migrated:

- `manifests/*.yaml` (3 judge values), `templates/*/manifest.yaml` (2)
- `examples/multi_model_agents/agents/*_opt/sampler_config.json` — 6 files, **2 judge
  values each**, so 12. A one-per-file sweep silently does half the job.
- `examples/multi_model_agents/config.py` (`SIMULATOR_MODEL`) and `.env.example`

### Two places deliberately left on Gemini 2.5

Both are **historical records**, and migrating them would falsify the record rather than
fix anything:

- `examples/multi_model_agents/prompts/*_prompts.py` — 30 `"judge_model"` entries. These
  are provenance written by `wrangler/tools/prompt_registry.py:70`: each saved prompt
  version records *the judge that produced it*. Rewriting them would claim `wrangler_v1`
  was optimized under a judge that did not exist when it ran.
- `experiments/active/multi-model-v6/`, `-v7/`, `manifest-v7.yaml` — 6 values. All five
  stages (`deploy`, `eval_before`, `optimize`, `redeploy`, `eval_after`) are complete with
  reports and charts committed. The config is the record of what produced them.

The consequence to accept: **re-running either will 404 after 2026-10-16.** That is the
correct trade — a stale archive is honest, a rewritten one is not.

## Gemini 2.5 retires 2026-10-16 — judge migrated 2026-08-20

`gemini-2.5-flash`, `gemini-2.5-pro`, and `gemini-2.5-flash-lite` retire on
**2026-10-16** on Vertex AI / Agent Platform. Retired Vertex model IDs return **404** —
a hard failure, not a soft degrade.

- Google publishes two dates: 2026-10-16 for the Gemini Developer API, 2026-10-20 for
  Agent Platform. The registry records the earlier one.
- Google calls these "earliest possible" with at least six months of notice, and the real
  date tracks Gemini 3 GA. Re-verify on the
  [lifecycle page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions).

**Blast radius, before the migration.** `DEFAULT_JUDGE_MODEL` was `gemini-2.5-flash` and
`DEFAULT_JUDGE_ENSEMBLE` was `["gemini-2.5-pro", "gemini-2.5-flash"]`. When those 404,
GEPA optimization stops — not just agent inference. 2.5-flash is also ADK 2.7.1's *own*
built-in default for `JudgeModelOptions` and
`GEPARootAgentPromptOptimizerConfig.optimizer_model`, so after the date anything that
does not set the judge explicitly breaks.

`test_default_models_are_not_near_retirement` would have turned this into a red build on
**2026-09-16**, 30 days ahead of the shutdown. The migration below landed first.

## Judge A/B, 2026-08-20 — the re-baseline

**Chosen: `gemini-3.5-flash`,** replacing `gemini-2.5-flash` as `DEFAULT_JUDGE_MODEL`.

### The plan's measurement method could not have worked

Task 3.2b proposed A/B-ing by running `wrangler eval` twice with different judges in the
sampler config. That measures nothing: `wrangler/eval/evaluator.py` **deliberately omits
`judge_model`** because `create_evaluation_run()` wants a full autorater *resource name*
and rejects a bare model id with `INVALID_ARGUMENT`. Batch eval always uses the service
default autorater. **The sampler config's `judge_model` only affects the GEPA path** —
so both runs would have produced identical scores and "proved" the judges equivalent.

What was run instead: the two GEPA-side evaluators
(`RubricBasedFinalResponseQualityV1Evaluator`, `RubricBasedToolUseV1Evaluator`) driven
directly with `lite_opt`'s real rubrics, `num_samples=5` (the production default), over 6
eval cases spanning search/booking/expense/planning/error-handling/policy. Each case was
scored in two variants — the golden trajectory, and a *degraded* one (no tool calls, "I'm
not sure" answer) — because a judge that scores good answers well is worthless if it also
scores bad answers well. Inputs were byte-identical across judges.

| Judge | FRQ good | FRQ degraded | TUQ good | TUQ degraded | rubric-match failures | `None` scores | errors |
|---|---|---|---|---|---|---|---|
| `gemini-2.5-flash` | 0.917 | 0.333 | 1.000 | 0.500 | 0 | 0 | 0 |
| `gemini-3.6-flash` | 1.000 | 0.500 | 1.000 | 0.417 | 0 | 0 | 0 |
| `gemini-3.5-flash` | 1.000 | 0.500 | 0.917 | 0.500 | 0 | 0 | 0 |

**The thought-leakage risk did not materialize.** Zero rubric-match failures, zero `None`
scores, zero errors on either Gemini 3 judge. The concern from
[adk-judge-model.md](adk-judge-model.md) — that `llm_as_judge_utils.py:88` does not filter
thought parts — stays theoretical because the judge builds a bare
`GenerateContentConfig()` and never asks for thoughts. Re-measure if that changes.

**2.5-flash was the *least* reliable of the three**, which was not the expected result.
It produced both a false negative (`case_11_low_expense`, golden response scored
`completeness: 0.0`) and a false positive (`case_7_low_policy`, the deliberately useless
"Sorry, I'm not sure" answer scored a full 1.0/1.0). Both Gemini 3 judges were perfectly
consistent across all 6 cases in each variant.

### Why 3.5-flash and not the plan's recommended 3.6-flash

The plan said pin `gemini-3.6-flash`, reasoning that "this repo's whole purpose is
comparing prompt variants across runs, and a self-updating judge undermines that." That
reasoning is right and it disqualifies 3.6 too — the plan just did not know yet that 3.6
is on the **short-term availability track**: it retires 45 days after a replacement ships,
with no date published in advance. That is `gemini-flash-latest`'s defect on a slower
fuse. `gemini-3.5-flash` does not shut down before **2027-05-19**.

Two things fall out for free:

- **The two scoring judges now agree.** `DEFAULT_MANIFEST_JUDGE_MODEL` was already
  `gemini-3.5-flash`, so the repo had been judging the GEPA path with 2.5-flash and the
  manifest path with 3.5-flash — an inconsistency nobody chose, only visible once the
  constants were named.
- The measured discrimination gap (good minus degraded, summed over both metrics) is
  1.00 for 3.5-flash, 1.08 for 3.6, 1.08 for 2.5 — statistically indistinguishable at
  n=6, and 2.5's edge comes from noise in both directions rather than sharper judgement.

Cost is the trade: 3.5-flash is $1.50/$9.00 against 3.6's $0.75/$3.75. From 2027-01-01
3.6 rises to $1.50/$7.50 and the input-side gap disappears entirely.

### Two findings that outlived the A/B

- **The registry's 5 RPM for Gemini 3.x is far too conservative for this project.** The
  probe sustained ~40 req/min against `gemini-3.5-flash` at concurrency 6 with zero 429s
  (120 judge calls in 177s). This matters because `get_batch_config()` keys off `rpm`, and
  at the recorded 5 it drops every Gemini 3 judge from `(16, 5.0s, 10)` to
  `(4, 15.0s, 4)` — roughly 4x slower judging. The plan pinned this warning on
  `gemini-3.1-flash-lite` specifically, but **every** Gemini 3.x entry in the registry is
  at 5 RPM, so it applied to any migration off 2.5. Worth re-measuring against real
  project quota and correcting the registry.
- **`correct_parameters` is vacuously true when the agent calls no tools.** Both judges,
  independently, score it 1.0 for a trajectory with zero tool calls — no parameters means
  no wrong parameters. That floors `tool_use_quality` at 0.5 for a completely
  non-functional agent, and it is a rubric-design flaw in every `sampler_config.json`, not
  a judge artifact. Fixing it changes what GEPA optimizes against, so it is left alone
  here and noted for a deliberate decision.

### The scaffold judge and the ensemble

- `DEFAULT_SCAFFOLD_JUDGE_MODEL` went `gemini-2.5-pro` → `gemini-3.1-pro-preview`. Emitted
  into config files that do not exist yet, so there was no baseline to keep comparable.
- `DEFAULT_JUDGE_ENSEMBLE` went to `["gemini-3.1-pro-preview", "gemini-3.5-flash"]`,
  keeping the pro-tiebreaker + flash shape. Not A/B-tested: nothing outside `tests/`
  imports `wrangler.optimize.multi_judge`, so the ensemble is dormant. Migrated anyway,
  because it retires on the same date as everything else.

### Successors

| Retiring | Successor per Google's deprecation table |
|----------|------------------------------------------|
| `gemini-2.5-flash` | `gemini-3.6-flash` |
| `gemini-2.5-pro` | `gemini-3.1-pro-preview` |
| `gemini-2.5-flash-lite` | `gemini-3.1-flash-lite` |

**Do not take `gemini-3.6-flash` at face value as the default.** It is GA (2026-07-21)
and both cheaper and newer than `gemini-3.5-flash`, but it is on the **short-term
availability track**: it retires 45 days after a replacement ships, with no date
published in advance. That is the wrong property for a framework default whose job is to
make runs comparable over time. `DEFAULT_AGENT_MODEL` is `gemini-3.5-flash` (stable, not
before 2027-05-19); pick 3.6 explicitly in a manifest when the cost matters more. Its
price also rises $0.75/$3.75 → $1.50/$7.50 on 2027-01-01, which erases the advantage.

`gemini-3.1-pro-preview` is a *preview* ID — expect churn.

### Migration is not a string swap

- **Thought signatures.** Gemini 3 models emit encrypted reasoning-state blobs that must
  be preserved across multi-turn tool-calling. Handled by the GenAI SDK's chat surface; a
  concern anywhere interactions are assembled manually.
- **Pricing.** Gemini 3 is more token-efficient but costs more per token. Net cost change
  depends on workload — the registry's cost figures need real re-measurement, not
  arithmetic.
- **Score re-baselining.** Changing the judge changes every GEPA score. It does **not**
  change batch-eval scores — batch eval cannot send a judge id at all, so `eval_before` /
  `eval_after` numbers stay comparable across the migration. Only the optimization signal
  moved.
- **Alias hot-swapping.** Unversioned/`-latest` IDs get repointed silently, and Google has
  repointed a dated-looking preview ID after shutdown. Prefer a pinned successor over
  `gemini-flash-latest` for reproducibility.

## Claude on Vertex

Vertex uses **bare, dateless** IDs (no `anthropic.` prefix; that is Bedrock). The whole
Claude 5 family is registered as of 2026-08-20 — Opus 5, Sonnet 5, and Fable 5 alongside
Sonnet 4.6 and Opus 4.6/4.7/4.8. Claude Haiku 4.5 (`claude-haiku-4-5@20251001`) is not,
because nothing in the repo runs it.

### Sampling parameters are deprecated from Opus 4.7 onward

`temperature`, `top_p`, and `top_k` return a **400** on a non-default value for **Claude
Opus 4.7 and later**. The cutoff is the model *generation*, not the Opus tier: Sonnet 5
and Fable 5 are affected, Opus 4.6 is not. Getting that boundary wrong is a runtime 400
on a deployed agent.

Two corrections to what this note previously said:

- The claim that `wrangler/core/factory.py` "sets temperature unconditionally" and would
  break was wrong. `AgentPromptPair.temperature` is parsed and carried but never passed
  to a deployed agent — `grep -rn "\.temperature\b\|temperature=" --include=*.py .`
  returns the assignment and nothing else. There was no live 400 risk.
- The field is still worth guarding, because manifests document it and a future wiring-up
  would fail obscurely. `PairFactory.load()` now rejects a non-default temperature on a
  model with `supports_sampling_params=False`, naming the offending pair. An unregistered
  model id gets the benefit of the doubt — the registry cannot know about a model Vertex
  added yesterday, and refusing to load is worse than letting the API decide.

### Other Claude gotchas

- Claude 5 models require **provider data sharing** on Vertex
  (`PublisherModelConfig.data_sharing_enabled_provider`), otherwise requests fail 403.
- Claude models must be enabled explicitly in Model Garden.
- Newer Claude models use **global or multi-region** endpoints (`us`, `eu`); specific
  regional endpoints like `us-east5` only go up to Sonnet 4.6. Regional/multi-region
  carries roughly a 10% premium over global for Claude 4.5+.
- Anthropic's retirement dates are "not sooner than" and apply to Anthropic-operated
  platforms. Google Cloud sets its own schedule for partner models — treat the dates in
  the registry as an early-warning floor, not a contract.

## Pricing figures were wrong before 2026-08-20

The pre-registry cost table listed `gemini-2.5-flash` at $0.15/$0.60. Verified list price
is **$0.30/$2.50** — output was off by ~4×, which silently understated the cost of every
report that used it. Sources:
[Gemini](https://ai.google.dev/gemini-api/docs/pricing),
[Claude](https://platform.claude.com/docs/en/about-claude/pricing).

Do not trust a cost figure in this repo's prose without re-checking it against those two
pages. `WebFetch` on `cloud.google.com/vertex-ai/generative-ai/pricing` truncates and its
summarizer has an older knowledge cutoff — it will confidently report current model names
as fabricated. Fetch `ai.google.dev` directly instead.

## Not yet done

- **End-to-end smoke test** (plan Task 3.4) — `uv run wrangler run
  manifests/pipeline_smoke_manifest.yaml`, 5 cases, ~25-30 min, deploys to GEAP and spends
  real money. The A/B above validated the judge in isolation; it did **not** exercise
  deploy → eval → GEPA → redeploy → eval. Until it runs, the migration is verified at the
  unit of the judge, not of the pipeline.
- **Re-measure Gemini 3.x RPM against real project quota** and correct the registry's `5`.
  See the finding above — it currently makes `get_batch_config()` throttle 4x harder than
  necessary for every Gemini 3 judge.
- **Decide on the vacuous `correct_parameters` rubric.** Changing it changes GEPA's
  optimization target, so it wants an explicit decision rather than a drive-by fix.
