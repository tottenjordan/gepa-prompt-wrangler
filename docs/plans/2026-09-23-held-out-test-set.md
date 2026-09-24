# Held-Out Test Set Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` skill to implement this plan task-by-task.

**Goal:** Give the harness a test partition GEPA never sees, so that "GEPA improved the metric"
can be distinguished from "GEPA memorised the cases it was optimized against."

**Architecture:** Keep scoring all 64 cases — do **not** shrink the eval set. Introduce a
three-way *partition manifest* (train / validation / test) as a single source of truth, remove
the test ids from every `sampler_config.json` so GEPA cannot reach them, and split the existing
per-case scores by partition at report time. The train−test gap *is* the overfitting measurement.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`. No new dependencies.

---

## Why this plan looks different from the obvious one

The obvious plan — "split the cases, compare train vs test" — was tested against campaign 09's
existing per-case data before this plan was written, and **it does not work as stated**:

| arm | `safety_v1` train Δ | val Δ | gap |
| --- | --- | --- | --- |
| `c09-rationale-on` | +0.1146 | +0.0333 | +0.0812 |
| `c09-rationale-off` | +0.0885 | +0.1167 | −0.0281 |
| **`c09-control`** (optimizes nothing) | +0.0521 | +0.1667 | **−0.1146** |

**The control arm shows the largest train/val gap, in the opposite direction.** An arm that ran
no optimize stage cannot overfit, so the gap is a property of the two *case subsets*, not of
optimization: the strata are unmatched (train has 18 tier×category cells, validation 11) and
n=15 is tiny.

Two consequences this plan is built around:

1. **The split must be stratified** on `tier` × `category`, or composition swamps the signal.
2. **The comparison must be control-corrected.** The readout is
   `(treatment train−test gap) − (control train−test gap)`, never the raw gap. The control's own
   gap is the composition baseline and it is not zero.

A third consequence is a genuine open question, addressed as a decision gate in Task 2: **64
cases may simply be too few to split three ways.** Miller's MDE formula
(`docs/analysis/2026-09-23-harness-improvement-research.md`, idea 2) says the question-sampling
term ω² shrinks only with more questions — no amount of re-scoring fixes a 12-case test set.

---

## Task 1: The partition manifest

A single source of truth for which case belongs to which partition, stratified, with the mapping
between the two id schemes the repo already uses.

**Files:**
- Create: `examples/multi_model_agents/eval_data/partitions.yaml`
- Create: `wrangler/core/partitions.py`
- Test: `tests/test_partitions.py`

**Background you need.** Cases live in `examples/multi_model_agents/eval_data/eval_cases.yaml`
as a 64-element list with no `id` field. Two identifiers exist and both are positional:

- `wrangler/core/converter.py:294` builds GEPA's id as `f"case_{i + 1}_{tier}_{category}"` —
  **1-based**.
- Eval artifacts record `case_index`, which is the **0-based** list position.

Verified: reconstructing ids this way matches all 64 in the checked-in sampler configs.

**Step 1: Write the failing test**

```python
# tests/test_partitions.py
"""The partition manifest is the only thing that may say which case is which.

Two id schemes exist and both are positional -- GEPA's `case_{i+1}_{tier}_{category}` and the
eval artifacts' 0-based `case_index`. A mapping bug silently reassigns cases between train and
test, which would make the overfitting measurement report noise with total confidence.
"""
from wrangler.core.partitions import load_partitions, case_ids, PARTITIONS


def test_every_case_is_in_exactly_one_partition():
    p = load_partitions()
    allocated = [i for part in PARTITIONS for i in p[part]]
    assert sorted(allocated) == list(range(64))
    assert len(allocated) == len(set(allocated)), "a case appears in two partitions"


def test_gepa_ids_are_one_based_and_match_the_converter():
    """`converter.py:294` uses i+1. Off-by-one here silently shifts every case."""
    from wrangler.core.converter import _cases_from_yaml  # add if absent; see Step 3

    ids = case_ids()
    assert ids[0].startswith("case_1_")
    assert len(ids) == 64


def test_the_split_is_stratified_on_tier_and_category():
    """The control arm's train/val gap (-0.1146 on safety) came from unmatched strata.
    Composition must not differ across partitions or it is measured as an effect."""
    p = load_partitions()
    strata = _strata_fractions(p)
    for cell, fracs in strata.items():
        spread = max(fracs.values()) - min(fracs.values())
        assert spread <= 0.15, f"{cell} is unbalanced across partitions: {fracs}"
