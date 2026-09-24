# Cost-Quality Frontier Instrument Implementation Plan (research idea 4)

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> Copy this file to `docs/plans/2026-09-24-cost-quality-frontier.md` as the first commit.

**Goal:** Replace a scatter plot that averages five metrics into one scalar and prices models at
list rate, with a per-metric cost-quality frontier that carries intervals, counts frontier
membership per metric, and answers whether optimizing a cheap tier moves it past an expensive one.

**Architecture:** One new pure-arithmetic module plus a CLI command, following
`campaign_floor.py`'s existing shape exactly — thin GCS fetch, pure computation, markdown render.
Cross-run assembly, because every campaign was submitted one arm per pipeline job. The chart
becomes a secondary rendering of a table that stands on its own.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`, PaperBanana for figures. No new deps.

---

## Context

`generate_cost_quality_chart` plots `np.mean(list(before_scores.values()))` — **all five metrics
collapsed to one number** — against `blended_cost(model)`, which is **list price at an assumed 4:1
input:output ratio**, with **no uncertainty**. Per-metric floors span 3.4×, and campaign 09
measured metrics moving in *opposite* directions (holdout +0.077 against quality −0.075). The
average hides precisely the tradeoff the chart exists to show.

This is the idea the original research brief asked for by name: *"help users better understand the
cost-quality tradeoff between a set of models."*

### Three findings from exploration that change the idea as written

1. **Nothing is metered.** The report says to plot "measured" cost. But
   `evaluator.py:_estimate_token_usage` computes `len(text) // 4`, every artifact carries
   `is_estimate: True`, and **`usage_metadata` is referenced nowhere in `wrangler/`**. The
   inference DataFrame has columns `[prompt, session_inputs, expected_tool, case_description,
   reference, intermediate_events, response, agent_data]` — no usage anywhere, including nested.
   Decision (2026-09-24): build on estimated cost and **label it estimated everywhere**. It is
   still a real improvement over `blended_cost`, because it reflects actual response verbosity,
   which is what separates tiers. Real metering would mean reworking the managed
   `run_inference()` path that produces every campaign number — disproportionate for a
   second-order gain.

2. **There is no multi-tier pipeline job.** Every `run-*` in GCS holds a single pair, except
   `run-86239e1924` (campaign 09) which holds three arms of the *same* model. So the instrument
   **must assemble across run ids**. The cross-publisher data that exists is campaign 07:
   `c07-pro`, `c07-sonnet5`, `c07-ctrl-sonnet5`.

3. **The only multi-model data is contaminated.** CLAUDE.md marks campaign 07's tool-use numbers
   uninterpretable (12–24% silent-failure-12 contamination), and it predates both the 2026-09-17
   judge re-baseline and the 2026-09-18 fix. Decision: build and validate against it, but **every
   number the tool prints from a pre-2026-09-18 run must carry that caveat in the output**, not
   just in a doc.

---

## What already exists (reuse, do not rewrite)

| Thing | Where | Note |
| --- | --- | --- |
| `fetch_arms(run_ids, bucket)` | `wrangler/reporting/campaign_floor.py:137` | **the exact precedent** — maps run ids to `{arm: (eval_before, eval_after)}` and nothing else, so the arithmetic stays testable without a bucket |
| `summarize_arms` / `render_markdown` | `campaign_floor.py:25,74` | the shape to copy: pure computation, then rendering |
| `wrangler floor <run-ids>` | `wrangler/cli.py:375` | the CLI pattern to mirror |
| `per_case_delta`, `per_case_contrast`, `common_cases`, `EvalSide`, `load_eval_sides` | `wrangler/reporting/inference.py` | per-case layer from idea 2 — pairing and intervals already solved |
| `mde_for_design`, `campaign_09_variance` | `wrangler/reporting/inference.py` | the interval/MDE machinery |
| `measured_cost(model, in_tok, out_tok, custom)` | `wrangler/core/models.py:381` | returns `priced` flag for unregistered models — render it, never a bare `$0.00` |
| `blended_cost` | `wrangler/core/models.py:358` | what we are moving *away* from; keep for the list-price column |
| `stage_economics.py` | `wrangler/reporting/` | per-stage cost and wall clock; the "eval is 64% of dollars, optimize is 87% of clock" split |
| `PairAnalysis`, `delta_comparison` | `wrangler/reporting/analyzer.py` | paired per-case deltas with intervals |

