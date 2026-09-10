# Enhanced per-run report: stage economics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-10-per-run-report-economics.md` and commit it —
> plan mode could only write to the scratch plan path.

**Goal:** Make the per-run report answer "where did the money and the time actually go, and what did we buy" — instead of one blended cost row.

**Architecture:** Both `results` builders already load per-stage artifacts and then sum them away. Preserve the split, add one reporter section that reads it, and reuse the existing floor/verdict machinery for the value side. No new dependencies, no new commands.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`.

---

## Context

The report has exactly one cost view: `_cost_benefit_section` (`wrangler/reporting/reporter.py:557`), a per-arm row of blended $/M, measured spend, quality/$ and $/quality-point. It is careful work — two cost bases side by side, `n/a` rather than `$0.00` when tokens were not recorded — and it should be extended, not replaced.

What it cannot show is **which stage** spent anything, because both builders sum the stages before the reporter sees them:

- `wrangler/pipeline/components.py:1096` — `"token_usage": _summed_usage(eval_before, optimize_data, eval_after)`
- `wrangler/orchestration/stages.py:991` — `"token_usage": _sum_token_usage(...)`, the same shape

Costs and elapsed are accumulated into run-level `totals` only (`components.py:1099-1103`). The per-stage numbers exist in `stages/<stage>/<pair>.json` and are read a few lines earlier — they are discarded, not missing.

**Why it matters, from the real `c07-pro` run:**

| | dollars | wall clock |
| --- | --- | --- |
| optimize | $0.328 | 30,742 s (**87%**) |
| eval (before + after) | **$0.575** | ~4,550 s |
| total | $0.903 | 35,293 s (9.8 h) |

**Dollars and hours point at different stages.** Eval is where the money goes; optimize is where the clock goes. At $0.90 a run the dollars are close to irrelevant and the binding constraints are wall clock and judge RPM — a campaign is scheduled in hours, not budget. The current report can express none of this.

Two further facts the data supports and the report omits: output tokens are **90% of volume and 98% of cost** (3,900 in vs 35,840 out for `c07-pro`), so response verbosity is the cost lever and prompt length essentially is not; and every token figure carries `is_estimate: true`, which no column currently discloses.

**Decided:** cross-model comparison is *not* in scope — every campaign 07 run has exactly one arm, so it belongs in a separate campaign-level rollup. **Decided:** `$/surviving point` renders `uncalibrated` when the run has no control arm (its control lives in a different run), matching the precedent in `_per_metric_verdict_lines`. No `--floor-from` plumbing.

---

## Task 1: Preserve the per-stage split in both `results` builders

**Files:**
- Modify: `wrangler/pipeline/components.py:1066-1103`
- Modify: `wrangler/orchestration/stages.py:975-996`
- Test: `tests/test_stage_economics.py` (new)

Add a `stage_usage` key alongside the existing `token_usage`. Do **not** remove `token_usage` — `_cost_benefit_section` reads it and the summed value stays correct.

Shape, per pair:

```python
"stage_usage": {
    "eval_before": {"input_tokens": 0, "output_tokens": 0, "elapsed": 0.0,
                    "input_usd": 0.0, "output_usd": 0.0, "is_estimate": True},
    "optimize":    {...},   # {} when absent — eval-only runs have no optimize stage
    "eval_after":  {...},
}
```

Read straight from the stage dicts already in scope (`eval_before`, `optimize_data`, `eval_after`): `.get("token_usage", {})`, `.get("costs", {})`, `.get("elapsed")`.

**Step 1 — write the failing test.**

```python
def test_stage_usage_keeps_stages_separate_not_summed():
    """The summed total cannot answer 'which stage spent this'.

    c07-pro: optimize was 87% of wall clock but only 36% of dollars. Summing the
    stages before the reporter sees them makes that unrecoverable, and it is the
    single most useful thing the cost data can say.
    """
