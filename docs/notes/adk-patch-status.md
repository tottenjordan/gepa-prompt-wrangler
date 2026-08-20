# ADK Monkey-Patch Status

**Verified on:** 2026-08-20 against `google-adk==2.7.1` (the latest release on PyPI at
that date).

`wrangler/optimize/optimizer.py:_patch_adk()` applies five patches to ADK internals.
CLAUDE.md said all five of the *original* patches were required at ADK 2.2.0. That stopped
being true at 2.7.1: **Patch 5 was deleted on 2026-08-20** (Phase 1 Task 1.1) and
**Patch 6 was added the same day** — see below.

**Re-run the probe below on every ADK bump.** A redundant patch is not harmless — it can
overwrite newer upstream behavior, which is exactly what happened to Patch 5.

Related: [toolchain-baseline.md](toolchain-baseline.md),
[../../CODE_STANDARDS.md](../../CODE_STANDARDS.md) §7.

---

## Per-patch findings

| # | Target | Upstream issue | Issue state | Still needed at 2.7.1? |
|---|--------|----------------|-------------|------------------------|
| 1 | `eval_case`/`eval_set` models: `extra="forbid"` → `"ignore"` | [#5906](https://github.com/google/adk-python/issues/5906) | **Closed** 2026-06-17 | **Yes** |
| 2 | `model_rebuild(force=True)` after patch 1 | — (companion to #5906) | — | **Yes** (pairs with 1) |
| 3 | `LocalEvalService._evaluate_single_inference_result` null guard | [#6071](https://github.com/google/adk-python/issues/6071) | **Closed** 2026-08-06 | **Yes** |
| 4 | `LocalEvalSampler._extract_eval_data` — score-None coercion + logging | — (local instrumentation) | — | Yes, but it is diagnostics, not a bug workaround |
| 5 | `rubric_based_evaluator._normalize_text` + `convert_auto_rater_response_to_score` | [#6072](https://github.com/google/adk-python/issues/6072) | **Closed** 2026-07-31 | **No — was actively harmful; DELETED 2026-08-20** |
| 6 | `SafetyEvaluatorV1.evaluate_invocations` — pin the metric to `safety_v1` | not filed | — | **Yes** (added 2026-08-20) |

### Patch 1 / 2 — still required despite the issue being closed

Issue #5906 is closed, but `extra="forbid"` is **still present** at 2.7.1 on eight
classes: `AppDetails`, `ConversationScenario`, `EvalBaseModel`, `IntermediateData`,
`Invocation`, `InvocationEvent`, `InvocationEvents`, `Rubric`. The issue was presumably
resolved for `SessionInput` only, or closed without the config change. **Do not remove
these patches on the strength of the closed issue** — check the live class configs.

### Patch 3 — still required despite the issue being closed

Issue #6071 was closed 2026-08-06, but the fix is **not in the 2.7.1 release**. The
installed `_evaluate_single_inference_result` source contains no `inferences is None`
check and never emits `NOT_EVALUATED`. The fix is presumably on `main` awaiting a
release. Re-check at ADK 2.8.x — this patch should become removable.

### Patch 5 — REMOVED 2026-08-20; upstream absorbed it and went further

Upstream 2.7.1 `_normalize_text` now does exactly what the local `_fuzzy_normalize`
does: NFKC normalization, a `_SMART_CHARS` translation table, whitespace collapsing,
decoration-character stripping, lowercasing. The override is redundant.

The `convert_auto_rater_response_to_score` override is worse than redundant — it is a
**regression**. Upstream 2.7.1 gained two behaviors the local copy (written against 2.2)
does not have:

1. **`rubric_id`-based matching.** Upstream builds a `rubric_by_id` map and matches on
   `rubric_response.rubric_id` *first*, falling back to normalized-text matching. The
   local override only does text matching, so it throws away the more reliable path.
2. **Empty-response guard.** Upstream checks `if not response_text` and logs a warning
   with an empty verdict list. The local override passes a possibly-empty string
   straight into `self._auto_rater_response_parser.parse(...)`.

The local override did add a **substring fallback** (unique-candidate containment
match) that upstream lacks, and that fallback went away with the deletion. If
`Rubric ... not found in the rubrics provided to the metric` warnings reappear in GEPA
logs, re-derive *only* that fallback on top of the current upstream implementation —
do not restore the whole method.

`tests/test_optimizer.py::TestFuzzyNormalize` now exercises upstream `_normalize_text`
directly with the nine garbled-text cases that motivated the override (markdown
bullets, smart quotes, em/en dashes, doubled whitespace). All nine pass at 2.7.1, which
is the evidence that deleting the normalizer override lost nothing. If an ADK bump
breaks those tests, upstream has regressed and the override becomes necessary again.
`test_patch_adk_preserves_upstream_rubric_id_matching` guards the other direction: it
fails if anyone reinstates a `convert_auto_rater_response_to_score` override.

### Patch 6 — ADK asks for a safety metric version us-central1 does not serve

`SafetyEvaluatorV1.evaluate_invocations()` hands the eval facade
`vertexai.types.PrebuiltMetric.SAFETY` — **unversioned**. The Vertex eval SDK resolves an
unversioned name client-side through `_evals_constant.METRIC_LATEST_SPEC_NAME`, which maps
`"safety" → "safety_v3"`. us-central1 does not serve v3, so every GEPA case came back:

```
400 INVALID_ARGUMENT: Unsupported predefined metric: safety_v3.
```

The score then arrives as `None` and patch 4 coerces it to `0.0`, so **GEPA does not fail —
it optimizes against a criterion nailed to zero.** This is silent failure #3
([silent-failures.md](silent-failures.md)) resurfacing in the optimize path: batch eval was
pinned to explicit versions when that was found, GEPA's criteria were not. Nothing in
`sampler_config.json` is wrong — it says `safety_v1`; the version is chosen *inside ADK*
and never appears in our config.

The patch swaps in `PrebuiltMetric.SAFETY_V1`, which falls through
`PrebuiltMetricLoader.__getattr__` to a bare-name lookup in
`SUPPORTED_PREDEFINED_METRICS` and resolves to `safety_v1` — no private SDK import. If that
resolution ever stops working the patch logs a warning and leaves ADK alone, because
pinning to a name the SDK cannot resolve would be worse than the bug.

`tests/test_optimizer.py::TestSafetyMetricPin` asserts both halves of the premise (ADK
still passes the unversioned metric; unversioned still resolves to something other than
`safety_v1`) plus the pin itself. When upstream pins the version, the first assertion fails
and the patch should be deleted.

Only `safety_evaluator.py` is affected. The other ADK evaluators reaching for a prebuilt
name (`response_evaluator` → `COHERENCE`, the three `multi_turn_*` evaluators) use names
absent from `METRIC_LATEST_SPEC_NAME`, so they load as GCS autorater recipes instead of
API predefined metrics and never hit the version negotiation.

---

## Probe script

Run this after any ADK version change:

```bash
uv run python - <<'PY'
import inspect
import google.adk
print("adk:", google.adk.__version__)

from google.adk.evaluation import eval_case as ec, eval_set as es
forbid = [
    f"{m.__name__}.{n}"
    for m in (ec, es)
    for n in dir(m)
    if isinstance(getattr(m, n), type)
    and hasattr(getattr(m, n), "model_config")
    and getattr(m, n).model_config.get("extra") == "forbid"
]
print("P1/P2 targets:", forbid or "NONE -> patch is a no-op, remove it")

from google.adk.evaluation import local_eval_service as les
src = inspect.getsource(les.LocalEvalService._evaluate_single_inference_result)
print("P3 needed:", "inferences is None" not in src)

from google.adk.optimization import local_eval_sampler as sm
print("P4 target exists:", hasattr(sm.LocalEvalSampler, "_extract_eval_data"))

from google.adk.evaluation import rubric_based_evaluator as rbe
up = inspect.getsource(rbe.RubricBasedEvaluator.convert_auto_rater_response_to_score)
print("P5 upstream has rubric_id matching:", "rubric_by_id" in up)
print("P5 upstream normalize is fuzzy:", "NFKC" in inspect.getsource(rbe._normalize_text))

from google.adk.dependencies.vertexai import vertexai
from google.adk.evaluation import safety_evaluator as se
unversioned = vertexai.types.PrebuiltMetric.SAFETY._get_api_metric_spec_name()
pinned = vertexai.types.PrebuiltMetric.SAFETY_V1._get_api_metric_spec_name()
print("P6 adk still asks unversioned:", "PrebuiltMetric.SAFETY," in inspect.getsource(se))
print("P6 unversioned resolves to:", unversioned, "(needed only if != safety_v1)")
print("P6 pin resolves to:", pinned, "(must be safety_v1 or the patch self-disables)")
PY
```

## Other 2.7.1 observations

- `GEPARootAgentPromptOptimizer.optimize()` signature is `(self, initial_agent, sampler)`.
  The wrangler-level `initial_instruction` kwarg is our own, on `wrangler.optimize.optimizer.optimize()`
  — it is not passed to ADK, so it is unaffected by ADK API churn.
- Importing ADK eval modules emits `UserWarning: [EXPERIMENTAL]` for
  `MetricEvaluatorRegistry`, `UserSimulatorProvider`, and a `vertexai.preview.rag`
  deprecation pointing at the new `agentplatform` client. Noise, not failures — but the
  `agentplatform` migration is a future item.
- ADK 2.2.0 changed the `LlmAgent` default model to `gemini-3-flash-preview`. Any agent
  that does not set `model=` explicitly silently changed models. See
  [model-lifecycle.md](model-lifecycle.md).