**Artifacts already carry everything needed.** Each `stages/eval_{before,after}/{pair}.json` has
`scores`, `per_case` (per-case per-metric), `scores_std`, `token_usage`, `costs`, `coverage`.
Verified against `tests/fixtures/c09/`.

---

## Task 1 — Pareto arithmetic, per metric

**Files:** Create `wrangler/reporting/frontier.py` · Test `tests/test_frontier.py`

Pure functions over plain dicts, no GCS and no matplotlib:

- `frontier_for_metric(points, metric) -> list[str]` — the non-dominated arms. A point dominates
  another when it is **both cheaper and better**.
- `frontier_membership(points) -> dict[str, int]` — per-arm count of metrics it is on the frontier
  for. **This table, not the chart, is the primary readout** — it is HAL's recommended format and
  the reason is that a pooled score hides the disagreement between metrics.

**Dominance must respect uncertainty, and this is the load-bearing design decision.** With
intervals on every point, "better" cannot mean "greater by any margin". Require the quality
difference to **exceed the interval** before calling it domination; otherwise the two arms tie and
**both** are frontier members. Without this the frontier is decided by noise, which is the same
error the averaged scalar makes in a different costume.

**Tests:** a clearly dominated arm is excluded; two arms differing by less than the interval are
both members; an arm cheapest on one metric and worst on another appears in exactly one count;
an empty or single-arm input does not raise.

## Task 2 — Cost per arm, from artifacts, labelled estimated

**Files:** Modify `wrangler/reporting/frontier.py` · Test `tests/test_frontier.py`

`cost_for_arm(before, after, model, custom_costs) -> dict` summing `token_usage` across both eval
sides through `measured_cost()`.

**Three things the output must carry, because each is a way to be misread:**

- `is_estimate: True` — propagated from the artifacts, rendered in every table and axis label as
  *estimated $*. There is no metered number in this system and the output must not imply one.
- `priced: False` for an unregistered model — an unpriced arm renders as `n/a`, never `$0.00`.
  `measured_cost`'s docstring already explains why; honour it.
- The convention: **agent inference only, excluding judge/grader spend.** Artificial Analysis
  notes published costs routinely omit this. Say which we use, in the output.

## Task 3 — Assemble arms across runs

**Files:** Modify `wrangler/reporting/frontier.py` · Test `tests/test_frontier.py`

`summarize_frontier(arms, variance_source) -> dict` taking `fetch_arms`' exact output shape, so
the GCS reader is **reused rather than rewritten**. Produces per-arm cost, per-metric quality with
intervals, per-metric frontiers, and the membership table.

**Reject incomparable arms loudly.** `campaign_floor.COVERAGE_GAP_LIMIT` exists because a delta
between a 47%-covered and an 89%-covered side measures dropout, not quality. The same applies
here: an arm whose coverage gap exceeds the limit is excluded from the frontier with its reason
printed, not silently plotted.

**Flag pre-2026-09-18 runs in the output itself.** Any arm from a run predating the
silent-failure-12 fix carries a contamination note beside its `tool_use_quality_v1` number. A
caveat in a doc does not travel with a copied table.

## Task 4 — The two questions nothing currently asks

**Files:** Modify `wrangler/reporting/frontier.py` · Test `tests/test_frontier.py`

- `crosses_tier(arms, cheap, expensive, metric) -> dict` — **does optimizing the cheap tier's
  prompt move it onto or past the expensive tier's frontier point?** Compare cheap-after against
  expensive-before, per metric, with the interval. This is the demo-worthy result and all the data
  for it already exists.
