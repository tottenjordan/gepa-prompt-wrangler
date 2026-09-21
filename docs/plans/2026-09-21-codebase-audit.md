# Python & cloud-native audit — diagnostic, refactor, execution

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan
> task-by-task. On execution, copy this file to `docs/plans/2026-09-21-codebase-audit.md`.

**Goal:** Make the code that runs unattended for 10–24 hours testable, and guard the
divergence that has already produced two production bugs — without touching the parts of this
repo that are already correct.

**Architecture:** Extract logic out of KFP component bodies into an importable
`wrangler/pipeline/_steps.py`, one stage per PR, leaving each `@dsl.component` a thin shell.
This is a *proven* pattern here, not a new one: component bodies already do
`from wrangler.*` 16 times after `sys.path.insert(0, "/app")`.

**Tech Stack:** Python 3.11, `uv`, `ruff`, `ty`, `pytest`, KFP v2, Vertex AI Pipelines.

---

## Context

This audit was asked for as a general PEP 8 / cloud-best-practice review. **Most of that
ground is already covered, and saying so is part of the finding** — a report that lists
generic issues this repo has already solved would waste the effort. What the audit actually
surfaced is narrow and specific: the riskiest code in the repo is also the least tested, and
the one structural duplication has already cost two bugs.

---

# Phase 1 — Diagnostic report

## CRITICAL

### C1 · The longest functions in the repo are the least tested, and they run for 10–24 h

`wrangler/pipeline/components.py`: **642 statements at 2% coverage.**

| function | lines | coverage |
| --- | --- | --- |
| `optimize_single_agent` (`components.py:465`) | **444** | ~2% |
| `generate_analysis` (`components.py:1118`) | **351** | ~2% |
| `deploy_single_agent` (`components.py:81`) | 204 | ~2% |
| `redeploy_single_agent` (`components.py:916`) | 195 | ~2% |

This is the code that runs a campaign. A defect here surfaces after hours of compute, and the
history bears that out — silent failure #12 lived in this path for weeks, #13 was a
serialization bug in this exact file, and the `skip_optimize` gap fixed this week was a
missing parameter in `redeploy_single_agent`.

**Why it is untested, and why that is fixable.** KFP serializes each `@dsl.component`
function body in isolation, so a component cannot call a module-level helper *defined in
components.py*. CLAUDE.md records that the broader reading of that rule is **false**, checked
2026-09-01: every component extracts the tarball, calls `sys.path.insert(0, "/app")`, and
imports from `wrangler` freely. It already does so 16 times.

So logic can move into a real module — and doing so **reduces** exposure to #13, because KFP
then serializes less.

### C2 · Two implementations of the same six stages, no guard

`orchestration/stages.py` (local) and `pipeline/components.py` (pipeline) both drive
deploy → eval → optimize → redeploy → eval against the same primitives:

| primitive | stages.py | components.py |
| --- | --- | --- |
| `health_gate` | 9 refs | 12 refs |
| `deploy_agent_from_source` | 3 | 3 |
| `run_batch_eval_averaged` | 2 | 2 |
| `update_agent_from_source` | 2 | 3 |

**Already cost two bugs:** `manifest.enabled_pairs` was honoured locally and not in the
pipeline, so a disabled pair still ran; and `skip_optimize` was pipeline-level only until
this week. Nothing tests that the two paths agree.

## WARNING

### W1 · Under-tested modules that touch cloud resources

| module | statements | coverage |
| --- | --- | --- |
| `orchestration/runner.py` | 268 | **19%** — "legacy", but still reachable from `cli.py:84` and `cli.py:641` |
| `pipeline/deploy_pipeline.py` | 153 | **20%** — builds images, uploads tarballs, submits jobs |
| `eval/online_monitors.py` | 87 | **21%** |
| `eval/online_evaluators.py` | 300 | **28%** |

`runner.py` is the sharpest: labelled legacy, 19% covered, and still wired into two CLI
commands. Either it is supported and should be tested, or it is not and should be retired.

### W2 · Ten functions over 190 lines

Beyond C1: `optimize` (`optimizer.py:587`, 333), `format_analysis_report`
(`analyzer.py:718`, 232), `build_pipeline` (`dag.py:47`, 225), `deploy_pipeline`
(`deploy_pipeline.py:282`, 200), `generate_comparison_report`
(`report_sections.py:669`, 199).

Length alone is not a defect — `build_pipeline` is a DAG declaration and reads fine
top-to-bottom. Flagged so the extraction in Phase 3 is aimed at the ones where length hides
*logic*, not the ones where it is just declarative.

## STYLE / SUGGESTION

