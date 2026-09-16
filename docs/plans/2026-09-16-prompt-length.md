# Prompt-length regularization: correct the claim, preserve the evidence

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-16-prompt-length.md` and commit it.

**Goal:** Retract an overclaim I made in a merged document, and make the underlying question
answerable by keeping the data GEPA already produces and we currently throw away.

**Explicitly NOT building the length-constrained proposer.** The evidence does not support it.

**Tech Stack:** Python 3.11, `uv`, KFP v2 components, GCS.

---

## Context

In PR #87 and in conversation I recommended prompt-length regularization as *"plausibly a
bigger lever than the writer id, and better evidenced"*. **Tested against our own artifacts,
that is wrong.**

Every optimize stage already records `original_chars` and `optimized_chars`. Pairing those
with the holdout delta across all seven arms that have both:

| cohort | n | `r(optimized_chars, Δ instruction_following_v1)` |
| --- | --- | --- |
| all arms | 7 | **+0.373** |
| optimized only (drop the arm that returned its seed) | 6 | **+0.699** |
| **clean substrate only (c07 + c08)** | **4** | **−0.112** |
| `claude-sonnet-5` only, model held constant | 3 | **+0.470** |

**The sign flips with the subset, and the only comparable set is indistinguishable from no
relationship.** A positive `r` means *longer* prompts had a *smaller* holdout regression —
the opposite of the hypothesis. Nothing here supports the mechanism in either direction; it
is n=4 with a confounded cohort.

The hypothesis itself remains reasonable — it is GEPA's documented failure mode, and
campaign 07 did grow prompts 78 → 3,873 characters while five arms showed criterion-up /
holdout-down. What is missing is any evidence that *length* is the channel, and arm-level
data cannot supply it: one run yields **one** data point.

### The data that would settle it exists, and we delete it

GEPA writes a `run_dir` (`outputs/gepa_runs/<app_name>`, set in
`wrangler/optimize/optimizer.py:486`). Inspected on a surviving local run:

```
3.7 MB total
  candidates.json        every candidate prompt -- PLAIN JSON, no unpickling
  gepa_state.bin         14 candidates + per-candidate validation subscores
  run_log.json           iteration log
  candidate_tree.html    the search tree
  generated_best_outputs_valset/…
```

**14 candidate prompts from a single run, spanning 78 to 12,741 characters**, each with its
validation score. One 4-arm campaign would yield ~56 points across a far wider length range
than the 1,493–5,370 the arm-level data covers.

The optimize component uploads the final prompt and the MCP logs and nothing else, so the
`run_dir` dies with the container. That is the same failure the MCP log upload was added to
fix — `components.py:717` says so: *"Without this it dies with the container, which is what
made silent-failures #12 undiagnosable."*

**Honest limitation, to be stated wherever this data is used:** the per-candidate scores are
GEPA's *criteria*, not the holdout. `instruction_following_v1` is not a criterion and is
never scored during search. So this data can test whether length drives the *criterion* —
the first half of the overfitting story — and cannot, on its own, test the holdout half.

---

## Task 1: Correct the record

**Files:** `docs/analysis/2026-09-16-writer-model-survey.md`,
`docs/doe/11-writer-scorer-identity.md`

**Step 1.** The survey's closing section says prompt-length regularization is "plausibly a
bigger lever than the writer id, and better evidenced". Replace with the cohort table above
and the finding that the correlation is unstable in sign and ~zero on the comparable set.
Keep the hypothesis as a hypothesis — the literature and the 78 → 3,873 growth are real —
but drop "better evidenced", which was the false part.

**Step 2.** The survey's recommendation 3 currently reads *"Investigate length regularization
before running any writer A/B"*. That ordering was justified by the overclaim. Rewrite it as:
preserve the candidate data, and revisit when a campaign has produced some.

**Step 3.** DOE 11 carries a line saying a writer A/B should run first once quota lands. That
survives — nothing here changes it — but add that the length question is now *unsupported*
rather than *promising*, so it does not silently become the next campaign.

Do not delete the reasoning. **The way this was wrong is the useful part**: an appeal to
published literature plus one internal correlation, neither checked against the data sitting
in our own bucket.

## Task 2: Preserve `run_dir` in the optimize component

**Files:** `wrangler/optimize/optimizer.py`, `wrangler/pipeline/components.py`,
`tests/test_optimize_run_dir.py` (new)

**Step 1 — write the failing test.** Assert by AST that the optimize component uploads the
run directory, in the style of `tests/test_deploy_health_gate.py` — and extract the function
with `ast.get_source_segment`, **not** a fixed-size slice. That test was rewritten for
exactly this reason on 2026-09-16 and several siblings still slice.

**Step 2.** Export the path derivation from `optimizer.py` so both the optimizer and the
component use one definition:

```python
def gepa_run_dir(agent_module_path: str) -> Path:
    """Where GEPA writes candidates, scores and its search tree."""
