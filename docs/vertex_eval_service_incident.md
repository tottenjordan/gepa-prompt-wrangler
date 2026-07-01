# Vertex AI Gen AI Evaluation Service — Silent Partial-Metric Degradation (Escalation)

---

## 🛑 RETRACTED 2026-07-01 (PM) — NOT a Vertex incident. This was our own deploy bug.

**Do not file this with Google.** Root-caused and fixed on 2026-07-01. The partial-metric
"drop" was a **downstream symptom of degenerate agent responses**, not an Evaluation Service fault.

**Actual root cause:** `wrangler/core/deploy.py:build_source_package()` copied the agent's `.env`
verbatim into the GEAP build package. That `.env` contained a live `GOOGLE_API_KEY` (added for
PaperBanana). The packaged `config.py`'s `load_dotenv()` re-read it at runtime, so `GOOGLE_API_KEY`
overrode Vertex ADC → `google.genai` attempted key-auth → Vertex returned
**`401 UNAUTHENTICATED: API keys are not supported by this API`** → ADK raised
`RuntimeError("Failed to create session.")`. Every inference then returned an error payload
(`{"error": "Failed to create a new session: 400 FAILED_PRECONDITION ..."}`). Feeding that error
string to the autoraters: `safety_v1` + `instruction_following_v1` still scored it (→ survive),
while `final_response_quality_v1` + `hallucination_v1` + custom `tool_use` errored (score=None →
dropped) → the "2/5" result. **The Eval Service behaved correctly on garbage input.**

**Decisive evidence:** same model / cases / inference path, only the engine differs —
`lite-core` (`5407968282181369856`) returned real prose and 5/5; `lite-bare`
(`7757466301064282112`) returned the error payload every case and 2/5. GEAP ReasoningEngine logs
for the broken engine show the literal `401 UNAUTHENTICATED ... API keys are not supported ...
raise RuntimeError("Failed to create session.")`. This also explains the earlier confounds: the
"clean→broken cutover at 06-26" tracked when the key entered the deployed env (not a service
event); the "8768 identical output_tokens" was the identical error payload; the "per-engine
recovery" was engines redeployed/warmed without the key.

**Fix (shipped, commit `5d34aa0`):** `build_source_package()` now strips
`GOOGLE_API_KEY`/`GEMINI_API_KEY` from the copied `.env`. Verified live: a lite-bare agent
redeployed with the fix returns real responses and full 5/5 metrics.

**Everything below is the original (incorrect) escalation, kept for the record only.**

---

**Status:** RETRACTED — was misdiagnosed as a service-side incident; actually a client deploy bug (see banner above)
**Filed by:** prompt-wrangler team
**Project:** `hybrid-vertex`
**Region:** `us-central1`
**First observed:** ~15:18 UTC 2026-06-26 · **Still reproducing:** 2026-07-01 (≥5 days)
**Severity:** High — the service reports `SUCCEEDED` while silently dropping 3 of 5 requested metrics, so downstream consumers compute plausible-but-wrong aggregates unless they add their own completeness check.

**Client versions (regression reproduces across both — confirming it is server-side):**
- Sweep pipeline evals: `google-cloud-aiplatform 1.157.0`, `google-genai 2.8.0`, `google-adk 2.2.0` (single cached Docker image `cf89b888d680`, built 2026-06-10, reused unchanged for all 10 runs).
- Independent 2026-07-01 probe: `google-cloud-aiplatform 1.158.0`, `google-genai 2.9.0`. Same 2/5 result on engines that returned 5/5 before the cutover.

The version was held constant across the 5/5→2/5 boundary within the sweep (same image), and the failure then reproduced on a *newer* client version — so no client SDK version explains the regression.

---

## ⚠️ UPDATE 2026-07-01 (PM) — refined diagnosis supersedes "service-wide" claim below

Controlled re-probing on 2026-07-01 ~16:20–16:35 UTC (client 1.158.0 **and** 1.159.0, same 3 cases, same 4 predefined metrics, only the engine varies) shows the drop is **NOT a blanket service-wide outage** and is **NOT time-uniform**:

| engine | cohort | model | metrics now |
|---|---|---|---|
| lite-core (`5407968282181369856`) | core | flash-lite | **4/4 ✅ recovered** |
| lite-bare (`7757466301064282112`) | bare | flash-lite | **2/4 ❌** |
| pro-bare (`1946107543617011712`) | bare | pro | **2/4 ❌** |
| flash-bare (`1690528264763736064`) | bare | flash | **2/4 ❌** |

**Decisive controlled pair — lite-core vs lite-bare:** same model, same client, same cases, same instant → opposite result. This isolates the cause to the **agent's deployed prompt and therefore its response content**, not time, model, or SDK version. The `final_response_quality` + `hallucination` autoraters **error on certain agents' responses** (→ `error_message`, `score=None` → dropped), while `safety` + `instruction_following` score fine.

**Time component is real but per-engine:** this morning (07-01 AM) lite-core was *also* 2/5; by 16:20 UTC it returned 4/4. So affected engines are **recovering individually** — the core cohort has cleared, the bare cohort had not yet as of 16:35 UTC.

**Two prior claims corrected:** (1) the SDK **upgrade is not a fix** — 1.158.0 and 1.159.0 give identical results per engine (lite-core 4/4 on both). (2) the "every engine regardless of model or prompt" statement below was an artifact of the 07-01 AM snapshot when all probed engines happened to be broken. **Still true:** it is server-side (autorater), not a wrangler bug and not client-version-dependent. The sharper ask for Google: *why do the FRQ/hallucination autoraters error on specific agent response content, and what changed ~06-26?*

---

## Summary

The Vertex AI Gen AI Evaluation Service (`client.evals.create_evaluation_run`) has, since ~15:18 UTC on 2026-06-26, been returning only a **subset** of the requested metrics while the evaluation run still reports terminal state `EvaluationRunState.SUCCEEDED`. There is no error, no `FAILED` state, and no top-level warning — the dropped metrics are simply absent from the aggregate results and appear per-case as `EvalCaseMetricResult` entries carrying an `error_message` with `score=None`. *(See the UPDATE block above — the drop is content/agent-dependent and recovering per-engine, not a uniform service-wide outage.)*

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
