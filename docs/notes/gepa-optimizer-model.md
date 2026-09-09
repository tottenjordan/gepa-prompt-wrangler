# GEPA writes prompts with a model nobody chose

**Found 2026-09-09**, reading campaign 07's optimize logs.

GEPA runs **two** models, doing unrelated jobs:

| role | job | who chose it |
| --- | --- | --- |
| **judge** | *scores* candidate prompts against the eval set | us — `judge_model`, set to `gemini-3.5-flash` by the [2026-08-20 A/B](model-lifecycle.md) |
| **optimizer model** | reads the failures and *writes* the next candidate prompt | **ADK's default** |

`GEPARootAgentPromptOptimizerConfig.optimizer_model` defaults to
**`gemini-2.5-flash`**, described upstream as "the model used to analyze the
eval results and optimize the agent". Nothing in this repo sets it.

Campaign 07's optimize phase made **331 calls to `gemini-3.5-flash`** (the
judge, correct) and **69 to `gemini-2.5-flash`** (the optimizer, unconfigured),
interleaved. Its 78 → 1,647 character prompt was written by the latter.

## This is not a correctness problem

Scoring is the judge, and the judge is the one the A/B chose. Campaign 07's
numbers are sound on this axis. What is unchosen is the *creative* half — the
model reading failures and proposing better wording.

## It is a lifecycle problem, and it has a date

`gemini-2.5-flash` **retires 2026-10-16**.

`tests/test_models.py` already fails the build when a named-role default comes
within 30 days of retirement. It cannot fire here: it inspects the roles *this
repo declares*, and an ADK default is not one of them. Left alone, the first
sign would be GEPA erroring against a retired model part-way through a
ten-hour optimize.

`tests/test_adk_optimizer_model.py` closes that. It reads ADK's default from
the live config rather than hardcoding the string — hardcoding would make the
test pass forever after the very thing it guards against — and applies the same
30-day bar. **It passes today (37 days) and starts failing 2026-09-16.**

## Logged is not guarded

`optimize/optimizer.py:505` already prints the value, and campaign 07's log
carries `[c07-sonnet5]   Optimizer model: gemini-2.5-flash`. The information
was never hidden. Nobody reads a log line and checks a retirement date 37 days
out, which is the difference between observable and guarded — the same
distinction as the ADK log-string dependency in
[silent-failures.md](silent-failures.md).

## When the guard fires, it is a decision and not a bump

Two honest options, and they differ in more than effort:

- **Set `optimizer_model` explicitly.** Then we own it, it appears in the
  registry's named-role table, and the existing retirement guard covers it like
  every other role. It also means choosing — and nobody has measured which
  model writes better prompts. The judge A/B exists because that question was
  worth measuring for the judge; the same question for the optimizer has never
  been asked.
- **Track ADK's default deliberately.** Keep inheriting it, but with this test
  making each change visible. Cheaper, and reasonable if ADK's choice is
  better-informed than ours.

Either way the test should stay: an unguarded dependency on somebody else's
default is what this note is about, and it survives whichever option is taken.
