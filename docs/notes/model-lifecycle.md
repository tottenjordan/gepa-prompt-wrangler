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

This note no longer needs to list where ids are hardcoded — the guard does that. What is
**not** covered by the guard, and still holds literal ids:

- `manifests/*.yaml`, `templates/*/manifest.yaml`, `experiments/active/**`
- `examples/multi_model_agents/agents/*_opt/sampler_config.json` — 6 files, 2 judge
  values each
- `examples/multi_model_agents/config.py` — the near-duplicate of `wrangler/core/config.py`

Those are data, not code, and a migration there re-scores experiments. As of 2026-08-20
they still name `gemini-2.5-flash` (13 sites) and `gemini-2.5-pro` (4 sites).

## ⚠️ Gemini 2.5 retires 2026-10-16 — and it is still the judge

`gemini-2.5-flash`, `gemini-2.5-pro`, and `gemini-2.5-flash-lite` retire on
**2026-10-16** on Vertex AI / Agent Platform. Retired Vertex model IDs return **404** —
a hard failure, not a soft degrade.

- Google publishes two dates: 2026-10-16 for the Gemini Developer API, 2026-10-20 for
  Agent Platform. The registry records the earlier one.
- Google calls these "earliest possible" with at least six months of notice, and the real
  date tracks Gemini 3 GA. Re-verify on the
  [lifecycle page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions).

**Blast radius.** `DEFAULT_JUDGE_MODEL` is still `gemini-2.5-flash` and
`DEFAULT_JUDGE_ENSEMBLE` is still `["gemini-2.5-pro", "gemini-2.5-flash"]`. When they
404, GEPA optimization and the multi-judge ensemble stop — not just agent inference.
(Batch eval is the exception: it never sends a judge id, see
[adk-judge-model.md](adk-judge-model.md).) 2.5-flash is also ADK 2.7.1's *own* built-in
default for `JudgeModelOptions` and `GEPARootAgentPromptOptimizerConfig.optimizer_model`,
so after the date anything that does not set the judge explicitly breaks.

`test_default_models_are_not_near_retirement` turns this into a red build on
**2026-09-16**, 30 days ahead of the shutdown.

### Why the judge has not moved yet

Swapping the judge re-scores every experiment; reports from before and after are not
comparable. That is a measurement decision, not a refactor, so it is deliberately
deferred to an A/B run against a deployed agent (Task 3.2b in the plan) rather than done
blind. Two things worth knowing before that run:

- **A 3.x judge is not actually unproven here.** `DEFAULT_MANIFEST_JUDGE_MODEL` — the
  judge used when a manifest's `eval_config` omits one, and what `wrangler init` writes —
  is already `gemini-3.5-flash`. One scoring path has been running on 3.x.
- **The scaffold judge already moved.** `DEFAULT_SCAFFOLD_JUDGE_MODEL` went
  `gemini-2.5-pro` → `gemini-3.1-pro-preview` on 2026-08-20. It is emitted into config
  files that do not exist yet, so there was no baseline to keep comparable and no reason
  to wait.

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
- **Score re-baselining.** Changing the judge changes every GEPA and batch-eval score.
  Archive the before-state or keep a pinned 2.5 lane if old experiments must reproduce.
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

- **Judge A/B** (plan Task 3.2b) — needs two live `wrangler eval` runs against deployed
  agents. Watch for `RUBRIC MATCH FAILURE`, `None` scores, and drift that flips pass/fail.
- **Manifest / sampler-config migration** (Task 3.3) — blocked on the A/B picking a value.
- **Smoke-test re-baseline** (Task 3.4) — `uv run wrangler run manifests/pipeline_smoke_manifest.yaml`,
  5 cases, ~25-30 min, real spend. Record the post-migration scores here when it runs;
  there is no post-migration baseline in this note yet.
