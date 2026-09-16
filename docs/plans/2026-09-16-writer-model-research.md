# Research: which model should write GEPA's prompts?

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-16-writer-model-research.md` and commit it.

**Goal:** Answer three questions with evidence — what values `optimizer_model` can take, whether
`claude-opus-4-8` or `gemini-3.5-flash` is the right writer, and what else in Model Garden
deserves consideration.

**Deliverable is a recommendation, not a change.** `DEFAULT_OPTIMIZER_MODEL` stays where #86
put it; any re-pointing is a separate PR.

**Tech Stack:** Python 3.11, `uv`, ADK 2.8.0, Vertex AI Model Garden, `gcloud ai model-garden`.

---

## Context

The writer has moved three times in a week — `gemini-2.5-flash` (ADK's unexamined default,
campaigns 07/08) → `gemini-3.5-flash` (#83, to clear a retirement deadline) → `claude-opus-4-8`
(#86, to stop the writer being the scorer). **None of those choices was made against a survey of
what is actually available**, and each was reactive. This closes that.

### Already established during planning — do not redo

| Finding | Evidence |
| --- | --- |
| ADK resolves models by **34 regex patterns**, `re.fullmatch` | `google.adk.models.registry._llm_registry_dict` |
| `gemini-.*` → `Gemini`; `claude-3-.*`, `claude-.*-4.*`, `claude-.*-5.*` → `Claude`; `gpt-.*`/`o\d+-.*` → `OpenAILlm`; 14 `<provider>/.*` prefixes → `LiteLlm`; 7 `<vendor>\..*` → `OCIGenAILlm` | same |
| A **prefix override** exists: `"claude:anything"`, matched against the class name with a trailing `llm` stripped | `LLMRegistry._parse_model` / `_match_prefix` |
| Gemini **resource paths** resolve (`projects/…/publishers/google/models/gemini.+`) and **Vertex endpoints** do (`projects/…/endpoints/.+`). There is **no Anthropic path pattern** — a Claude resource path raises `ValueError` | registry dict |
| **41 callable text models** in this project (`CAN_PREDICT=Yes`, minus image/audio/embedding) | `gcloud ai model-garden models list --project=…` |
| `gemini-3.7-flash` and `gemini-3.8-flash` are **callable but absent from `wrangler/core/models.py`** — the registry lags the platform, which is part of why the retirement scare happened at all | same |
| `claude-opus-4-8` reports **`CAN_PREDICT=No`** (only the 5-series Anthropic models say Yes) yet a live call succeeded on 2026-09-16 | same + live probe |
| `litellm==1.85.7` is **in the optimize image**, so `vertex_ai/…` Model Garden ids are technically reachable | `Dockerfile.pipeline:34` |
| ADK forwards **7 of ~40** `gepa.optimize` arguments | `gepa_root_agent_prompt_optimizer.py:285-294` |

### The hazard every probe must be routed through

`_gepa_utils.generate_reflection_response` ends:

```python
if not content or not content.parts:
    return ""
