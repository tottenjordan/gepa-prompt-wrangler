# Move GEPA's prompt writer to Claude

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-16-claude-optimizer.md` and commit it.

**Goal:** Make `claude-opus-4-8` write GEPA's candidate prompts, so the writer is neither
the judge nor any agent under test, and the optimizer leaves the saturated Gemini pool.

**Architecture:** One config object, built in `wrangler/optimize/optimizer.py` and passed to
`GEPARootAgentPromptOptimizerConfig`. **No ADK monkey-patch, no resource-path plumbing.**

**Tech Stack:** Python 3.11, `uv`, ADK 2.8.0, Vertex AI.

---

## Context

PR #83 set `DEFAULT_OPTIMIZER_MODEL = "gemini-3.5-flash"` to clear a retirement time bomb.
That fixed the lifecycle problem and created two others: the model **writing** prompts is now
the model **scoring** them, and the optimizer sits on the 5 RPM Gemini pool that already
bounds campaign length.

#83's PR body claimed Claude was blocked by two mechanics. **Both were wrong, and this plan
starts by correcting them** — they are quoted in `DEFAULT_OPTIMIZER_MODEL`'s comment and in
CLAUDE.md, so they will mislead the next reader until fixed.

| #83 claimed | Actually |
| --- | --- |
| "ADK passes a Gemini-only `thinking_config`" | ADK **maps** it. `anthropic_llm._build_anthropic_thinking_param` turns `ThinkingConfig` into Anthropic's `thinking` param |
| "a bare Claude id lacks the `projects/.../global/...` path" | `Claude._anthropic_client` falls back to `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`, and **every pipeline component hardcodes `GOOGLE_CLOUD_LOCATION = "global"`** (`components.py:126`, `:331`, `:499`, `:869`, `:1043`) *before* the Secret Manager load. A full path is in fact **worse**: `LLMRegistry.resolve()` matches `supported_models()` regexes (`claude-.*-5.*` etc.) and raises `ValueError` on a `projects/...` string |

### The real blocker, and it is small

ADK's default `model_configuration` is
`GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=10240))`.
A **positive** budget maps to `{'type': 'enabled', 'budget_tokens': 10240}`, which:

1. is **rejected with a 400 by Claude Opus 4.7 and later** — the generation the registry
   already marks with `supports_sampling_params=False`, which includes every candidate here;
2. exceeds `Claude.max_tokens`, whose default is **8192**. Anthropic requires
   `budget_tokens < max_tokens`, so this would fail even on a model that accepted `enabled`.

`thinking_budget=-1` maps to `{'type': 'adaptive'}`, which is what that generation requires.
Verified against the live function for budgets `10240 / -1 / 0`.

And `model_configuration` is a plain field on the config object, while
`_gepa_utils.generate_reflection_response` forwards `config.max_output_tokens` to Anthropic's
`max_tokens` (`anthropic_llm.py:1033`). **So both knobs are reachable without patching ADK.**

### Why `claude-opus-4-8`

| | |
| --- | --- |
| ≠ judge | no self-scoring bias; restores the writer ≠ scorer property campaigns 07 and 08 had |
| ≠ any agent under test | **all 4 opus pairs in `manifests/` are `enabled: false`** (verified), so this cannot silently become writer == agent the way `claude-sonnet-5` would |
| quota | rpm **800** on the Anthropic pool — off the 5 RPM Gemini bottleneck entirely |
| lifecycle | retires **2027-05-28**, well clear of the 30-day bar |

Cost rises from pennies to roughly **$5/campaign** (≈69 calls at $5/$25 per 1M). Against a
~$4 campaign that is a real multiple and an immaterial absolute; state it, do not hide it.

---

## Task 1: Build the optimizer's model configuration

**Files:** Create `wrangler/optimize/optimizer_config.py`; test `tests/test_optimizer_config.py`

A helper, not an inline branch, because two tests and one caller need it.

**Step 1 — write the failing test.**

```python
def test_claude_gets_adaptive_thinking():
    cfg = build_optimizer_config("claude-opus-4-8")
    assert cfg.thinking_config.thinking_budget == -1

