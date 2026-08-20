# ADK Judge Model Constraints

**Verified on:** 2026-08-20 against `google-adk==2.7.1` (installed in `.venv`) and
`https://adk.dev/evaluate/criteria/`.

Why this note exists: the intuition "the ADK judge model has to stay on Gemini 2.5" is
common and *almost* right — but for a different reason than it sounds. There is no ADK
constraint. The real risks are a defaulting pattern and one unguarded parser. Both are
invisible from the config surface.

Related: [model-lifecycle.md](model-lifecycle.md),
[adk-patch-status.md](adk-patch-status.md).

---

## Question: does ADK require a Gemini 2.5 judge?

**No.** Nothing in ADK restricts the judge model.

`JudgeModelOptions.judge_model` (`google/adk/evaluation/eval_metrics.py:84-89`) is a
plain `str` — no validator, no `Literal`, no enum, no allowlist:

```python
class JudgeModelOptions(EvalBaseModel):
  judge_model: str = Field(
      default="gemini-2.5-flash",
      description="The judge model to use for evaluation. It can be a model name.",
  )
```

`_setup_auto_rater()` (`google/adk/evaluation/llm_as_judge.py:203-207`) resolves it
through the generic registry:

```python
def _setup_auto_rater(self) -> BaseLlm:
  model_id = self._judge_model_options.judge_model
  llm_registry = LLMRegistry()
  llm_class = llm_registry.resolve(model_id)
  return llm_class(model=model_id)
```

Resolution verified empirically at 2.7.1:

| Judge id | Resolves to |
|----------|-------------|
| `gemini-2.5-flash` | `google_llm.Gemini` |
| `gemini-3.5-flash` / `gemini-3.6-flash` | `google_llm.Gemini` |
| `gemini-3.1-flash-lite` / `gemini-3.1-pro-preview` | `google_llm.Gemini` |
| `claude-opus-5` / `claude-sonnet-5` | `anthropic_llm.Claude` |

`Gemini.supported_models()` is `['gemini-.*', 'gemma-4.*', 'model-optimizer-.*', ...]` —
version-agnostic. **Google's own docs use `"judge_model": "gemini-flash-latest"` in every
example config** on the criteria page, state no restriction, and give no model-selection
guidance at all.

## But the instinct is half right — two real reasons for caution

### 1. ADK 2.7.1 is mid-migration and the GEPA path stayed on 2.5

Defaults are inconsistent across the installed tree:

| Location | Default |
|----------|---------|
| `evaluation/eval_metrics.py:86` `JudgeModelOptions.judge_model` | `gemini-2.5-flash` |
| `optimization/gepa_root_agent_prompt_optimizer.py:53` `optimizer_model` | `gemini-2.5-flash` |
| `optimization/gepa_root_agent_optimizer.py:109` | **`gemini-3.5-flash`** |

The newer *root agent* optimizer moved to 3.5; the **prompt** optimizer this repo uses
did not. So the GEPA prompt-optimization path is the least-exercised-on-Gemini-3 corner
of ADK. That is a testing-surface argument, not a hard constraint — but it means "nobody
upstream has proven this yet."

### 2. The judge response parser does not filter thought parts

`google/adk/evaluation/llm_as_judge_utils.py:88-89`:

```python
if content and content.parts:
  return "\n".join([p.text for p in content.parts if p.text])
```

No `part.thought` check. Compare `simulation/llm_backed_user_simulator.py:174`, which
does filter: `if part.text and not part.thought:`. So if a thinking-heavy judge returns
thought parts, reasoning prose gets concatenated into the string handed to the strict
Property / Rationale / Verdict parser. Gemini 3 is thinking-heavy by default, so this is
the specific way a Gemini 3 judge could fail — and it would fail as *silently wrong
scores*, not an exception.

**Mitigating factor:** the judge builds a bare `GenerateContentConfig()`
(`llm_as_judge.py:167`) with no `include_thoughts=True`, so thought parts should not be
returned in the first place. This is a risk to **measure**, not a proven blocker. The
GEPA optimizer's own `model_configuration`, by contrast, *does* default to
`ThinkingConfig(include_thoughts=True, thinking_budget=10240)` — different code path,
but it shows thoughts are actively in play nearby.

Detection signal: `RUBRIC MATCH FAILURE` warnings (from ADK Patch 4's instrumentation),
ADK's own `Rubric ... not found in the rubrics provided to the metric`, or rubric scores
coming back `None`.

## Scope: this is the GEPA/sampler side only

Batch eval is **unaffected**. `wrangler/eval/evaluator.py:107-110` deliberately omits
`judge_model` because the `evaluation_run` API requires a full autorater *resource name*
and rejects a bare model id with `INVALID_ARGUMENT`. Batch eval therefore uses the
service's default autorater no matter what this repo configures.

So the judge model decision touches exactly one surface: GEPA optimization via
`sampler_config.json`.

## Repo state

All six `examples/multi_model_agents/agents/*_opt/sampler_config.json` pin
`"judge_model": "gemini-2.5-flash"` — **twice each** (roughly lines 8 and 30), once per
metric block. A migration must change 12 values, not 6.

## The deadline makes "stay on 2.5" temporary

Staying on `gemini-2.5-flash` is defensible **only until 2026-10-16**, when it retires
and returns 404 (see [model-lifecycle.md](model-lifecycle.md)). After that, ADK's own
built-in default is broken for everyone, not just this repo. So the choice is not
"migrate or don't" — it is "migrate on a measured schedule, or migrate in an outage."

**Recommended posture:** pin a successor (`gemini-3.6-flash`) rather than ride
`gemini-flash-latest`. This repo exists to compare prompt variants across runs, and a
self-updating judge silently re-scores history. Validate by A/B-running the same evalset
under both judges and checking for rubric match failures before adopting — the procedure
is Task 3.2b in
[../plans/2026-08-20-repo-modernization.md](../plans/2026-08-20-repo-modernization.md).