return "".join(part.text for part in content.parts if part.text and not part.thought)
```

**It strips thought parts and returns `""` rather than raising.** A model that emits everything
as `thought=True`, or is truncated before visible text, hands GEPA an empty proposal silently —
and GEPA runs its full nine hours regardless. The thinking-heavy candidates are exactly the ones
most at risk. **Probe through this function, never a hand-rolled client.**

---

## Task 1: Desk screen — 41 candidates down to a shortlist

**Files:** `docs/analysis/2026-09-16-writer-model-survey.md` (new)

No network. For every callable text model, record: ADK resolution class, quota pool and RPM,
retirement date (registry, else vendor docs), whether it is the judge, whether it is an enabled
agent model in any manifest, and thinking-config shape.

**Eliminate on hard constraints only** — resolution failure, inside the 30-day retirement bar, no
announced retirement date (the `gemini-3.6-flash` objection in `DEFAULT_OPTIMIZER_MODEL`), lands
back on the 5 RPM Gemini pool, or collides with judge/agent identity.

**Carry `LiteLlm`-resolved candidates forward but flag them.** `vertex_ai/…` reaches DeepSeek,
Qwen, Kimi, GLM, Grok, Llama, Mistral — the reasoning-strong options — but through a path this
repo has never exercised. Flag, do not pre-emptively drop; the probe decides.

**Expect ~6–10 survivors.** Include `gemini-3.5-flash` and `claude-opus-4-8` regardless, since
question 2 is explicitly about them, and `claude-opus-5` because it is the Anthropic model the
platform actually advertises.

## Task 2: Probe the shortlist

**Files:** `scripts/probe_optimizer_models.py` (new), results into the survey doc

Follow `scripts/analyze_toolset_loss.py` and `scripts/repro_mcp_refresh_hang.py`: a committed,
re-runnable script, arguments not constants, and a docstring that says what a wrong result means.

| # | Probe | Fails when |
| --- | --- | --- |
| P0 | resolve + construct + build config | raises |
| P1 | liveness, one-line prompt | 4xx/5xx, auth, region error |
| P2 | thinking matrix `adaptive / enabled / disabled` | no accepted mode |
| **P3** | **non-empty after thought-stripping** | returns `""` — the silent one |
| P4 | realistic GEPA-shaped reflection (traces, scores, rubric feedback) | refusal, meta-commentary, no instruction |
| P5 | output length over several samples | consistently emits 5,000-char prompts |
| P6 | latency and token cost | 69 calls/campaign becomes painful |
| P7 | same prompt ×3 | wildly unstable |

Weight **P3** and **P5** most. P3 is the failure that would not announce itself. P5 is the one the
literature says matters: GEPA's documented failure mode is the reflector encoding edge cases into
ever-longer prompts, trading training score for generalization — and campaign 07 measured
78 → 3,873 characters alongside exactly the criterion-up/holdout-down signature that predicts.

Budget ~8 calls × 6–10 models ≈ **50–80 calls, a few dollars, ~1 hour**. No optimize stage, no
campaign, `main` never frozen.

Record the `claude-opus-4-8` `CAN_PREDICT=No` discrepancy as a caveat column with what the probe
observed, rather than as a separate investigation.

## Task 3: What ADK drops, and the verbosity mitigation

**Files:** the same survey doc, or a sibling analysis. **Findings only — any patch is its own PR.**

`gepa.optimize` accepts ~40 arguments; ADK forwards 7. Enumerate the rest and assess three:

- **`reflection_prompt_template` / `custom_candidate_proposer`** — the published
  length-regularization mitigation (DSPy's `WordLimitProposer` pattern). Unreachable through ADK's
  config today. Establish what reaching it would cost: a 6th `_patch_adk` entry, an upstream ADK
  change, or bypassing `GEPARootAgentPromptOptimizer` entirely.
- **`seed`** — unreachable, which plausibly explains campaign 07's **12.3× run-to-run spread**
  that `num_runs` could not touch. Check whether a fixed seed would actually make a GEPA run
  reproducible, or whether the agent/judge non-determinism dominates anyway.
- **`max_reflection_cost`** — a direct spend cap on the expensive half.

State plainly whether prompt-length regularization is a **bigger lever than the writer id** for
the holdout regression. If the evidence says yes, that reframes DOE 11 and the writer A/B both,
and the recommendation should say so.

---

## Deliverables

1. `docs/analysis/2026-09-16-writer-model-survey.md` — candidate matrix (41 → shortlist →
   probed), the recommendation with reasoning, the ADK-dropped-arguments findings, and an explicit
   section on **what the probes cannot answer**.
2. `scripts/probe_optimizer_models.py` — re-runnable, so the next person re-runs rather than
   re-derives.
3. Index rows in `docs/notes/README.md`.

**Not in scope:** changing `DEFAULT_OPTIMIZER_MODEL`, registering the missing models, or patching
ADK. Each is a separate PR the survey may motivate.

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1291), `ruff check`, `ruff format --check`,
   `ty check` at its 21-diagnostic baseline. The survey is docs plus one script, so a red suite
   means something unrelated was disturbed.
2. **The probe script is the verification.** Re-run it end to end and confirm the table in the doc
   matches its output — a hand-edited results table that drifts from the script is worse than no
   script.
3. Re-run the desk screen's `gcloud ai model-garden models list` and confirm the candidate count
   still matches; the roster changes weekly and the doc should carry the date it was taken.
4. Confirm every probe ran through `generate_reflection_response`, not a direct client — grep the
   script for it. This is the whole reason P3 can fire.
5. Image tag unchanged at `aff07d2d60f3`.

## Risks

- **The roster moves weekly.** The survey is a snapshot and must be dated in its title and header;
  a stale matrix read as current is the failure mode.
- **Probes measure capability, not outcome.** A model can pass all eight and still write worse
  prompts. The doc must say this in its own section, or a green table will be read as an
  endorsement it does not support.
- **P5 has no ground truth.** Shorter is *hypothesised* to generalise better, on the literature
  plus one internal correlation. Report lengths as an observation, not a ranking.
- **`LiteLlm` candidates may pass probes and still fail in the pipeline**, where the code runs
  under KFP with a different env. Any recommendation landing on a `vertex_ai/…` id needs a
  smoke run in the optimize container before it is trusted.
- **Spending real money on ~80 calls to answer a question we may then not act on.** Accepted: the
  writer has moved three times reactively, and a few dollars is cheap against another blind move.