```

**Step 2: Run it to confirm it fails**

```bash
uv run pytest tests/test_partitions.py -v
```
Expected: `ModuleNotFoundError: No module named 'wrangler.core.partitions'`

**Step 3: Write the minimal implementation**

`wrangler/core/partitions.py` exposes `load_partitions() -> dict[str, list[int]]` keyed by
`"train" | "validation" | "test"` with 0-based indices, `case_ids() -> list[str]` producing the
1-based GEPA ids, and a `stratified_split()` helper that generates the manifest.

Generate the manifest once and **check it in** — a split recomputed per run is a different
experiment each time.

**Step 4: Run tests**

```bash
uv run pytest tests/test_partitions.py -v
```

**Step 5: Commit**

```bash
git add wrangler/core/partitions.py tests/test_partitions.py examples/multi_model_agents/eval_data/partitions.yaml
git commit -m "feat: stratified train/validation/test partition manifest"
```

---

## Task 2: Decision gate — is 64 cases enough to split three ways?

**Do not skip this and do not proceed on assumption.** If the test partition's MDE exceeds the
effects we care about, the honest answer is to generate more cases first, and Tasks 3–5 are
premature.

**Files:**
- Create: `scripts/partition_mde.py`
- Test: `tests/test_partition_mde.py`

**Step 1: Write the failing test**

```python
def test_mde_uses_measured_per_case_variance_not_an_assumption():
    """Miller's formula separates question-sampling variance (omega^2, reducible only by more
    questions) from per-question variance (sigma^2/K, reducible by re-scoring). Our measured
    score_repeats exponent of ~0 on safety_v1 is that omega^2 term; a plausible-looking MDE
    built on an assumed variance would hide exactly the constraint we are testing for."""
    mde = compute_mde(n=12, per_case_sd=REAL_SD_FROM_C09, alpha=0.05, power=0.8)
    assert mde > 0
```

**Step 2–4:** Implement `compute_mde` using
`δ = (z_α/2 + z_β)·sqrt((ω² + σ²_A/K_A + σ²_B/K_B)/n)`, feeding ω² and σ² from campaign 09's
per-case artifacts (already downloadable from
`gs://<bucket>/pipeline-runs/run-86239e1924/stages/eval_{before,after}/`). Report the MDE at
n = 12, 16, 20, 24 test cases.

**Step 5: Record the decision in the plan file itself**

The observed effects to compare against: the DiD optimization effect was **+0.0952** on
`safety_v1`, and the rationale on−off contrast was **±0.075** on the four secondary metrics.
If MDE at the chosen test size exceeds those, **stop and generate cases**.

```bash
git add scripts/partition_mde.py tests/test_partition_mde.py
git commit -m "feat: MDE calculator for the test partition, as a go/no-go gate"
```

---

## Task 3: Make the test partition unreachable by GEPA

**Files:**
- Modify: the seven `examples/multi_model_agents/agents/*_opt/sampler_config.json`
- Create: `scripts/sync_sampler_partitions.py`
- Test: `tests/test_test_partition_is_sealed.py`

**Background.** `wrangler/core/converter.py:392 generate_sampler_config()` can write these files
but **has no callers** — all seven are hand-maintained. Do not hand-edit seven files; write the
sync script and let the test enforce the invariant.

**Step 1: Write the failing test — this is the load-bearing one**

```python
"""No test-partition case id may appear in any sampler config, ever.

This is the whole feature. A test case that leaks into `train_eval_case_ids` is optimized
against and the held-out measurement silently becomes another in-sample one -- with no symptom,
because the number still looks plausible.
"""
import glob, json
from wrangler.core.partitions import load_partitions, case_ids

def test_no_sampler_config_contains_a_test_case():
    ids = case_ids()
    test_ids = {ids[i] for i in load_partitions()["test"]}
    for path in glob.glob("examples/multi_model_agents/agents/*_opt/sampler_config.json"):
        cfg = json.load(open(path))
        declared = set(cfg.get("train_eval_case_ids", [])) | set(
            cfg.get("validation_eval_case_ids", [])
        )
        leaked = declared & test_ids
        assert not leaked, f"{path} exposes held-out cases to GEPA: {sorted(leaked)[:5]}"

def test_every_sampler_config_declares_a_split():
    """An absent key means 'use all cases' (converter.py:409) -- i.e. silent full exposure."""
    for path in glob.glob("examples/multi_model_agents/agents/*_opt/sampler_config.json"):
        cfg = json.load(open(path))
        assert cfg.get("train_eval_case_ids"), f"{path} has no train split; GEPA sees everything"
```

Note the second test: `converter.py:409` documents that a missing `train_eval_case_ids` means
**use all cases**. Omission is not a safe default here.

**Step 2:** Run — expect failures on all seven files.

**Step 3:** Write `scripts/sync_sampler_partitions.py` to rewrite the seven configs from
`partitions.yaml`, preserving each file's existing `criteria` block verbatim (those are tuned
per agent and are **not** what this task changes).

