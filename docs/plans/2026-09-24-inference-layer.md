# Inference Layer Implementation Plan (research idea 2)

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make a campaign state up front whether it can answer its own question, and make every
reported delta a paired per-case comparison rather than a difference of two run means.

**Architecture:** Two independent pieces, both reusing code that already exists. (1) Lift the MDE
machinery out of `scripts/partition_mde.py` into an importable module and surface it as a
**warn-only** third check in `wrangler preflight`. (2) Teach the reporting path to compute deltas
by pairing on `case_index` — `paired_deltas()` already exists and `PairAnalysis` already carries
the per-case data; `classify_deltas` simply isn't using it.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`. No new dependencies.

---

## Context

Every delta this repo publishes is a bare point estimate. `grep -rniE
"bootstrap|confidence|p_value|significan" wrangler/reporting/` returns **zero hits**. The
consequences are not hypothetical:

- **Campaign 09 was filed UNRESOLVED** on a readout that a per-case paired contrast later showed
  to be a clean null (+0.0000, CI ±0.0714). The resolution was in the artifacts all along.
- **The MDE gate built for idea 1 (PR #124) found that campaign's published +0.0952 effect sat at
  roughly the boundary of 80% power** — the whole 64-case eval set needs 76–99 cases to resolve
  it. Nothing in the harness said so, before or after.

Idea 1 is closed: the eval set will not grow (decision 2026-09-24). That makes idea 2 the
substitute, and it is cheaper than when it was written, because the gate is already half of it.

**One piece of idea 2 is deliberately excluded.** The research report recommended Wilson score or
Bayesian beta-binomial intervals, citing Bowyer et al. (ICML 2025 Spotlight). That recommendation
targets **binary** metrics. Ours are bounded-continuous with wildly varying granularity —
`safety_v1` has **4** distinct values, `instruction_following_v1` has **187** — so no single
method can simply be adopted. Choosing one responsibly needs a coverage simulation against our six
real eval sides. That is a separate piece of work; this plan does the two parts that need no
unsettled method choice.

---

## What already exists (reuse, do not rewrite)

| Thing | Where | Note |
| --- | --- | --- |
| `paired_deltas(before, after)` | `wrangler/eval/evaluator.py:526` | per-metric deltas over cases **both** sides scored; returns `n_paired`, `dropped_before`, `dropped_after` |
| `pair_per_case()` | `wrangler/eval/evaluator.py` | the pairing primitive underneath it |
| `PairAnalysis` | `wrangler/reporting/analyzer.py:27` | **already carries** `before_per_case` / `after_per_case`; its `.deltas` property ignores them |
| `classify_deltas(pair, floor)` | `wrangler/reporting/analyzer.py:484` | the decision point — improved / regressed / within-noise / uncalibrated |
| `measure_noise_floor_per_metric(pairs)` | `wrangler/reporting/analyzer.py:271` | per-metric floors from control arms, built from `pair.deltas` |
| `floor_from_control_arm()` | `wrangler/reporting/analyzer.py:192` | **the house pattern to copy**: reports `unpaired` *and* `paired` side by side rather than silently switching |
| MDE + variance decomposition | `scripts/partition_mde.py` | includes the σ²-invariance identity and the clamping behaviour; numbers are pinned by tests |
| `PreflightResult` / `run_preflight()` | `wrangler/tools/preflight.py:105,121` | `(name, ok, detail)`; adding a third check is additive |

---

## Task 1 — Lift the MDE machinery into an importable module

**Files:** Create `wrangler/reporting/inference.py` · Modify `scripts/partition_mde.py` ·
Test `tests/test_inference.py`

`scripts/` is not importable from `wrangler/`, so the gate's arithmetic cannot currently be reused.
Move `variance_components`, `Components.mde`, `Contrast`, `per_case_contrast` and the z-multiplier
constant across; leave the CLI, printing and fixture-loading in the script and have it import from
the new module.

**The numbers must not move.** The script's output is a published NO-GO. Before committing, run it
and diff the full numeric output against the pre-move run — every data row byte-identical.

**Tests:** the σ²-invariance identity (`bracket_at(K_OBSERVED) == total`) must survive the move —
copy that test across; it is the property the NO-GO rests on.

## Task 2 — Compute a manifest's MDE

**Files:** Modify `wrangler/reporting/inference.py` · Test `tests/test_inference.py`

Add `mde_for_design(*, n_cases, num_runs, variance_source) -> dict[str, float]` returning the MDE
per metric for a proposed design.

**Where the variance comes from matters and must be explicit.** The only measured per-case variance
we have is campaign 09's, committed at `tests/fixtures/c09/`. So the check's claim is *"against
campaign 09's measured variance, this design resolves X"* — never an assumed variance. Put that
sentence in the output, not just the docstring. CLAUDE.md is emphatic that a floor is not a
property of a metric and must be re-measured; the same caveat applies here.

Read `num_runs` and the eval-set size from the manifest via the existing `PairFactory.load()` path
rather than re-parsing YAML.

## Task 3 — Surface it in `wrangler preflight`, warn-only

**Files:** Modify `wrangler/tools/preflight.py` · Modify `wrangler/cli.py:1019` ·
Test `tests/test_preflight_mde.py`

Add a third `PreflightResult`. **`ok` must stay `True` regardless of the MDE** — this warns, it
does not block. Every campaign run so far would trip it, and a check that blocks on day one gets
switched off rather than heeded. The numbers go in `detail`.

**The load-bearing test:** a design whose MDE exceeds its target still yields `ok=True`, and
`run_preflight()`'s overall exit behaviour is unchanged. Assert the existing two checks still
report exactly as before — regressing dependency resolution to add a statistics warning would be a
bad trade.

`preflight` currently takes no manifest. Add an optional argument; with no manifest, the MDE check
is skipped rather than guessed.

## Task 4 — Pair the deltas, and the floor, together

**Files:** Modify `wrangler/reporting/analyzer.py` · Test `tests/test_paired_reporting.py`

Give `PairAnalysis` a `paired_deltas` property built on `evaluator.paired_deltas()` over its
existing `before_per_case` / `after_per_case`, falling back to the aggregate when per-case data is
absent (older artifacts have none).

**Pair the floor at the same time or not at all.** `measure_noise_floor_per_metric` builds floors
from `pair.deltas`. If deltas become paired while floors stay unpaired, `classify_deltas` compares
a paired delta to an unpaired threshold — apples to oranges, and it would silently shift every
verdict. Both move together, in one commit.

**Report both, do not silently switch.** Follow `floor_from_control_arm`'s existing pattern:
surface `unpaired` and `paired` side by side with `n_paired`. Pairing changed this repo's floor by
44–64% on a real arm where CLAUDE.md claimed ~15%, so the two numbers disagreeing is information,
not noise to hide.

**Acceptance test — reproduce a known answer.** `docs/analysis/2026-09-22-campaign-09-reanalysis.md`
records paired deltas computed by hand from these exact artifacts: control **+0.0794**,
rationale-on **+0.1746**, rationale-off **+0.1719** on `safety_v1`. The new code must reproduce
them from `tests/fixtures/c09/`. If it does not, the pairing is wrong — that document is the
reference.

## Task 5 — Say what changed

**Files:** Modify `CLAUDE.md` · Create `docs/analysis/<date>-paired-reporting-switch.md`

Task 4 moves published numbers. Record the before/after per metric on the campaign 09 fixtures and
the date, so campaign-to-campaign comparisons crossing this boundary carry the same caveat as the
2026-09-17 judge re-baseline. CLAUDE.md already lists those boundaries; add this one.

---

## Verification

Per task, before the PR:

1. `uv run pytest tests/ -q` — **1707 passing** at base, coverage ≥ 60% (the ratchet).
2. `uv run ruff format --check . && uv run ruff check .` clean; `uv run ty check wrangler/` clean.
3. **Task 1 only:** `uv run python scripts/partition_mde.py` output diffs clean against the
   pre-move run, and still exits **1** (NO-GO) / **2** (unmeasurable).
4. **Task 3:** `uv run wrangler preflight` still behaves as today with no manifest, and
   `uv run wrangler preflight --manifest manifests/c09-rationale_manifest.yaml` prints an MDE
   block and still exits 0.
5. **Task 4:** the campaign 09 paired numbers above are reproduced exactly.

Nothing here touches `wrangler/pipeline/components.py` or `dag.py`, so no KFP cache is invalidated
and this can merge under a live campaign.

## Risks

- **Task 4 changes published numbers.** That is the point, but it must be visible. Reporting both
  paired and unpaired, plus the Task 5 note, is what keeps it from being a silent re-baseline.
- **Task 1 could perturb a published verdict.** The NO-GO is a decision already acted on. The
  output diff in verification step 3 is the guard; treat any difference as a blocker, not a
  rounding detail.
- **Pairing is not free when coverage differs.** `paired_deltas` drops unmatched cases, so a badly
  dropped arm yields a paired delta over few cases. `n_paired` must be reported beside every
  paired number — `floor_from_control_arm` already does this and the reason is recorded there.
- **The MDE rests on one campaign's variance.** Campaign 09 is a single run per condition, so each
  variance estimate carries that run's luck. The check must say so in its output every time.