- `complementarity(arms, a, b, metric) -> dict` — the fraction of cases `a` gets right that `b`
  gets wrong, and vice versa, over `common_cases`. FrugalGPT measured 13% on COQA; a non-trivial
  number here is what justifies a tier choice over a leaderboard ranking. Reuse
  `inference.common_cases` so pairing is done the one way this repo does pairing.

## Task 5 — `wrangler frontier`, mirroring `wrangler floor`

**Files:** Modify `wrangler/cli.py` · Modify `wrangler/reporting/frontier.py` ·
Test `tests/test_frontier_cli.py`

`render_markdown(summary) -> str` plus a command taking run ids, exactly as `cli.py:375` does for
`floor`. Default output is the **membership table and the tier-crossing verdict**; the chart is
opt-in.

**Do not make this a required step of the report.** `floor` is a standalone command for the same
reason — a cross-run analysis whose inputs are chosen by hand should not run implicitly.

## Task 6 — Replace the averaged scatter, in both implementations

**Files:** Modify `wrangler/reporting/charts.py` · Modify `wrangler/reporting/analysis.py:101`

`generate_cost_quality_chart_pb` (PaperBanana, primary) wraps `generate_cost_quality_chart`
(matplotlib) as its **fallback** — `charts.py:312` passes it as `fallback_fn`. **Both must change
or the fallback silently renders the old averaged view**, which is worse than either alone because
it depends on whether PaperBanana happened to be reachable.

Small multiples: one panel per metric, cost on x (estimated, labelled), quality on y with interval
bars, before→after arrow per tier, frontier line per panel. Per CLAUDE.md, PaperBanana draws it;
matplotlib is only the fallback.

## Task 7 — Record it

**Files:** Modify `CLAUDE.md` · Create `docs/analysis/<date>-cost-quality-frontier.md`

State that no token count in this system is metered and every cost is an estimate from `len/4`;
that frontier membership is reported per metric rather than pooled, and why; and the c07 result
with its caveats. The write-up is the deliverable the original brief asked for.

---

## Verification

1. `uv run pytest tests/ -q` — **1840 passing, 4 skipped** at base; coverage ≥ 60% (currently
   73.22%).
2. `uv run ruff format --check . && uv run ruff check .` clean; `uv run ty check wrangler/` clean.
3. **The idea-1 gate is untouched:** `uv run python scripts/partition_mde.py` still prints md5
   `9488b203824abd64df465605600ba559` and exits 1.
4. **End to end on real data:**
   `uv run wrangler frontier run-5aa73d6191 run-8a5905dee0 run-70166a6bc8`
   (`c07-pro`, `c07-sonnet5`, `c07-ctrl-sonnet5`) prints a per-metric membership table, marks the
   tool-use numbers contaminated, and labels every dollar figure estimated.
5. **Arithmetic is testable without a bucket** — every test in `tests/test_frontier.py` runs off
   `tests/fixtures/`, mirroring how `campaign_floor`'s arithmetic is tested. If a test needs GCS,
   the split between fetch and computation is wrong.
6. **Both chart paths render:** force the matplotlib fallback and confirm it produces per-metric
   panels, not the averaged scatter.

Nothing here touches `wrangler/pipeline/components.py` or `dag.py`, so no KFP cache is invalidated
and this can merge under a live campaign.

## Risks

- **The frontier is only as good as the cost estimate.** `len/4` ignores tokenizer differences
  between publishers, and this is a *cross-publisher* comparison. A systematic bias between Gemini
  and Claude tokenization would tilt the x-axis. Say so in the output; do not quietly present it
  as billing.
- **c07 is contaminated and single-run.** One run per condition, so the frontier reflects which
  prompt GEPA happened to find — the term CLAUDE.md measures at 12.3× the control floor. The tool
  is sound; the c07 *result* is a demonstration, not a finding, and must be labelled as one.
- **Frontier membership with intervals is a judgement call.** Requiring domination to exceed the
  interval is defensible and stated, but a different rule gives a different table. Put the rule in
  the rendered output, not only in the code.
- **Two chart implementations.** The repo already carries several hand-synced pairs and guards
  them with tests (`test_shared_source_drift.py`). Add a test that both paths produce per-metric
  output rather than trusting the sync.