def test_gemini_keeps_adks_explicit_budget():
    cfg = build_optimizer_config("gemini-3.5-flash")
    assert cfg.thinking_config.thinking_budget == 10240

def test_the_budget_maps_to_a_param_anthropic_accepts():
    """The assertion that matters -- checked against ADK's real mapper."""
    from google.adk.models.anthropic_llm import _build_anthropic_thinking_param
    assert _build_anthropic_thinking_param(
        build_optimizer_config("claude-opus-4-8")) == {"type": "adaptive"}

def test_max_output_tokens_exceeds_any_manual_budget():
    cfg = build_optimizer_config("claude-opus-4-8")
    assert cfg.max_output_tokens > 10240
```

**Step 2 — run it, watch it fail** (`ModuleNotFoundError`).

**Step 3 — implement.** Branch on the registry, not on a string prefix:
`get_spec(model).supports_sampling_params is False` is already exactly the Opus 4.7+ /
Sonnet 5 / Fable 5 generation. **Write down in the docstring that this field is being read as
a generation marker rather than for its literal meaning** — that is load-bearing and
non-obvious, and if the two sets ever diverge this is the line that breaks.

Set `max_output_tokens = 16384` for Claude so thinking plus a ~4,000-character prompt fits
inside it, and leave Gemini on ADK's default.

**Step 4 — run tests. Step 5 — commit.**

## Task 2: Point the role at Claude and wire the config in

**Files:** `wrangler/core/models.py`, `wrangler/optimize/optimizer.py`

**Step 1.** `DEFAULT_OPTIMIZER_MODEL = "claude-opus-4-8"`, and **rewrite the comment block
entirely.** It currently argues for gemini-3.5-flash and repeats both wrong blockers. It
should now carry: why not the judge, why not an agent model, the opus-disabled-as-agent fact
that makes that durable, the real `enabled`-vs-`adaptive` blocker, and the cost delta.

**Step 2.** In `optimizer.py`, add `model_configuration=build_optimizer_config(...)` to the
`optimizer_kwargs` dict already added by #83.

**Step 3.** The existing log line prints writer and judge; keep it — it is how a run is
verified from its own log.

## Task 3: Guards

**Files:** `tests/test_adk_optimizer_model.py`

**Step 1.** `test_optimizer_and_judge_sharing_a_model_is_deliberate` asserts writer **==**
judge and will now fail. **Invert it, keep the reasoning**, and rename to
`test_the_writer_is_not_the_scorer`.

**Step 2.** New guard — the reason opus was chosen, so it cannot rot:

```python
def test_the_writer_is_not_an_enabled_agent_model():
    """Writer == agent is a confound for cross-model campaigns like 07's frontier."""
    # walk manifests/*.yaml via PairFactory.load, check m.enabled_pairs only
