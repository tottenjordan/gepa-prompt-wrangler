# Vertex AI Gen AI Evaluation Service — Silent Partial-Metric Degradation (Escalation)

**Status:** OPEN — service-side incident, not client-fixable
**Filed by:** prompt-wrangler team
**Project:** `hybrid-vertex`
**Region:** `us-central1`
**First observed:** ~15:18 UTC 2026-06-26 · **Still reproducing:** 2026-07-01 (≥5 days)
**Severity:** High — the service reports `SUCCEEDED` while silently dropping 3 of 5 requested metrics, so downstream consumers compute plausible-but-wrong aggregates unless they add their own completeness check.

---

## Summary

The Vertex AI Gen AI Evaluation Service (`client.evals.create_evaluation_run`) has, since ~15:18 UTC on 2026-06-26, been returning only a **subset** of the requested metrics while the evaluation run still reports terminal state `EvaluationRunState.SUCCEEDED`. There is no error, no `FAILED` state, and no top-level warning — the dropped metrics are simply absent from the aggregate results and appear per-case as `EvalCaseMetricResult` entries carrying an `error_message` with `score=None`.

We request confirmation of a service-side incident (likely an autorater/judge-model availability or sunset in `us-central1`) and an ETA for recovery.

---

## Exact behavior

We request **5 metrics** per run:

| Metric | Type | Result since 06-26 |
|---|---|---|
| `final_response_quality_v1` | predefined `RubricMetric.FINAL_RESPONSE_QUALITY` | **DROPPED** |
| `hallucination_v1` | predefined `RubricMetric.HALLUCINATION` | **DROPPED** |
| `tool_use_quality` (custom) | custom `LLMMetric` w/ explicit prompt | **DROPPED** |
| `safety_v1` | predefined `RubricMetric.SAFETY` | survives |
| `instruction_following_v1` | predefined `RubricMetric.INSTRUCTION_FOLLOWING` | survives |

The drop set (`final_response_quality_v1` + `hallucination_v1` + custom `tool_use`) and the survivor set (`safety_v1` + `instruction_following_v1`) are **100% consistent** across every run and every engine we have tested. Note this is **not** a predefined-vs-custom split — `final_response_quality` and `hallucination` are predefined `RubricMetric`s yet are dropped, while the other two predefined metrics survive.

---

## Decisive controlled probe (2026-07-01)

To rule out that the drop is specific to our agents, models, or manifest config, we re-evaluated the **same 3 eval cases** with the **same 5-metric request**, varying **only the engine** — re-running engines that had *previously returned all 5 metrics* before the cutover:

| Engine (role) | Reasoning Engine ID | Metrics before 06-26 | Metrics on 07-01 |
|---|---|---|---|
| lite-core (Gemini / core prompt) | `5407968282181369856` | 5/5 | **2/5** |
| opus48-bare (Claude / bare prompt) | `957848900385898496` | 5/5 | **2/5** |
| lite-bare (Gemini / bare prompt) | `7757466301064282112` | 2/5 (post-cutover) | **2/5** |

Engines that returned all 5 metrics a week ago now return 2 with **no change on our side** (same code, same cases, same metric request). This proves the regression is **service-wide and time-based**, not:
- model-specific (both Gemini and Claude engines affected),
- prompt/cohort-specific (both "core" and "bare" prompt variants affected),
- config-specific (identical request, only the engine varies).

---

## Timeline

- **Runs #1–7** of our sweep (all before ~15:18 UTC 2026-06-26): returned **5/5** metrics.
- **Runs #8–10** (pro-bare, flash-bare, lite-bare eval-before, starting ~15:18 UTC 2026-06-26): returned **2/5**.
- The clean→broken boundary in our sweep coincidentally aligned with a Claude→Gemini run-ordering boundary, which initially looked like a model correlation; the controlled probe above eliminated that confound.
- **2026-07-01:** still 2/5 for all engines tested. ≥5 days of continuous degradation.

---

## Reproduction

```python
from vertexai import Client, types
import vertexai

vertexai.init(project="hybrid-vertex", location="us-central1",
              staging_bucket="gs://<bucket>")
client = Client(project="hybrid-vertex", location="us-central1")

engine = "projects/hybrid-vertex/locations/us-central1/reasoningEngines/5407968282181369856"
df = client.evals.run_inference(agent=engine, src=<3 eval cases>)

run = client.evals.create_evaluation_run(
    dataset=types.EvaluationDataset(eval_dataset_df=df.eval_dataset_df),
    agent=engine,
    metrics=[
        types.RubricMetric.FINAL_RESPONSE_QUALITY,   # dropped
        types.RubricMetric.HALLUCINATION,            # dropped
        types.RubricMetric.SAFETY,                   # survives
        types.RubricMetric.INSTRUCTION_FOLLOWING,    # survives
    ],
    dest="gs://<bucket>/eval-results",
    labels={"solution": "promp-wrangler"},
)
# run.state -> SUCCEEDED, but aggregate results contain only safety_v1 +
# instruction_following_v1. final_response_quality_v1 and hallucination_v1
# are absent from aggregates; per-case they appear as EvalCaseMetricResult
# with error_message set and score=None.
```

---

## Why this is not client-fixable

For **predefined** metrics the SDK intentionally does not pass `autorater_config` / `judge_model` — the server uses its own judge model configuration and ignores the field (`vertexai/_genai/_evals_metric_handlers.py:1039-1041`: *"autorater_config is intentionally not passed for predefined metrics. The server uses its own model configuration for predefined metrics and ignores the autorater_config field."*). So we cannot redirect `final_response_quality_v1` / `hallucination_v1` to a different, available judge from the client. This points squarely at a server-side judge/autorater availability problem in `us-central1`.

The failing predefined metrics **are** attempted per case and return `EvalCaseMetricResult(metric_name, error_message=str(e))` with `score=None` (`_evals_metric_handlers.py:~1092`). The presence of a populated `error_message` on those per-case results is the specific artifact Google support should inspect — it should reveal the underlying judge error (e.g. model not found / permission / quota).

---

## Ask for Google

1. Confirm whether there is a known incident affecting the autorater/judge model backing `final_response_quality`, `hallucination`, and custom `LLMMetric` scoring in `us-central1` around/after 2026-06-26.
2. Provide the per-case `error_message` semantics — what judge model / resource is failing.
3. ETA for recovery, and whether another region (e.g. `us-east4`) is unaffected as a workaround.
4. Consider surfacing dropped metrics at the run level (a partial-success state or a top-level warning) instead of `SUCCEEDED` with silently-missing metrics.

---

## Our mitigation (already shipped)

`wrangler/eval/evaluator.py` now raises `IncompleteMetricsError` when a `SUCCEEDED` run returns fewer aggregate scores than requested (`_check_metric_completeness`), so we fail loud instead of emitting a partial composite. This is a guard, not a fix — the metrics still cannot be computed until the service recovers.

## Impact on our work

3 of 10 planned Round 2 GEPA sweep runs (pro-bare, flash-bare, lite-bare) cannot be scored while this persists. The other 7 runs were captured before the cutover and are usable. GEPA optimization outputs are unaffected (saved).