**Step 4:** Run the tests, then the full suite — `test_sampler_configs.py` asks ADK directly
about metric registration and must stay green.

```bash
uv run pytest tests/ -q
```

**Step 5: Commit**

```bash
git commit -m "feat: seal the test partition out of every sampler config"
```

---

## Task 4: Partition-aware, control-corrected reporting

**Files:**
- Modify: `wrangler/reporting/analyzer.py`
- Test: `tests/test_partition_reporting.py`

**Step 1: Write the failing test**

```python
def test_the_gap_is_reported_relative_to_the_control():
    """A raw train-test gap is not an overfitting measurement. Measured on campaign 09: the
    control arm -- which ran no optimize stage and therefore cannot overfit -- had a train/val
    gap of -0.1146 on safety_v1, larger than either treatment's. Composition, not optimization.
    So the readout is (treatment gap) - (control gap)."""
    out = partition_report(treatment=TREAT, control=CTRL, partitions=P)
    assert out["safety_v1"]["raw_gap"] == pytest.approx(0.08, abs=0.01)
    assert out["safety_v1"]["control_corrected_gap"] == pytest.approx(0.19, abs=0.01)

def test_a_run_without_a_control_reports_uncorrected_and_says_so():
    """The same discipline `_cost_per_surviving_point` already applies: a plausible number with
    no baseline behind it is the failure the report exists to prevent."""
    out = partition_report(treatment=TREAT, control=None, partitions=P)
    assert out["safety_v1"]["control_corrected_gap"] is None
    assert "uncorrected" in out["note"].lower()
```

**Steps 2–4:** Implement, following the existing convention in
`reporter.py:_cost_per_surviving_point` — when the baseline is missing, say so loudly rather
than emitting a number.

**Step 5: Commit**

```bash
git commit -m "feat: report per-partition deltas, control-corrected"
```

---

## Task 5: Re-read campaign 09 through the new lens, and write down whatever it says

**Files:**
- Create: `docs/analysis/2026-XX-XX-campaign-09-partition-reread.md`

Campaign 09's artifacts have per-case scores for all six eval sides, so the train/validation
split can be applied retroactively at zero compute cost. **Validation is not a clean held-out
set** — GEPA uses it for candidate selection — so this is a weak proxy, and the preflight above
already showed it is confounded by composition.

Report it anyway, with the control correction and an interval, and state plainly if it remains
uninformative. A null here is a real result: it says the proxy cannot answer the question and
only a true test partition will.

```bash
git commit -m "docs: campaign 09 re-read by partition"
```

---

## Task 6 (conditional): planted canary cases

Only if Task 2 said 64 cases suffice *and* Task 5 left the overfitting question open.

A self-improving-pipeline study (pith.science, 2026-09-02) plants cases "engineered so that a
perfect score is itself evidence of cheating" — a case whose correct behaviour is to *refuse* or
to *report inability*, where a high rubric score means the optimizer taught the agent to satisfy
the grader rather than the user. This is a different detector from the train/test gap and
catches reward hacking the gap cannot.

---

## Verification

Per task, before the PR:

1. `uv run pytest tests/ -q` — **1649 passing today**, coverage ≥ 60 (the ratchet).
2. `uv run ruff format --check . && uv run ruff check .` clean.
3. `uv run ty check wrangler/` clean.
4. `uv run pytest tests/test_sampler_configs.py -v` — asks ADK directly about metric
   registration; must stay green after Task 3 rewrites the configs.

**No KFP cache implications.** Nothing here touches `wrangler/pipeline/components.py` or
`dag.py`, so this can merge at any time, including under a live campaign.

## Risks

- **64 cases may not survive a three-way split.** This is the main risk and Task 2 exists to
  surface it before any code depends on the answer. The failure mode to avoid is a 12-case test
  partition reported with the same confidence as a 64-case one.
- **Changing the split invalidates cross-campaign comparison.** Every historical number was
  measured with train=49/val=15. Once the partition manifest changes, campaign-to-campaign
  comparisons cross a boundary — the same class of caveat as the 2026-09-17 judge re-baseline.
  Record the change date in `CLAUDE.md` beside the other boundaries.
- **The seven sampler configs carry hand-tuned `criteria` blocks.** Task 3 must rewrite only the
  id lists. A script that regenerates the whole file would silently revert the
  `INSTRUCTION_ADHERENCE` rubric work.
- **This weakens GEPA's training signal.** Moving cases out of train reduces what the optimizer
  learns from; RoboPhD (arXiv 2026-08-17) suggests small pools can still generalize (66–100
  examples → 267–900 held out), but that is their result on their tasks, not a promise about
  ours.