- **S1 · `ty` is scoped to `wrangler/`.** Unscoped it reports 109 diagnostics, dominated by
  sibling-import resolution in `examples/` and `scripts/` that works at runtime via
  `sys.path`. **Decision: leave scoped**, and record it so it is not re-litigated — the
  earlier "21-diagnostic baseline" in this repo's notes was a stale memory of a differently
  scoped command.
- **S2 · `print()` in library modules** (`online_evaluators.py` 61, `stages.py` 42,
  `evaluator.py` 36). Deliberate — `T201` is ignored with the reason "print() is the CLI's
  output mechanism", and these are progress lines for multi-hour jobs. **No change.**

## Audited and NOT a finding — recorded so it is not re-audited

| area | state |
| --- | --- |
| **Hardcoded secrets / keys** | none. No API keys, no project ids, no SA addresses, no bucket names in `wrangler/` or `scripts/`. A `detect-secrets` pre-commit hook plus AST guards (`test_models.py`, `test_region_literals.py`) enforce it |
| **Config loading** | strict `.env` / registry discipline; `FALLBACK_REGION` is the single permitted region literal and a test enforces it |
| **Retry / backoff** | present in every cloud-calling module. `EVAL_MAX_RETRIES=16`, engine deletes pace at 8 s with quota-aware retry, KFP `set_retry` with exponential backoff added this week on idempotent tasks only |
| **File / network handles** | every `open()` in `wrangler/` is a `with`. Zero leaks found |
| **Broad `except`** | 36 occurrences, **zero bare `except:`**, and every one either logs, re-raises, or carries a comment explaining why it swallows |
| **Lint configuration** | `select = ["ALL"]` with each ignore individually justified in-file. Beyond standard practice |
| **Typing** | dataclasses throughout (14 modules) rather than Pydantic — a deliberate, consistent convention; `ty` clean on `wrangler/` |
| **IAM / least privilege** | engine deletion refuses anything without the ownership label; `prune` is dry-run by default and protects unlabelled, referenced, warm and trafficked engines |

---

# Phase 2 — Refactoring plan

Every task is TDD, one PR each, each independently revertible.

**Two rules that apply to every task touching `components.py` or `dag.py`:**

1. **Never merge under a live campaign driver.** KFP recompiles the spec from disk using
   import-time line numbers; merging under a driver once serialised one component's body
   under another's name and killed an arm (silent-failures #13). Check with
   `pgrep -af "run_campaign|run_experiment|deploy_pipeline"` first.
2. **Each PR busts that component's KFP cache.** Intended, but it means the next campaign
   re-runs that stage. Land them between campaigns.

## Task 1 — Drift guard for the two execution paths (C2)

**Files:** `tests/test_execution_path_drift.py` (new)

Follow `tests/test_shared_source_drift.py`, which already guards three hand-synced pairs by
**behaviour rather than source** — source comparison needs an allowlist that eventually lets
a real difference through.

1. **Failing test:** every manifest/pair key that `components.py` reads must also be read by
   `stages.py`, and vice versa. Parse both with `ast`, collect `pair.get("…")` /
   `pair["…"]` subscripts and `manifest.<attr>` accesses, and diff the sets.
2. Seed the allowlist with the keys that are *legitimately* one-sided, each with a reason
   (`engine_labels_json` is pipeline-only; there is no local equivalent).
3. Assert both paths read `enabled_pairs` rather than `pairs` — the first bug this guards.

**Why a test and not a refactor:** unifying the paths is the real fix, but it is the same
project as Task 2–4 and doing it first would mean a large change with no safety net.

## Task 2 — Extract `deploy_single_agent` (C1, smallest first)

**Files:** `wrangler/pipeline/_steps.py` (new) · `wrangler/pipeline/components.py:81-284` ·
`tests/test_pipeline_steps.py` (new)

The 204-line body splits cleanly: tarball extraction and env setup (infrastructure, stays
inline) vs. label merging, health gating and the stage-artifact payload (logic, extracts).

1. **Failing test** for `build_engine_labels(engine_labels_json)` — the function whose
   absence caused this week's label-stripping bug. Assert the ownership label is merged
   *under* the caller's and cannot be overwritten.
2. Create `_steps.py` with that function; component imports it after `sys.path.insert`.
3. Repeat for `deploy_stage_payload(...)` — the artifact dict, currently inline.
4. **Assert the component still parses** (`ast.parse` on `python_func`) and that the DAG
   compiles. A syntax error here fails nine hours in, not at import.

**Smallest first deliberately** — it proves the extraction pattern on the least dangerous
component before touching the 444-line one.

## Task 3 — Extract `redeploy_single_agent` (195 lines)