```

`optimize()` currently computes it inline at line 486 as
`os.path.join("outputs", "gepa_runs", os.path.basename(agent_module_path))`. Call the helper
there instead of duplicating the expression. A component may import from `wrangler` — the
KFP isolation rule only forbids calling module-level helpers defined *in* `components.py`.

**Step 3.** In `optimize_single_agent`, after the existing MCP-log upload block
(`components.py:717-725`), upload every file under the run dir to
`pipeline-runs/{run_id}/stages/optimize/gepa_run/{pair_id}/…`.

Copy that block's discipline exactly: wrapped in `try/except Exception`, logging a warning
and never raising. **Nothing here may fail a nine-hour stage.** Add a total-size cap
(100 MB is ~27× the observed 3.7 MB) that logs and stops rather than uploading without
bound, since `generated_best_outputs_valset/` grows with the eval set.

**Step 4.** Note in the component comment that `candidates.json` is plain JSON, so the
analysis path needs neither `pickle` nor a `gepa` import.

## Task 3: An analysis tool for the data once it exists

**Files:** `scripts/analyze_candidate_lengths.py` (new)

Follow `scripts/analyze_toolset_loss.py` and `scripts/probe_optimizer_models.py`: arguments
not constants, a docstring that says what a wrong result means, and a guard that
distinguishes "clean" from "looked in the wrong place".

Reads `candidates.json` for a run (local path or `gs://`), and reports per candidate its
prompt length and validation score, plus the correlation across the run. It must:

- **Say `n` in the output**, next to every correlation. The whole reason this plan exists is
  a correlation quoted without its sample size.
- **Print the cohort caveat**: these scores are GEPA's criteria, not the holdout.
- **Refuse to report a correlation below some small n** rather than emit a number that will
  be quoted.

Run it against the surviving local run (`outputs/gepa_runs/sonnet_opt`, 14 candidates) to
prove it works on real data today.

---

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1291 today), `ruff check`,
   `ruff format --check`, `ty check` at its 21-diagnostic baseline.
2. **The upload cannot be verified without a pipeline run, so verify the parts that can be.**
   Unit-test `gepa_run_dir()` directly; AST-test that the component calls the upload; and run
   `analyze_candidate_lengths.py` against `outputs/gepa_runs/sonnet_opt` to prove the reader
   works on a real `candidates.json`.
3. Confirm `gepa_run_dir()` returns the same path `optimize()` used before the refactor —
   a changed path would silently write GEPA's state somewhere new.
4. Image tag unchanged at `aff07d2d60f3`.

## Risks

- **This edits `components.py`, so the optimize component takes a KFP cache miss** on its
  next run. Nothing is running and campaign 08 is complete, so the cost is zero today — but
  it must not be merged under a live campaign (silent-failures #13).
- **The data answers the criterion half, not the holdout half.** Anyone reading
  per-candidate scores as evidence about `instruction_following_v1` will be wrong. Say it in
  the script output, not only in a doc.
- **Preserving data is not the same as answering the question.** This plan makes the
  question answerable at zero marginal cost from the next campaign onward; it does not
  answer it. Resist reporting a within-run correlation as a result until there is more than
  one run.
- **3.7 MB × arms × campaigns accumulates.** Small, but it is per-arm forever; the size cap
  and the `stages/optimize/gepa_run/` prefix keep it prunable.
