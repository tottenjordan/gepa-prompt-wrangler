# Which model should write GEPA's prompts? A survey, 2026-09-16

**Snapshot date: 2026-09-16.** The Model Garden roster changes weekly; re-run
[`scripts/probe_optimizer_models.py`](../../scripts/probe_optimizer_models.py) before
trusting any row here.

**This is a recommendation, not a change.** `DEFAULT_OPTIMIZER_MODEL` is untouched.

**Scope:** latest first-party Claude and Gemini. Model Garden's open and partner models
were surveyed once and ruled out — see [Q3](#q3-other-model-garden-models--surveyed-once-ruled-out).

## Why

GEPA runs two models: the **judge** scores candidates, the **optimizer model** reads the
failures and writes the next candidate prompt. The writer has moved three times in a week —
`gemini-2.5-flash` (ADK's unexamined default, campaigns 07/08) → `gemini-3.5-flash` (PR #83,
to clear a retirement deadline) → `claude-opus-4-8` (PR #86, to stop the writer being the
scorer). Every move was reactive and none was made against a survey.

---

## Q1: What values can `optimizer_model` take?

**Any string matching one of ADK's 34 registered regexes** — the docs' "default:
gemini-2.5-flash" says nothing about the range. From
`google.adk.models.registry._llm_registry_dict`, matched with `re.fullmatch`:

| Pattern family | Examples | Class |
| --- | --- | --- |
| `gemini-.*`, `gemma-4.*`, `model-optimizer-.*` | `gemini-3.8-flash` | `Gemini` |
| `projects/…/publishers/google/models/gemini.+` | full resource path | `Gemini` |
| `projects/…/endpoints/.+` | a self-deployed Vertex endpoint | `Gemini` |
| `claude-3-.*`, `claude-.*-4.*`, `claude-.*-5.*` | `claude-opus-4-8` | `Claude` |
| `gpt-.*`, `o\d+-.*` | `gpt-5` | `OpenAILlm` |
| 14 × `<provider>/.*` | `vertex_ai/…`, `anthropic/…`, `bedrock/…`, `openai/…` | `LiteLlm` |
| 7 × `<vendor>\..*` | `meta.llama-…`, `xai.grok-…` | `OCIGenAILlm` |
| `gemma-.*`, `ollama/gemma3.*`, `apigee/.*` | | `Gemma`, `Gemma3Ollama`, `ApigeeLlm` |

Three things worth knowing:

- **A prefix override exists.** `"claude:some-id"` forces a class, matched against the
  class name with a trailing `llm` stripped (`LLMRegistry._parse_model`). Undocumented
  escape hatch.
- **Gemini resource paths resolve; Anthropic ones do not.** There is no
  `publishers/anthropic` pattern, so a full Claude resource path raises `ValueError`. This
  is why PR #83's "a bare Claude id lacks the resource path" concern was backwards — the
  bare id is the *only* form that works.
- **`vertex_ai/…` opens Model Garden**, and `litellm==1.85.7` is in the optimize image, so
  it is reachable in the pipeline and not only locally.

**In this project specifically:** 91 models report `CAN_PREDICT=Yes`, of which **38 are
text models**, and all 38 resolve through ADK. Seven latest first-party models were probed;
five Model Garden models were probed once and ruled out (Q3).

---

## Q2: `claude-opus-4-8` vs `gemini-3.5-flash` — and every other latest first-party model

Probed through ADK's own `generate_reflection_response`, 3 reflection samples each, on an
identical realistic request (traces, scores, rubric feedback):

| model | class | thinking accepted | prompt chars | s/call | verdict |
| --- | --- | --- | --- | --- | --- |
| `gemini-3.5-flash` | Gemini | adaptive, enabled, disabled, none | **817 ±26** | 6.9 | ok, **but it is the judge** |
| `gemini-3.7-flash` | Gemini | adaptive, enabled, none | 887 ±111 | 9.6 | ok, **unregistered** |
| `gemini-3.8-flash` | Gemini | adaptive, enabled, none | 898 ±109 | 9.4 | ok, **unregistered** |
| **`claude-opus-4-8`** | Claude | adaptive, disabled, none | **1004 ±137** | **4.3** | **ok — recommended** |
| `claude-sonnet-5` | Claude | adaptive, disabled, none | 1649 ±61 | 5.2 | ok, **but it is an agent under test** |
| `claude-opus-5` | Claude | adaptive, disabled, none | **3375 ±235** | 10.9 | ok |
| `claude-fable-5-1` | Claude | — | — | — | **403, access not enabled** |

**Recommendation: keep `claude-opus-4-8`.** It is the only candidate that is neither the
judge nor an enabled agent model, sits on the Anthropic pool at rpm 800 rather than the
judge's saturated 5, has a dated retirement (2027-05-28), is the **fastest of all seven**,
and is the most concise Claude by a wide margin.

Four findings behind that:

**Prompt length varies 4× across first-party models alone** — 817 characters from
`gemini-3.5-flash` to 3,375 from `claude-opus-5`, on an identical request. GEPA's documented
failure mode is the reflector encoding edge cases into ever-lengthening prompts, trading
training score for generalisation, and campaign 07 measured 78 → 3,873 characters alongside
exactly the criterion-up / holdout-down signature that predicts. **The "better" Claude is
the more verbose one**, and on current evidence that counts against it. Reported as an
observation, not a ranking — the link between length and the holdout regression is a
hypothesis plus one internal correlation.

**The Gemini flash tier is remarkably uniform**: 817 / 887 / 898 characters across 3.5, 3.7
and 3.8, and the newer two are *slower* (9.4–9.6 s against 6.9). There is no evident writing
gain from the newer Gemini ids, so the registry lagging the platform costs less than feared.

**`gemini-3.5-flash` is the most concise and most consistent** writer measured (817 ±26). If
it were not the judge it would be the leading candidate — worth recording in case the judge
ever moves.

**`claude-opus-4-8`'s `CAN_PREDICT=No` is not a blocker on current evidence.** Model Garden
reports `No` for every Anthropic model except the 5-series, yet opus-4-8 answered every
probe. The flag appears to track "offered for new consumption" rather than callability.
A watch item, not a fault — and `claude-opus-5` is the proven fallback if it ever becomes one.

---

## Q3: Other Model Garden models — surveyed once, ruled out

**Scope decision: the candidate list is now latest first-party Claude and Gemini only.**
Model Garden's open and partner models were surveyed on 2026-09-16 and are not being
carried forward. Recorded here so the question does not get re-opened blind.

They *are* reachable, and the id form is load-bearing — it took three attempts:

| form | result |
| --- | --- |
| `vertex_ai/<model>` | `"Vertex project and location are required for custom endpoint"` |
| + `vertex_project` / `vertex_location` | routed to a *self-deployed endpoint*, 404 |
| **`vertex_ai/<publisher>/<model>` at location `global`** | **works** |

`us-central1` returns "Publisher model not found" — global-only. `LiteLlm` needs
`vertex_project` and `vertex_location` passed **explicitly**; it does not read
`GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`, so `LLMRegistry.new_llm()` cannot build a
working one.

Five probed: `qwen3-next-80b-a3b-thinking` (483 ±120 chars, ok),
`kimi-k2-thinking` (855 **±764**, and it returned an **empty proposal** on a later call), `deepseek-v3.2` (429 on the
reflection samples), `glm-5.2` (429), `grok-4.6` (404).

**Ruled out on three grounds unrelated to model quality:**

1. **Quota.** Two of five returned 429 on a *first* call, and **LiteLLM retries a 429
   internally for minutes** — a follow-up run was killed at 250 s waiting. Against a 69-call
   reflection budget that is a campaign-length risk on the one axis already binding.
2. **Stability.** Kimi's ±764 spread on an 855 mean means consecutive identical requests
   produce wildly different prompts. Campaign 07 already suffers 12.3× run-to-run spread
   from a *stable* writer.
3. **Untested path.** `LiteLlm` has never run in the optimize container.

One worth remembering: **`qwen3-next-80b-a3b-thinking` was the most concise writer measured
anywhere, at 483 characters.** If the quota position changes and length turns out to drive
the holdout regression, it earns a second look.

## What these probes cannot answer

**Whether any of these writes *better* prompts.** Every probe measures capability and
behaviour — does it resolve, does it answer, does it survive thought-stripping, how long
and how stable is its output. None measures optimization outcome. A model can pass all of
them and still produce worse prompts.

That needs a real optimize-stage comparison: ~44 h at n=2, gated on the judge quota. Scored
on the criterion it is well powered (n=2 detects 23% of the effect) — see
[DOE 11](../doe/11-writer-scorer-identity.md).

**CORRECTION (same day): the silent failure the probes were built around DID fire.** An
earlier revision of this document said it did not. On a follow-up call,
`vertex_ai/moonshotai/kimi-k2-thinking-maas` returned **an empty string** through
`generate_reflection_response` — the exact failure the probe was designed to catch, where a
model emits only thought parts and GEPA receives an empty prompt proposal with no error.

It is **intermittent**, which is why the first pass missed it: the same model returned
855 characters on average across three samples, but with a standard deviation of **±764**.
That spread was the tell, and it should have been read as one at the time — a ±764 spread
on an 855 mean means some samples are near zero.

Two things follow. **No first-party candidate tripped it** — every Claude and Gemini model
returned text on every call. And **intermittency is the point**: a writer that returns an
empty proposal one call in several would corrupt an optimize stage silently and
irregularly, which is far harder to diagnose than one that fails outright. It strengthens
the case for ruling the Model Garden models out, and it means the probe should be re-run
with more samples for any candidate that shows high length variance.

---

## Task 3: what ADK does not forward, and the verbosity lever

**`gepa.optimize` accepts 46 arguments. ADK passes 8.** The 38 dropped include three that
matter here:

**`reflection_prompt_template` / `custom_candidate_proposer`** — the published
length-regularization mitigation (DSPy's `WordLimitProposer` pattern: constrain the
proposer's word count so it cannot encode edge cases indefinitely). **Unreachable through
ADK's config.** Getting at it means a 6th `_patch_adk` entry, an upstream ADK change, or
bypassing `GEPARootAgentPromptOptimizer` entirely.

**`seed`** — also unreachable. Campaign 07 measured **12.3× the control floor** in
run-to-run spread on identical inputs, and `num_runs` could not touch it because it
averages the evaluation of one optimized prompt, not the choice of prompt. A fixed seed is
the obvious candidate. Whether it would actually make a run reproducible is **unverified** —
agent and judge non-determinism may dominate — and that is a cheap thing to test before
assuming it.

**`max_reflection_cost`** — a direct spend cap on the expensive half, unused.

### Is prompt length a bigger lever than the writer id?

**RETRACTED 2026-09-16, same day.** An earlier revision of this section said *"Plausibly
yes, and it is better evidenced than the writer question."* The "plausibly" survives. The
**"better evidenced" was false, and it was false against data already in our own bucket.**

Every optimize stage records `original_chars` and `optimized_chars`. Pairing those with the
holdout delta, across all seven arms that have both:

| cohort | n | `r(optimized_chars, Δ instruction_following_v1)` |
| --- | --- | --- |
| all arms | 7 | +0.373 |
| optimized only (drop the arm that returned its seed unchanged) | 6 | **+0.699** |
| **clean substrate only (c07 + c08)** | **4** | **−0.112** |
| `claude-sonnet-5` only, model held constant | 3 | +0.470 |

**The sign flips with the subset, and the only comparable cohort is indistinguishable from
no relationship.** A *positive* `r` means longer prompts had a *smaller* holdout
regression — the opposite of the hypothesis. Nothing here supports the mechanism in either
direction.

The hypothesis remains reasonable: verbosity-overfitting is GEPA's documented failure mode,
campaign 07 did grow its prompt 78 → 3,873 characters, five arms do show criterion-up /
holdout-down, and the writers measured above differ 4× in output length so the lever has
real range. What was never true is that any of that is *evidence about length as the
channel*. It was an appeal to published literature plus one internal correlation, neither
checked against the artifacts.

**Arm-level data cannot settle it** — one nine-hour stage yields one `(length, delta)` pair,
which is why n=4. GEPA's own `run_dir` yields one per *candidate*: 14 on the surviving local
run, spanning 78 to 12,741 characters. That directory was being deleted with the container
on every pipeline run; it is now uploaded to
`pipeline-runs/{run_id}/stages/optimize/gepa_run/{pair_id}/`, and
[`scripts/analyze_candidate_lengths.py`](../../scripts/analyze_candidate_lengths.py) reads
it.

One limit that does not go away: those per-candidate scores are GEPA's **criteria**, not the
holdout. `instruction_following_v1` is not a criterion and is never scored during the
search, so candidate data can test the first half of the overfitting story and not the
second.

## Recommendations

1. **Keep `claude-opus-4-8`.** No change needed; #86 landed on a defensible choice for
   reasons that survive the survey.
2. **Register the missing models.** `gemini-3.7-flash` and `gemini-3.8-flash` are callable
   and absent from `wrangler/core/models.py`. The registry lagging the platform is part of
   why the retirement scare happened at all. Separate PR.
3. **Do not build length regularization.** The premise is unsupported (above). GEPA's
   `run_dir` is now preserved, so the question becomes answerable at no marginal cost from
   the next campaign onward — revisit when a campaign has produced candidate data, not
   before.
4. **Test whether `seed` would fix the run-to-run spread** before building any design around
   repeats. Cheap, and it bears on every campaign costing.