**Files:** `wrangler/pipeline/_steps.py` · `components.py:916-1110` · `tests/test_pipeline_steps.py`

Same shape. This is the component that lost the engine labels, so it gets the second slot:
the logic is now covered by a test that would have caught it.

## Task 4 — Extract `optimize_single_agent` (444 lines, the big one)

**Files:** `wrangler/pipeline/_steps.py` · `components.py:465-908` · `tests/test_pipeline_steps.py`

Extract in this order, each its own commit:

1. `resolve_agent_paths(pair, agent_module)` — the `_opt` directory and sampler-config
   resolution, currently ~20 inline lines with a silent fallback.
2. `control_arm_payload(pair, original_prompt)` — the short-circuit added this week.
3. `optimize_cost_summary(...)` — token estimation and cost arithmetic.
4. **Leave inline:** MCP server startup, the `try/finally` that uploads server logs, and the
   `_deferred_toolset_closes` window. These are process lifecycle, not logic, and moving them
   risks patch 8.

## Task 5 — Decide `runner.py` (W1)

**Files:** `wrangler/orchestration/runner.py` · `wrangler/cli.py:84,641`

19% coverage, labelled legacy, still reachable from two CLI commands. **Do not refactor it
blind.** Establish first whether either command is used:

1. Check whether `wrangler run` / the `--resume-from` path appears in any manifest, script,
   doc or campaign procedure.
2. If unused → deprecate with a warning that names the pipeline replacement, and open a
   follow-up to delete.
3. If used → it is not legacy, and the docstring saying so is the bug. Test the two paths
   the CLI actually reaches.

**Report the evidence either way** — "legacy" has been asserted in a docstring and never
re-checked.

## Task 6 — Record the `ty` scoping decision (S1)

**Files:** `CODE_STANDARDS.md`

One paragraph: `ty check wrangler/` is the gate, the 109 unscoped diagnostics are
sibling-import noise from `examples/` and `scripts/`, and the number is not a regression
baseline. This exists because a stale "21-diagnostic baseline" in this repo's notes cost real
time when an unscoped run reported 109 and looked like a regression.

---

# Phase 3 — Execution

Order: **1 → 6 → 2 → 3 → 4 → 5.**

Task 1 first (it is the safety net), Task 6 next (it is free), then the extractions from
smallest to largest, then the `runner.py` decision last because it may turn into a deletion
that is easier once the drift guard exists.

| task | hands-on | risk |
| --- | --- | --- |
| 1 · drift guard | 60–90 min | none — test only |
| 6 · ty decision | 15 min | none — docs |
| 2 · extract deploy | 90 min | low — busts deploy cache |
| 3 · extract redeploy | 90 min | low |
| 4 · extract optimize | 2–3 h | **highest** — the 444-line body; split across commits |
| 5 · runner.py decision | 60 min + findings | low |
| | **≈ 7–9 h** | |

## Verification

Per task, before the PR:

1. `uv run pytest tests/ -q` — **1433 passing today**, coverage ≥ 60 (the ratchet).
2. `uv run ruff format --check . && uv run ruff check .` clean.
3. `uv run ty check wrangler/` clean.
4. **`components.py` still parses and the DAG still compiles** —
   `ast.parse(inspect.getsource(comp.python_func))` and importing `wrangler.pipeline.dag`.
5. **Image tag unchanged** unless a dependency moved (`_compute_image_tag`).
6. `pgrep -af "run_campaign|run_experiment|deploy_pipeline"` empty before merging anything
   that touches `components.py` or `dag.py`.

End-to-end, once Tasks 2–4 are in: submit `manifests/pipeline_smoke_manifest.yaml`
(5 cases, ~25–30 min) and confirm all six stages still produce their artifacts. **Coverage on
`components.py` should move from 2% toward 40–50%** — the extracted logic is now reachable
by unit tests while the shells stay thin.

## Risks

- **`components.py` is the single most damaging file in the repo to get wrong.** Mitigated by
  extracting smallest-first, one stage per PR, with an `ast.parse` guard per task — and the
  net effect *reduces* the surface that silent-failures #13 acts on.
- **Cache busting is cumulative.** Four PRs touching component bodies means four cache
  invalidations. Land them in one window between campaigns, not spread across a month.
- **Task 4 could disturb patch 8.** The `_deferred_toolset_closes` window and the MCP
  lifecycle are explicitly out of scope; `tests/test_toolset_close_deferral.py` must stay
  green and `TestTheFixReachesProduction` already asserts `optimize()` opens the window.
- **The drift guard may be noisy on first run.** Expect a real allowlist. A key that is
  one-sided for a good reason gets a comment, not a silent exemption.
