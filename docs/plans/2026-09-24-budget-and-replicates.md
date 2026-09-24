# Budget Discipline and Replicates Implementation Plan (research idea 3)

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> Copy this file to `docs/plans/2026-09-24-budget-and-replicates.md` as the first commit.

**Goal:** Stop spending ~11h of optimize budget per arm after the search has converged, and spend
the savings on the n=2 replicates CLAUDE.md requires and no campaign has ever run.

**Architecture:** Three pieces, each reusing machinery that already exists. (1) Replay stopping
rules offline against archived `gepa_state.bin` files to pick a patience from our own runs rather
than a heuristic. (2) Inject gepa's shipped `NoImprovementStopper` and a per-replicate `seed`
through `_gepa_extra_kwargs()`, the same hook that already carries `use_merge`. (3) Expand
`replicates: N` into N ordinary pairs at manifest-load time, which the DAG, the artifact layout
and the reporting path all already handle.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`. No new dependencies.

---

## Context

An optimize stage costs ~11 hours and a flat `max_metric_calls: 600` whether the search converged
at generation 20 or never converged at all. `gepa.optimize` accepts `stop_callbacks` and this repo
passes none. It also accepts `seed` (default `0`), which this repo has never set, so two
"replicates" would share a search schedule. And nothing in the manifest, `PairFactory` or the DAG
can express a replicate at all — despite CLAUDE.md requiring one and the campaign 09 reanalysis
explicitly needing n=2 before Result 3 counts as more than a lead.

**The freed budget buys replicates, and that is the point.** GEPA's search variance is the
dominant term in this repo's uncertainty: two runs of one manifest — same seed, model, criteria,
budget, shared cached `eval_before` — produced `eval_after` scores differing by up to **12.3× the
control-arm floor**. The MDE machinery added for idea 2 does not model that term *at all*; it
covers case-sampling and eval noise only. So today's MDE numbers flatter single-run arms, and
replicates are how the missing term gets measured for the first time.

### The racing allocator is deliberately out of scope

Idea 3's headline was a cross-arm race (Sequential Halving / Successive Rejects). **It is dropped,
and this section exists so it is not re-proposed.** A race eliminates arms on partial data.
Against campaign 09's measured variance:

| design | MDE range across the five metrics |
| --- | --- |
| n=64, K=2 (the full eval, what we run today) | 0.060 – 0.103 |
| n=32, K=2 (an elimination call halfway) | 0.085 – 0.146 |
| n=16, K=2 (an early elimination call) | 0.120 – 0.206 |

The between-arm differences we care about are ~**0.075** (campaign 09's secondary contrasts) and
~**0.0952** (its DiD effect). The *full* eval is already at the boundary; eliminating on a
fraction of it would be a coin flip. That is precisely irace's documented failure mode — non-elitist
racing "measurably discards genuinely-best configurations." Idea 1 closed the door on growing the
eval set (decision 2026-09-24), so this does not become viable later without a new instrument.

Reproduce the table with `wrangler.reporting.inference.mde_for_design`.

### Honest arithmetic on what this funds

A replicate is a whole extra arm: deploy + health gate (~12 min), two eval sides (~16.5 min each),
redeploy + gate, and an optimize stage. Early stopping saves **optimize time only**. If it cuts
optimize by ~40% (MO-CAPO reached 80% of final hypervolume at 32.3% of budget), three arms free
roughly one and a half arms' worth of wall-clock. **So this funds roughly one extra arm per three,
not a blanket n=2.** Task 1 replaces that guess with a measurement before anything is promised.

---

## What already exists (reuse, do not rewrite)

| Thing | Where | Note |
| --- | --- | --- |
| `_gepa_extra_kwargs()` | `wrangler/optimize/optimizer.py:134` | returns a dict injected into `gepa.optimize`; **filtered against the live signature**, so an argument gepa drops degrades to "not passed" |
| `_patch_gepa_optimize()` | `wrangler/optimize/optimizer.py:170` | applies it; caller-supplied values win. Calls `_gepa_extra_kwargs()` **fresh on every invocation**, so run-scoped module state is read correctly |
| `NoImprovementStopper(max_iterations_without_improvement)` | `gepa.utils.stop_condition` | **ships with gepa** — do not write a stopper. `CompositeStopper`, `ScoreThresholdStopper`, `TimeoutStopCondition` are there too |
| `gepa_run_dir(agent_module_path)` | `wrangler/optimize/optimizer.py:570` | locates the run dir the pipeline archives |
| `_pairs_json(manifest)` | `wrangler/pipeline/deploy_pipeline.py:31` | the per-pair payload the DAG forwards verbatim; iterates `enabled_pairs`, so expanded replicates flow through with **no DAG signature change** |
| `PairAnalysis.is_control` | `wrangler/reporting/analyzer.py:158` | keyed off the prompt being unchanged, **not** an id convention — so replicated controls register correctly, and the floor machinery already says "control arm(s)" |
| `mde_for_design(...)`, `campaign_09_variance()` | `wrangler/reporting/inference.py` | added by idea 2; the source of the table above |
| `AgentPromptPair.forward_rationale` / `.skip_optimize` | `wrangler/core/factory.py` | **the precedent to copy** for a per-pair campaign factor |

### Two facts that shape the whole plan

1. **ADK forwards only 8 of `gepa.optimize`'s arguments** (`gepa_root_agent_prompt_optimizer.py:297`),
   and neither `seed` nor `stop_callbacks` is among them. Both are injectable via the existing hook
   with **no ADK patch** and no change to `docs/notes/adk-patch-status.md`.
2. **`optimize()` already receives the pair id** — `components.py:723` passes `agent_name=pair_id`.
   So a per-replicate seed can be derived inside `optimizer.py`, which rides in the **code tarball,
   not a component body**. Tasks 1–4 therefore bust **no KFP cache** and can merge under a live
   driver. Only Task 5 touches `components.py`; it is isolated for exactly that reason.

---

## Task 1 — Replay stopping rules against real runs (no new spend)

**Files:** Create `scripts/replay_stopping.py` · Create `wrangler/optimize/trajectory.py` ·
Test `tests/test_trajectory.py`

Campaign 09's `gepa_state.bin` survives in GCS for both optimizing arms
(`pipeline-runs/run-86239e1924/stages/optimize/gepa_run/{c09-rationale-on,c09-rationale-off}/`).
It carries every candidate with its validation subscores, so the question "what would patience *p*
have cost us?" is answerable for free, against our own runs.

**Step 1 — derisk the pickle before building anything.** Download one `gepa_state.bin` and confirm
it unpickles under the installed gepa. It is a pickle, and CLAUDE.md's rule about captures
("scratch, not archive — an SDK bump can render an old one unloadable") applies here too. If it
does not load, stop and report; the rest of this task rests on it.

**Step 2 — extract the trajectory.** In `trajectory.py`, read the state into a plain list of
`(candidate_index, val_aggregate_score, budget_consumed)`. Keep this module free of GCS and CLI
concerns so it is testable from a fixture.

**Step 3 — mirror the real stopper, and pin the mirror.** Read `NoImprovementStopper`'s source and
mirror its notion of "improvement" exactly. **A replay using a different predicate than the live
stopper is worthless**, so write a test that runs the mirror and the real `NoImprovementStopper`
over the same synthetic trajectory and asserts they fire on the same index. That test is the
load-bearing one in this task.

**Step 4 — report the tradeoff.** For patience ∈ {3, 5, 8, 10, 15}: budget consumed at the stop
point as a fraction of total, and best-score-at-stop minus best-score-at-end. Write the result to
`docs/analysis/2026-09-24-stopping-replay.md`.

**The output is a decision, not a number in a file:** the patience Task 3 defaults to. Do not
proceed to Task 3 with a guess.

## Task 2 — Make the injection hook run-scoped

**Files:** Modify `wrangler/optimize/optimizer.py` · Test `tests/test_gepa_extra_kwargs.py`

`_gepa_extra_kwargs()` returns a constant `{"use_merge": True}`. Give it run-scoped state — a
module-level dict set by `optimize()` and cleared in a `finally`, following `_GEPA_PATCH_STATE`'s
existing shape — so per-run values can reach the call.

**`use_merge: True` must survive unchanged.** It is inert but deliberate, and
`tests/test_merge_is_inert.py` pins the upstream semantics it rests on. Assert it is still injected
when the new state is empty.

**Test the filter still works:** a key absent from the live `gepa.optimize` signature is dropped,
not passed. That guard is what stops a `TypeError` nine hours into a stage, and it must keep
holding for the two new keys.

## Task 3 — Inject `stop_callbacks`, default off

**Files:** Modify `wrangler/optimize/optimizer.py` · Test `tests/test_early_stopping.py`

Add a `patience: int | None = None` parameter to `optimize()`. When set, put
`NoImprovementStopper(patience)` into the run-scoped dict. When `None`, inject nothing and behave
exactly as today.

**Default off, and stay off until a manifest asks.** `max_metric_calls` remains the ceiling; this
only ever stops *earlier*. Shipping it on would change every in-flight campaign's behaviour on the
strength of a replay.

**Tests:** (a) `patience=None` injects no `stop_callbacks` and the injected dict is byte-identical
to today's; (b) `patience=5` injects a stopper that is an instance of gepa's own class — not a
local lookalike; (c) an explicit caller-supplied `stop_callbacks` still wins, per the hook's
documented contract.

## Task 4 — Replicates as pairs, with distinct seeds

**Files:** Modify `wrangler/core/factory.py` · Modify `wrangler/optimize/optimizer.py` ·
Test `tests/test_replicates.py`

**Expand at load time.** `PairFactory.load()` turns `replicates: N` on a pair into N pairs with ids
`{id}-r1 … {id}-rN`, identical in every other field. Everything downstream then works untouched:
`run_id` hashes the pair-id list, artifacts are keyed `{run_id}/stages/{stage}/{pair_id}.json`, and
`_pairs_json` iterates `enabled_pairs`. **No DAG change, no component change, no new artifact
layout.**

**Derive the seed from the pair id** inside `optimizer.py`, from the `agent_name` it already
receives — a stable hash, not `random`. This is what keeps the change out of `components.py`.
Replicates then differ in gepa's minibatch schedule as well as in LLM sampling, which is what makes
them independent draws rather than two samples of one schedule.

**Do not implement common random numbers.** Sharing a seed across arms (irace's variance-reduction
device) only pairs gepa's *internal* minibatch sampling. The numbers this repo reports come from
the batch eval service, which never sees gepa's seed — so CRN would buy nothing here and would cost
the plumbing through `components.py` that everything above is structured to avoid.

**Tests:** (a) `replicates: 2` yields two pairs with distinct ids and otherwise-equal fields;
(b) absent or `replicates: 1` leaves the pair list byte-identical to today — every existing manifest
must be unaffected; (c) the derived seeds differ between replicates and are stable across processes
(call it twice in a subprocess, not just twice in one); (d) `replicates` on a `skip_optimize`
control expands too — a replicated control is a better floor estimate and is explicitly wanted.

**Note for the campaign that uses this:** adding `replicates:` to an existing manifest changes the
pair-id list and therefore `run_id`, invalidating that manifest's entire KFP cache. That is correct
— it is a different campaign — but it means the first replicated run pays full price for every
stage.

## Task 5 — Protect the file this all depends on (isolated: busts a KFP cache)

**Files:** Modify `wrangler/pipeline/components.py` · Test `tests/test_pipeline_component_bodies.py`

The run_dir uploader iterates `sorted(run_dir_path.rglob("*"))` under a 100 MB budget
(`components.py:791`). Alphabetically `generated_best_outputs_valset/` sorts **before**
`gepa_state.bin` — and that directory is the one the existing warning says "grows with the eval
set." So the first file dropped when the budget blows is the one the surrounding comment calls
"the ONLY per-candidate record of the run," and the one Task 1 depends on. There is 27× headroom
today; the ordering is a latent trap, not a live bug.

Upload `gepa_state.bin` and `candidates.json` first, before the budget can be consumed.

**Commit this separately and merge it in a between-campaigns window.** It is a component body, so
it busts the optimize component's cache, and per silent-failures #13 it must not merge under a live
driver. Everything in Tasks 1–4 is free of that constraint.

## Task 6 — Say what changed

**Files:** Modify `CLAUDE.md` · Create `docs/analysis/2026-09-24-stopping-replay.md` (from Task 1)

Record in CLAUDE.md: that `seed` and `stop_callbacks` are now injected roles alongside `use_merge`;
that `replicates:` exists and how it interacts with `run_id`; and **that the racing allocator was
evaluated and rejected, with the MDE table** — so the next reader of the research report does not
rebuild it. Note that any campaign running with a patience set is not budget-comparable to one
without.

---

## Verification

1. `uv run pytest tests/ -q` — **1760 passed, 4 skipped** at base; coverage ≥ 60% (the ratchet),
   currently 72.87%.
2. `uv run ruff format --check . && uv run ruff check .` clean; `uv run ty check wrangler/` clean.
3. **Regression guard on the idea-1 gate:** `uv run python scripts/partition_mde.py` still prints
   md5 `9488b203824abd64df465605600ba559` and exits 1. It caught a stray-stderr import regression
   once already.
4. **Task 3/4 together, without a campaign:** run the smoke manifest
   (`manifests/pipeline_smoke_manifest.yaml`, 5 cases, ~25–30 min) with `replicates: 2` and a
   patience set. Confirm two distinct pair ids appear in the stage artifacts, their seeds differ in
   the logs, and the optimize stage stops before `max_metric_calls`.
5. **Confirm the default path is untouched:** the same smoke manifest *without* `replicates:` or
   `patience:` produces the same pair-id list and the same injected kwargs as before the change.
6. **Task 5:** the component-body harness (`tests/pipeline_component_harness.py`) exercises the
   uploader with a `generated_best_outputs_valset/` large enough to exhaust the budget, and asserts
   `gepa_state.bin` is still uploaded.

## Risks

- **A patience chosen from two arms of one campaign.** Campaign 09 is n=1 per condition — the exact
  problem this plan exists to fix. Task 1's number comes from the same weak evidence base, so
  choose conservatively (prefer the longest patience with no measured score loss) and re-run the
  replay once replicated runs exist.
- **Early stopping adds its own variance.** If some runs stop at generation 20 and others at 80,
  that is a new source of between-run spread layered on search variance already measured at 12.3×
  the floor. This is why it defaults off and why campaigns crossing the boundary are flagged in
  Task 6 as not budget-comparable.
- **`gepa_state.bin` is a pickle.** Task 1 Step 1 derisks it up front. If a future gepa bump makes
  old states unloadable, the replay is not re-runnable against historical campaigns — which is an
  argument for landing Task 1 promptly rather than after the next dependency bump.
- **Replicates cost more than early stopping saves.** Stated in Context above; the plan must not
  promise blanket n=2 on the strength of the savings. Task 1's measurement settles what is
  affordable.