```

**Step 2 — run it, confirm it fails** on the missing key.
Run: `uv run pytest tests/test_stage_economics.py -v`

**Step 3 — implement in both builders.** The two are hand-synced; `components.py` already carries the comment *"Same forwarding as the local report path"*. Keep them identical in shape.

**Step 4 — verify.** `uv run pytest tests/test_stage_economics.py -v`

**Step 5 — commit.**

> **Note:** editing `components.py` invalidates the KFP cache for `generate-analysis` only. It does **not** move the pipeline image tag (that is `md5(pyproject.toml + uv.lock + Dockerfile.pipeline)`).

---

## Task 2: Guard the two builders against drift

**Files:** Modify `tests/test_shared_source_drift.py`

The builders are hand-synced and now share one more key. That file already guards two such pairs (`models.py` vs the example config; `_REGISTRY_PY_TEMPLATE` vs `registry.py`), so this is an established pattern, not a new one.

Assert **by behaviour**, as the file's existing tests do: feed both builders equivalent stage dicts and require the same `results[pair_id]` keys. Source comparison was explicitly rejected there and should be here too.

If a behavioural comparison needs more scaffolding than it is worth, assert the narrower thing — that both produce the same key set — and say so in the docstring.

---

## Task 3: The stage economics section

**Files:**
- Modify: `wrangler/reporting/reporter.py` (new `_stage_economics_section`)
- Test: `tests/test_stage_economics.py`

Reuse `measured_cost` (`wrangler/core/models.py:327`) for pricing; it already returns `priced=False` for unregistered models rather than a misleading `$0.00`.

Three tables, in this order:

**a. Where it went.** Per stage: dollars, share of dollars, wall clock, share of clock. Lead with whichever share is most lopsided.

**b. Token asymmetry.** Input vs output tokens and cost per arm, with output share. The finding to surface: output dominates, so verbosity is the lever.

**c. Rate.** `$/hour` and `tokens/hour` per stage — this is what makes "the money is negligible, the clock is the constraint" legible.

**Every table carries an estimate marker** where `is_estimate` is true. A precise-looking dollar figure that was never metered is the same class of error as the `$0.00`-vs-`n/a` distinction the existing code already gets right.

**Step 1 — failing tests first**, covering: stages render separately; a missing optimize stage (eval-only run) does not crash; an unpriced model renders `unpriced` not `$0.00`; `is_estimate` surfaces.

**Steps 2-5** — implement, verify, commit.

---

## Task 4: Cost per surviving point

**Files:** Modify `wrangler/reporting/reporter.py:_cost_benefit_section`

Today `$/quality pt` prices the raw delta. Pricing movement that has not cleared the noise is how spend gets justified against noise — and campaign 07 has already shown a metric moving 12.3x its floor between two runs of one manifest.

Reuse, do not reimplement: `measure_noise_floor_per_metric` and `classify_deltas` (`wrangler/reporting/analyzer.py:271, 395`), exactly as `_per_metric_verdict_lines` (`reporter.py:94`) already calls them — including the "drop floors that measured exactly 0.0" rule, which is subtle and already correct there.

- Floors present → price only metrics whose verdict is `improved`. A regression prices as `—`, never as negative value.
- Floors absent (the common case: one-arm runs) → render **`uncalibrated`** and a single line pointing at `wrangler floor <control-run-id>`.

**Test the uncalibrated path first** — it is the one that will actually render on campaign 07 runs, and a column that silently prints a plausible number without a floor is the failure this task exists to prevent.

---

## Task 5: Wire in, and verify against real artifacts

**Files:** Modify `wrangler/reporting/reporter.py:generate_report`

Place the new section under the existing **"What it cost"** layer, which PR #60 already created. Keep `_cost_benefit_section` first (the per-arm summary), then stage economics (the breakdown) — same progressive-disclosure rule as the rest of the document.

**Verify end-to-end against the real run, not fixtures:**

```bash
gsutil -m cp -r gs://$GCP_STAGING_BUCKET/pipeline-runs/run-5aa73d6191/reports /tmp/c07check
# re-render from the real summary.json + stage artifacts, then:
uv run python -c "from wrangler.reporting.html_report import write_self_contained_html as w; \
                  w('/tmp/c07check/experiment_report.md')"
```

Open the HTML and confirm the optimize-vs-eval split reads correctly and the numbers match the table in *Context* above ($0.328 / $0.575, 87% of clock).

---

## Task 6: Documentation

**Files:** `docs/notes/README.md`, and `CLAUDE.md` only if a convention changed.

Record the finding, not just the feature: **dollars and wall clock point at different stages, and at ~$0.90/run the binding constraint is hours and judge RPM rather than budget.** That reframes how campaign cost should be discussed and is the durable part of this work.

---

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1192 today), plus `ruff check`, `ruff format --check`, `ty check wrangler/`.
2. **The image tag has not moved:**
   ```bash
   uv run python -c "from pathlib import Path; from wrangler.pipeline.deploy_pipeline import _compute_image_tag; print(_compute_image_tag(Path('pyproject.toml'),Path('uv.lock'),Path('Dockerfile.pipeline')))"
   ```
   must print `aff07d2d60f3` (the value after PR #60; `d7cb178fe46d` if #60 has not merged).
3. **Rendered against real `c07-pro` artifacts**, not only fixtures — the numbers in *Context* are the expected output.
4. **An eval-only run still renders.** `optimize` is absent there and every new table must degrade rather than raise.

## Risks

- **`is_estimate: true` on every token figure.** These are estimates presented to the dollar. If they are not labelled the report will be read as metering. Labelling is a requirement of Task 3, not a nicety.
- **Two builders, one shape.** Task 1 edits mirrored code; Task 2 exists because a fix landing in only one of them would look correct locally and ship wrong through the pipeline — the exact failure `docs/notes/repo-traps.md` records for the two `config.py` files.
- **`components.py` cache invalidation.** Fine now (campaign 07 is finished); it would forfeit a cache hit mid-campaign.

## Out of scope

- **Cross-model comparison** — needs a campaign-level rollup across run ids, because each run has one arm.
- **`--floor-from` / manifest-declared floors** — decided against; uncalibrated is the honest default.
- **Quality-vs-cost frontier charts** — four points at n=1 with error bars wider than the spread. Revisit when repeats exist.
