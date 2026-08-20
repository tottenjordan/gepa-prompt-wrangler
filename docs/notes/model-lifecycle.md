# Model Lifecycle & IDs

**Verified on:** 2026-08-20.

Why this note exists: model IDs are scattered across ~30 files with no single registry,
and the ones this repo depends on have a hard shutdown date. Neither fact is visible
from the code.

Related: [repo-traps.md](repo-traps.md), [adk-patch-status.md](adk-patch-status.md),
[adk-judge-model.md](adk-judge-model.md).

---

## ⚠️ Gemini 2.5 retires 2026-10-16

`gemini-2.5-flash`, `gemini-2.5-pro`, and `gemini-2.5-flash-lite` are scheduled for
retirement on **2026-10-16** on Vertex AI / Agent Platform. Retired Vertex model IDs
return **404** — this is a hard failure, not a soft degrade.

Caveats worth knowing:

- Google's own pages disagree: release notes say **Oct 16**, the model-lifecycle page
  says **Oct 20**. Plan against the earlier date.
- Google describes these as "earliest possible" dates with at least six months of notice
  before actual shutdown, and the real date tracks Gemini 3 GA. Treat Oct 16 as the
  planning date and re-verify on the
  [lifecycle page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions).

**Blast radius in this repo:** `gemini-2.5-flash` is the default judge model. When it
404s, GEPA optimization and the multi-judge ensemble stop working — not just agent
inference. (Batch eval is the exception: it never sends a judge id, see
[adk-judge-model.md](adk-judge-model.md).) It is also ADK 2.7.1's *own* built-in default
for `JudgeModelOptions` and `GEPARootAgentPromptOptimizerConfig.optimizer_model`, so
after the retirement date anything that does not set the judge explicitly breaks.

**The judge model deserves separate treatment from the agent model.** There is no ADK
constraint pinning it to 2.5, but changing it silently re-scores every experiment and
there is an unguarded thought-part parser in the judge path. See
[adk-judge-model.md](adk-judge-model.md) for the evidence and the validation procedure.

### Successors

| Retiring | Successor per Google's deprecation table |
|----------|------------------------------------------|
| `gemini-2.5-flash` | `gemini-3.6-flash` |
| `gemini-2.5-pro` | `gemini-3.1-pro-preview` |
| `gemini-2.5-flash-lite` | `gemini-3.1-flash-lite` |

Note the repo's cost table lists `gemini-3.5-flash`, which the deprecation table has
already superseded with `gemini-3.6-flash`.

### Migration is not a string swap

- **Thought signatures.** Gemini 3 models emit encrypted reasoning-state blobs that must
  be preserved across multi-turn tool-calling. Handled by the GenAI SDK's chat surface;
  a concern anywhere interactions are assembled manually.
- **Pricing.** Gemini 3 is more token-efficient but costs more per token. Net cost
  change depends on workload — the `MODEL_COSTS` table needs real re-measurement, not
  arithmetic.
- **Score re-baselining.** Changing the judge model changes every GEPA and batch-eval
  score. Reports produced before and after are not comparable. Archive the before-state
  or keep a pinned 2.5 lane if old experiments must be reproducible.
- **Alias hot-swapping.** Unversioned/`-latest` IDs get repointed silently, and Google
  has repointed a dated-looking preview ID after shutdown. Decide deliberately between a
  pinned ID and auto-migration. `gemini-3.1-pro-preview` is a *preview* ID — expect churn.

## Claude on Vertex — current IDs

Vertex uses **bare, dateless** IDs (no `anthropic.` prefix; that is Bedrock).

| Model | Vertex ID | In repo? |
|-------|-----------|----------|
| Claude Opus 5 | `claude-opus-5` | **No** |
| Claude Sonnet 5 | `claude-sonnet-5` | **No** |
| Claude Fable 5 | `claude-fable-5` | Yes |
| Claude Haiku 4.5 | `claude-haiku-4-5@20251001` | No |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | Yes |
| Claude Opus 4.6/4.7/4.8 | `claude-opus-4-6` … `-4-8` | Yes |

The repo is a generation behind on Claude: it carries Opus 4.6/4.7/4.8 and Sonnet 4.6
but not Opus 5 or Sonnet 5, despite already having Fable 5.

Gotchas when adding the Claude 5 family:

- **Opus 5 deprecates `temperature`, `top_p`, and `top_k`.** `wrangler/core/factory.py:19`
  and `:94` set `temperature` (default `1.0`) unconditionally. That needs to become
  model-aware before Opus 5 is wired in.
- Claude 5 models require **provider data sharing** on Vertex
  (`PublisherModelConfig.data_sharing_enabled_provider`), otherwise requests fail 403.
- Claude models must be enabled explicitly in Model Garden.
- Newer Claude models use **global or multi-region** endpoints (`us`, `eu`); specific
  regional endpoints like `us-east5` only go up to Sonnet 4.6. Regional/multi-region
  carries roughly a 10% premium over global for Claude 4.5+.
- Retirement: Sonnet 5 not sooner than 2026-12-24; Opus 4.6 not sooner than 2027-02-05.

## Where model IDs are hardcoded

No single source of truth. As of 2026-08-20, `gemini-2.5-*` alone appears in 30+ files:

- `wrangler/core/config.py` — `MODEL_COSTS`, `RATE_LIMITS`, `resolve_model()`
- `examples/multi_model_agents/config.py` — a **near-duplicate** of the above
- `examples/multi_model_agents/agents/*/sampler_config.json` — 6 files, judge models
- `manifests/*.yaml`, `templates/*/manifest.yaml`
- `wrangler/optimize/multi_judge.py`, `wrangler/eval/evaluator.py`,
  `wrangler/core/converter.py`, `wrangler/tools/prompt_registry.py`,
  `wrangler/tools/inspector.py`, `wrangler/orchestration/stages.py`,
  `wrangler/pipeline/dag.py`, `wrangler/pipeline/deploy_pipeline.py`
- `examples/multi_model_agents/prompts/*.py`
- `tests/test_evaluator.py`, `tests/test_converter.py`, `tests/test_pipeline.py`

`resolve_model()` routes on **string prefix** (`gemini-2` → plain string, `claude` →
`Claude()`, else → `Gemini()`). Adding a model family means editing that prefix logic,
not just a table.
