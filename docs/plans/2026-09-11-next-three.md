# Where the next effort is worth spending — top three

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-11-next-three.md` and commit it —
> plan mode could only write to the scratch plan path.

**Goal:** Raise the rate at which this project learns something true, rather than adding
more machinery for verifying things it already knows.

**Architecture:** One area removes the ceiling on every future campaign, one spends that
capacity on the only open product question, one stops the next long run being wasted. In
that order, because the first is cheap and compounds.

**Tech Stack:** Python 3.11, `uv`, Vertex AI Managed Pipelines, GCP quotas console.

---

## Context

The repo is in unusually good shape: 1264 tests, no open PRs, guards on model ids, regions,
image pins, sampler configs, redeploy health, and artifact overwrites. Fourteen silent
failures are catalogued and twelve are closed.

What it has produced in return is **one** replicated product finding: GEPA improves
`safety_v1`, a criterion it is scored on, and degrades `instruction_following_v1`, the
holdout — reproduced across two model families, and the only two effects that survived both
the noise floor and the run-to-run spread.

That ratio is the thing to fix. The constraint is not rigour and it is not tooling; it is
that a campaign costs 22-44 hours and most of that is one API quota.

Three measurements make the case:

- **Optimize is 87% of every run** — 515 min of a 618-min arm, against 13-15 min deploy and
  ~80 min for both evals.
- **Optimize is judge-bound.** A *single* arm drew **70 × HTTP 429** in 52 generations;
  campaign 07 saw 76 with *two* arms running. Concurrency cannot help: throughput is fixed.
- **Every Gemini 3.x model in the registry is `rpm=5`**, the judge included, while the
  agent `claude-sonnet-5` sits at `rpm=2000`. Three orders of magnitude of unused headroom
  on the wrong side of the bottleneck.

One concern checked and cleared while writing this: PR #69 took
`rubric_based_final_response_quality_v1` from 2 rubrics to 4, which would have slowed every
generation if rubrics cost a call each. They do not —
`rubric_based_evaluator.py:433` parses a single autorater response into per-rubric verdicts.

---

## Area 1 — Raise the judge quota. Highest return per hour in the repo.

**Files:** `docs/escalations/2026-09-11-judge-quota.md` (new)

`gemini-3.5-flash` at 5 requests/minute is the single number that sets campaign length. At
~10× it, campaign 08 is an afternoon rather than two nights, campaign 10's budget curve
becomes affordable at the repeats it actually needs, and every future design stops being
shaped around wall clock.

We cannot file it — it is a console action against the project. What we can do is make it
**one click and impossible to refuse**, which is the part that has stalled the other
escalation for 19 days.

**Step 1.** Assemble the evidence already measured: 429 counts per arm, optimize duration,
the 87% share, and the arithmetic tying them together. All of it is in
`docs/notes/repo-traps.md` and the campaign 07 wrap-up; this is collation, not new work.

**Step 2.** State the ask precisely — metric name, model, current limit, requested limit,
and the workload that justifies it. Pick the requested number from measured demand rather
than a round figure.

**Step 3.** Note the fallback in the same document: `gemini-2.5-flash` is `rpm=100` and
cheaper, but it **retires 2026-10-16** and swapping it changes the instrument that scores
the rubrics — rejected for campaign 08 for that reason, and the reasoning belongs on the
record so it is not re-litigated.

**Step 4.** While in the escalations directory, get
`docs/escalations/2026-08-23-geap-empty-stream.md` over the line. It has been *ready to
file* since 2026-08-23 — 31.7% of GEAP requests return 200 with no inference, and the ask
is narrow. Re-verify the evidence has not gone stale and hand Jordan both filings together.

**Neither of these is ours to submit. Both should leave here as a paste-ready document and
a one-line summary of where it goes.**

---

## Area 2 — Run campaign 08, rescoped. The only new knowledge on offer.

**Files:** `scripts/run_campaign.py`, `scripts/validate_then_run.py`,
`docs/doe/08-criteria-holdout.md`, `tests/test_sampler_configs.py`

The design was settled in the previous planning round: **drop the concurrent baseline, run
two repeats of the new criteria plus a control per batch, ~22h.** It screens the criteria
change in or leaves it unresolved; it cannot rule it out, and the write-up says so.

**Step 1.** `CAMPAIGNS["08"]` becomes two batches — `c08-new-r1 + c08-ctrl-a`, then
`c08-new-r2 + c08-ctrl-b`. Replace the comment block; it describes a crossed two-condition
design that no longer exists. Keep the one fact worth not rediscovering: one optimizing arm
per batch, and pairing would not have helped anyway because the judge is the constraint.

**Step 2.** Move `VALIDATION_ARM["08"]` to `c08-new-r1`.
`test_every_validation_arm_is_part_of_its_own_campaign` will fail first — that guard exists
for exactly this. Let it fail, then fix it.

**Step 3.** Rewrite the pre-registration as a **screening** design, with the readings fixed
before any data: a large effect screens in and justifies the staged baseline arms as a
controlled follow-up; anything near campaign 07's −0.062 is **unresolved**, not a negative
result, because PR #59 and #75 moved the substrate underneath the comparison.

**Step 4.** Mark `BASELINE_CONFIGS` in the test file as **staged, not live** — the
directory it exempts is now unused, and an exemption for something nothing runs is how dead
weight accumulates. The two tests pinning its shape stay; they are what stops it drifting
into a copy of the treatment while it sits idle.

**Step 5.** Launch, and treat the first arm as a gate. **It also confirms the silent
failure #12 fix for free**: expect `will run without the tools` at **0**, against 14-15%
before. If it is unchanged, the refresh was not the cause either and the reproduction in
`scripts/repro_mcp_refresh_hang.py` is the place to resume.

---

## Area 3 — Make the driver survive its own campaign.

**Files:** `scripts/run_campaign.py`, `scripts/validate_then_run.py`

The driver holds state in memory across a 22-44 hour run and sleeps between batches. Two
things have already gone wrong, and campaign 08 is about to re-run the same gauntlet:

- **It outlives ADC credentials.** On 2026-09-09 it slept through a token expiry and the
  next `submit()` died on *"Reauthentication is needed"*, killing a run between two arms.
  `OPTIMIZE_STAGGER` was cut from 90 to 40 minutes so each individual sleep fits inside one
  token lifetime — the code says outright that this is a mitigation, not a fix, and that a
  driver sleeping for hours will eventually straddle a reauth however short each nap is.
- **It can die without anyone noticing.** During this session the driver process vanished
  along with its `/tmp` log, and nothing surfaced it — the campaign simply would not have
  progressed. It was found by checking, not by being told.

**Step 1.** Refresh credentials immediately before each `submit()` rather than relying on
the token acquired at start. This is the narrow fix the existing comment already prescribes.

**Step 2.** Make the driver resumable from Vertex rather than from memory. `--watch-job`
already adopts a running validation arm; the same idea generalises — derive remaining
batches from what has and has not run, so a dead driver is restartable instead of a lost
campaign.

**Step 3.** Write the driver's log somewhere that survives, not `/tmp`. `outputs/campaigns/`
is already the `--log-dir` default and is not swept.

**Step 4.** Guard what can be guarded hermetically: that a resumed driver skips completed
batches, and that credential refresh is called on the submit path. Both are AST- or
fake-testable in the style the repo already uses.

---

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1264 today), plus `ruff check`,
   `ruff format --check`, `ty check wrangler/`.
2. **Area 2's launchability guards pass** — every campaign has a validation arm, it exists,
   and it belongs to its own campaign. Step 2 exists because the last one fails first.
3. `validate(CAMPAIGNS["08"])` clean, 2 batches / 4 arms, and the four staged manifests
   still produce distinct `run_id`s so the follow-up needs no rework.
4. **Area 3's resume path is exercised against a fake job lister**, not against Vertex.
5. Image tag unchanged at `aff07d2d60f3` — nothing here touches `pyproject.toml`,
   `uv.lock` or `Dockerfile.pipeline`.

## Also worth 20 minutes, not an area

**Two status lines contradict their own documents.** `silent-failures.md` #12 still reads
*"the underlying cause is still open"* and #14 *"Status: open"*, while both carry a
**Fixed 2026-09-10** section further down. Anyone scanning statuses gets the wrong picture
of what is outstanding — in the one file whose whole purpose is knowing that.

## Risks

- **Area 1 depends on someone else.** Google may refuse the quota, and neither filing is
  ours to submit. It is still the best hour available, because the payoff is a standing
  multiplier and the cost is collation of numbers already measured.
- **Area 2's most likely outcome is "unresolved".** Campaign 07's effect was −0.062 against
  a ~0.015 floor; a partial improvement lands in the band the design cannot read. Going in
  knowing that is the point of pre-registering it.
- **`main` must stay frozen while campaign 08 runs.** `submit()` packages the working tree
  at each batch, so a mid-campaign merge changes the substrate between batches — which is
  the confound the rescope is already exposed to. Area 3's work should land **before** the
  launch or **after** the campaign, not during.

## Explicitly not recommended

- **More guards.** The suite is 1264 tests and the last three failures were all caught by
  existing ones. Marginal return is now low.
- **Concurrency between arms.** Measured not to help; one arm already saturates the judge.
- **DOE 02 and 03** (judge variance, noise floor re-measurement). Both are good questions
  and both are instrument work — the same trap campaign 08 was just rescued from. Revisit
  once a campaign is cheap enough that characterisation is not a two-day commitment.