```

Use `manifest.enabled_pairs`, never `manifest.pairs` — CLAUDE.md is explicit, and a disabled
opus pair must not trip this.

## Task 4: Docs

**Files:** `CLAUDE.md`, `docs/doe/11-writer-scorer-identity.md`

**Step 1.** CLAUDE.md's "GEPA runs two models" paragraph says both are `gemini-3.5-flash`.
Correct it, and state that campaigns 07/08 used a *third* writer (`gemini-2.5-flash`), so
writer capability still differs from those campaigns even though writer ≠ scorer is restored.

**Step 2.** DOE 11 gets a status note at the top: **GATED, and now hypothetical** — the
overlap it measures is no longer shipped. Keep the file; the power arithmetic
(sd 0.0148, n=4 detects only 0.029) applies to any two-condition holdout design.

Add one line to it recording that **a writer A/B scored on the criterion is better powered
than DOE 11 itself** (23% of the effect at n=2, against 79%), so if a slot opens after the
quota lands, that is the campaign to run first. Do not create a new DOE file for it yet —
nothing is scheduled, and a pre-registration for an unscheduled campaign is the dead weight
this repo already has one instance of.

---

## Should the two writers be A/B'd first? No — but the A/B is worth scheduling

Asked during planning, and the arithmetic changes the usual answer. Scored on the
**criterion** rather than the holdout, a writer A/B is well powered, because safety's effect
is large and its variance small:

| comparison | sd | detectable at n=2 | % of effect |
| --- | --- | --- | --- |
| writer A/B on `safety_v1` (mean +0.149) | 0.0121 | 0.034 | **23%** |
| DOE 11 on the holdout (mean −0.053) | 0.0148 | 0.042 | 79% |

**So this is the best-powered campaign currently available and should displace DOE 11 in
priority.** It still costs ~44 h at n=2 and freezes `main`, so it waits for the quota.

It must not gate this change, for three reasons:

1. The case for opus is **structural, not a quality claim** — ≠ judge, ≠ any agent under
   test, off the 5 RPM bottleneck, dated retirement. It needs to write *comparably* good
   prompts, not better ones.
2. Gating keeps a **known-suspect** config in place for two more nights. Writer == scorer
   shipped as a side effect of a retirement fix, with a plausible bias mechanism and no
   evidence in its favour.
3. The contrast is **confounded**: `gemini-3.5-flash` vs `claude-opus-4-8` varies capability
   *and* judge-identity together. That is the right comparison for "shipped vs proposed", but
   it cannot answer "which writes better prompts" — that needs two writers both ≠ judge.

**Free check instead, in verification step 6 below:** the next campaign needs a validation
arm anyway, and its criterion gain lands against a known four-arm band of **+0.131 to
+0.157**. Uncontrolled, but it catches a gross regression at zero cost.

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1279 today), `ruff check`,
   `ruff format --check`, `ty check wrangler/` at its baseline of 21 diagnostics.
2. **A live call, and this is the one that actually matters.** Everything above is static
   analysis of ADK internals; the claim "Opus 4.7+ rejects `enabled`" is about the *Vertex
   API*, not about ADK. Write a throwaway script that calls
   `_gepa_utils.generate_reflection_response` with `Claude(model="claude-opus-4-8")`, the new
   config, and a one-line prompt. Expect non-empty text back. **Then run it a second time
   with `thinking_budget=10240` and confirm it 400s** — if that *succeeds*, the premise of
   this plan is wrong and the simpler fix (leave the config alone) applies.
3. Confirm `LLMRegistry.resolve("claude-opus-4-8")` is `Claude`, and that the pipeline's
   `GOOGLE_CLOUD_LOCATION = "global"` is set *after* the Secret Manager `load_dotenv`.
4. Image tag unchanged at `aff07d2d60f3` — nothing here touches `pyproject.toml`, `uv.lock`
   or `Dockerfile.pipeline`.
5. Do **not** validate by launching a campaign. The next optimize stage will print
   `Optimizer model: claude-opus-4-8`; that is the field check.
6. **Non-inferiority band, on the next campaign's validation arm — free.** Record its
   Δ`safety_v1` against the four-arm band **+0.131 to +0.157** (writer `gemini-2.5-flash`).
   Inside it, or above: no evidence of a worse writer. **Materially below it: stop and run
   the A/B before spending a campaign.** One arm against a historical band is not a
   controlled comparison and must not be reported as one — it is a smoke alarm, not a
   measurement.

## Risks

- **The live call is the plan's single point of failure.** If Vertex's Opus 4.8 accepts
  `enabled`, task 1 is unnecessary; if it rejects `adaptive` too, the whole approach fails and
  the fallback is staying on Gemini. Run verification step 2 **first**, before writing code.
- **Substrate change, again.** Campaign 08 ran writer `gemini-2.5-flash`. This is a third
  writer. Comparability with 07/08 is *not* restored by this change — only the writer ≠ scorer
  property is. Anything comparing across it needs the same caveat campaign 08's `old`
  condition got.
- **Cost per campaign roughly doubles** in percentage terms (~$4 → ~$9) and stays trivial in
  absolute terms.
- **Opus could be re-enabled as an agent.** Task 3's guard turns that into a red build rather
  than a silent confound, which is the point of writing it.
