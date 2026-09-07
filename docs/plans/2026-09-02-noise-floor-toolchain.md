# Build the floor toolchain, then run Campaign 06 into it

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-02-noise-floor-toolchain.md` and commit it — plan mode could only write to the scratch plan path.

**Goal:** Make the pipeline able to finish, and turn the floor analysis from ad-hoc arithmetic into tested code — so that when Campaign 06 runs, reading it is a function call rather than a throwaway script.

**Architecture:** Four code tasks, all completable and testable *before* any campaign runs, validated against the validation arm's real artifacts which already exist in GCS. Campaign 06 is then an operational step that feeds the finished toolchain, and two data-driven follow-ups close it out.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`, Vertex AI Managed Pipelines (KFP v2).

---

## Context

The Campaign 06 validation arm (`gepa-run-3aa99b8293-20260902-125228`) reached further than any previous attempt — `deploy`, `eval_before`, and the control `eval_after` all SUCCEEDED, and `condition-5` was correctly NOT_TRIGGERED. Only `generate-analysis-2` failed.

Two results came out of it anyway, from artifacts that survived:

**Coverage is 100%** — 64/64 on both sides, against 88% before. `EVAL_MAX_RETRIES = 16` worked, and this is the project's first floor measurement not confounded by dropout.

**The floor at `num_runs: 1`,** prompt byte-identical on both sides, so every number is noise:

| metric | unpaired Δ | paired Δ |
| --- | --- | --- |
| hallucination_v1 | **−0.0747** | −0.0420 |
| final_response_quality_v1 | −0.0554 | −0.0197 |
| safety_v1 | −0.0104 | +0.0000 |
| tool_use_quality_v1 | −0.0050 | −0.0070 |
| instruction_following_v1 | −0.0028 | +0.0050 |

Pairing reduces the floor by 44–64% where CLAUDE.md claims ~15%, and **all five deltas are negative** where noise should scatter in sign. Both need more arms before they mean anything.

### Why this plan is code-first

I computed that table with a throwaway script. Doing that again for four arms is how a number ends up in CLAUDE.md that nobody can reproduce. Every piece of analysis Campaign 06 needs can be written and tested **now**, because the validation arm's artifacts are a real fixture sitting in GCS.

That also removes a six-hour blocking wait from the middle of the plan: Task 1 unblocks the campaign, the campaign runs in the background, and Tasks 2–4 are built while it does.

### Why `generate_analysis` gets instrumented, not fixed

The cause is unknown, and three submissions have died there with empty `ml_job` worker logs. Everything cheap is ruled out: `_try_paperbanana` (`wrangler/reporting/charts.py:122-164`) already falls back to matplotlib per chart; `normalize_agent_keys` maps `c06-ctrl-claude-n1` → `sonnet` correctly and `reporter.py:602` has an `if not ordered` fallback; and **running the identical code path locally against the run's own artifacts succeeds.** The failure is environmental to the container. No amount of source reading will find it — the next failure has to be made legible instead.

What the failure costs is the real defect. `generate_analysis` runs: build `results` → `generate_report(...)` ← throws → build `summary_data` → upload reports → upload `summary.json`. A chart renderer takes down the run's summary. The measurement is the artifact; the report is a rendering of it.

---

## Task 1: Make the analysis component survive rendering, and say why it failed

**Files:** Modify `wrangler/pipeline/components.py` (`generate_analysis`) · Test `tests/test_pipeline.py`

**Step 1 — write the failing test.** The component body cannot be executed by a unit test (KFP serializes it in isolation), so assert on structure, as `test_no_component_reads_the_optimize_stage_unguarded` already does in this file.

```python
class TestAnalysisSurvivesARenderingFailure:
    """A chart renderer must not be able to destroy the run's summary.

    generate_analysis called generate_report first and wrote summary.json
    fifth, so when the reporter raised in the pipeline container -- three
    submissions running -- the summary, the uploaded report and the per-pair
    totals went with it. The eval artifacts survived only because a different
    component writes them, which is the sole reason the 2026-09-02 floor could
    be computed by hand.
    """

    def _body(self) -> str:
        from pathlib import Path
        src = Path("wrangler/pipeline/components.py").read_text()
        return src[src.index("def generate_analysis"):]

    def test_the_summary_is_written_before_the_report(self):
        b = self._body()
        assert b.index("summary.json") < b.index("generate_report("), (
            "summary.json must be uploaded before the reporter runs"
        )

    def test_the_reporter_call_is_guarded(self):
        import ast
        b = self._body()
        tree = ast.parse("def _f():\n" + "\n".join("    " + l for l in b.splitlines()))
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and node.handlers:
                if any(getattr(c.func, "id", "") == "generate_report"
                       for c in ast.walk(node) if isinstance(c, ast.Call)):
                    return
        raise AssertionError("generate_report must run inside a try/except")

    def test_a_failure_writes_a_traceback_artifact(self):
        b = self._body()
        assert "traceback" in b and "analysis_error" in b, (
            "three submissions died here with empty ml_job logs; the traceback "
            "must land in GCS next to the artifacts"
        )
```

**Step 2 — run it, watch all three fail:**
`uv run pytest tests/test_pipeline.py::TestAnalysisSurvivesARenderingFailure -q --no-cov`

**Step 3 — implement.** Move `summary_data` construction and its `summary.json` upload *above* the `generate_report(...)` call, then:

```python
    try:
        generate_report(results, experiment_name, use_paperbanana=True)
        for local_file in reports_dir.rglob("*"):
            if local_file.is_file():
                rel = local_file.relative_to(reports_dir)
                gcs_bucket.blob(f"pipeline-runs/{run_id}/reports/{rel}").upload_from_filename(
                    str(local_file)
                )
    except Exception:
        # Never lose the diagnostic. `ml_job` worker logs came back empty for
        # all three failures here, so the traceback goes where the next person
        # will actually look -- beside the artifacts.
        import traceback
        tb = traceback.format_exc()
        gcs_bucket.blob(
            f"pipeline-runs/{run_id}/reports/analysis_error.txt"
        ).upload_from_string(tb, content_type="text/plain")
        logging.error("analysis rendering failed; saved analysis_error.txt\n%s", tb)
```

Exit **zero** after saving the traceback — the summary and eval artifacts are complete and a chart must not block a campaign — but log loudly, and record that choice in a comment rather than leaving it to inference.

**Step 4 — verify:** `uv run pytest tests/ -q --no-cov` (991+ green), `ruff check`, `ruff format`, `ty check wrangler/`.

**Step 5 — commit:** subject `fix: a chart renderer could destroy the run's summary`. Body states the ordering bug, the three submissions with empty logs, and that the root cause is still unknown and expected to surface in the next run's `analysis_error.txt`.

---

## Task 2: A tested floor calculation, replacing the throwaway script

**Files:** Modify `wrangler/reporting/analyzer.py` · Test `tests/test_noise_floor.py` (new) · Fixture `tests/fixtures/c06_validation/{eval_before,eval_after}.json`

The arithmetic done by hand on 2026-09-02 becomes a function. This is what makes Campaign 06's four arms readable without a scratch script, and what makes the number in CLAUDE.md reproducible.

**Step 1 — capture the fixture.** Copy the validation arm's two eval artifacts from
`gs://<bucket>/pipeline-runs/run-3aa99b8293/stages/eval_{before,after}/c06-ctrl-claude-n1.json`
into `tests/fixtures/c06_validation/`. Real data, so the test cannot drift from what the pipeline actually emits.

**Step 2 — write the failing test.** Assert the exact values already computed by hand, so the function is pinned to a verified result rather than to itself:

```python
def test_the_validation_arm_floor_matches_the_hand_computation():
    before, after = _load_fixture()
    f = floor_from_control_arm(before, after)
    assert f["coverage"] == {"before": 1.0, "after": 1.0}
    assert f["unpaired"]["hallucination_v1"] == pytest.approx(-0.0747, abs=5e-4)
    assert f["paired"]["hallucination_v1"] == pytest.approx(-0.0420, abs=5e-4)
    assert f["floor"] == pytest.approx(0.0747, abs=5e-4)   # max |unpaired|
```

Also test: a metric absent from one side is skipped, not treated as zero; a case present in only one side is excluded from the paired set but counted in coverage; and an empty `per_case` yields `{}` rather than a divide-by-zero.

**Step 3 — implement `floor_from_control_arm(before, after) -> dict`** in `analyzer.py`, returning per-metric `unpaired` and `paired` deltas, `coverage` for both sides, the paired-set size, and a scalar `floor`. Reuse the existing `average_per_case` conventions; do not reimplement case pairing.

**Step 4 — extend `noise_floor()` deliberately.** It currently returns a single scalar `max(|delta|)` across all control metrics (`analyzer.py`, above `classify_deltas`). Campaign 06 produces **per-metric** floors. Add a per-metric return path; keep the scalar as a documented conservative fallback rather than silently changing what existing callers get.

**Step 5 — verify and commit.** Subject: `feat: the noise floor is a function, not a script`.

---

## Task 3: `minimum_detectable_effect`, wired into delta classification

**Files:** Modify `wrangler/reporting/analyzer.py` (`classify_deltas`, `analyzer.py:212`) · Test `tests/test_noise_floor.py`

This is Campaign 06's stated payoff in `docs/doe/06-pipeline-noise-floor.md`, and it is what stops a reader having to remember a constant.

**Step 1 — failing test.** MDE falls as `num_runs` rises; a delta below the floor classifies `within-noise` whatever its sign; `floor=None` still yields `uncalibrated` (the existing contract — `None` must never be coerced to `0.0`, which would assert there is no noise).

**Step 2 — implement `minimum_detectable_effect(num_runs, n_cases)`.** Derive it from the measured floor rather than assuming √n — Campaign 06 is testing whether √n even holds, so the function must not presuppose the answer. Take the floor as a parameter.

**Step 3 — wire into `classify_deltas`** so a per-metric floor is used when available and the scalar otherwise.

**Step 4 — verify and commit.** Subject: `feat: classify a delta against a measured floor, not a remembered constant`.

---

## Task 4: The drift sign-test, pre-registered before there is data

**Files:** Create `docs/analysis/2026-09-02-eval-order-drift.md` · Modify `wrangler/reporting/analyzer.py` · Test `tests/test_noise_floor.py`

**Campaign 07 is gated on this.** Its entire output is before/after deltas per tier; a systematic downward drift understates every arm's improvement by roughly the drift size, which is the same order as the effects 07 exists to measure.

**Step 1 — write the pre-registration first, before any campaign data exists.** That ordering is the point.

- **H₀:** the sign of `after − before` is random per metric per arm.
- **The five metrics are not independent** — all score the same 64 responses via the same autorater — so a binomial p-value over 20 metric-arm cells overstates significance. **The arms are the independent units.** With four arms the strongest honest claim is 4/4 arms drifting the same way; say so rather than quoting p over 20 cells.
- **Decision rule, fixed now:** all four arms negative on a majority of metrics → treat as real, escalate to Step 4. Signs scatter across arms → the validation arm was a coincidence; record the null and unblock campaign 07.
- **Known limitation:** `per_case` rows carry only `case_index` and scores — **no timestamps** — so nothing in these artifacts can correlate drift with wall-clock. State it rather than implying the analysis could have.

**Step 2 — failing test** for `drift_sign_summary(arms) -> dict`: counts negative/positive per arm and across arms, and reports the per-arm majority direction.

**Step 3 — implement and commit.** Subject: `feat: a sign test for eval-order drift, registered before the data`.

**Step 4 — the discriminating experiment, only if Step 1's rule fires.** Run one control arm with the two evals **swapped in time**, so the arm labelled `eval_after` executes first. If the *earlier* eval scores higher regardless of label → temporal or engine-state effect. If the *`before`-labelled* eval scores higher regardless of order → the two code paths differ, which is a defect rather than a measurement property. Needs a manifest and a small DAG change; scope only if reached.

---

## After the toolchain: run Campaign 06

Not a code task — an operation, run once Task 1 lands. It can start in the background while Tasks 2–4 are built.

```bash
uv run python scripts/validate_then_run.py --campaign 06
```

Config is already correct: `cache_bust: "c06-v3"`, `skip_optimize: true`, `health_gate.required: true`, `max_rerolls` 4. The validation arm auto-releases both batches only on SUCCEEDED. Batches are `claude-n1 ‖ gemini-n1` then `claude-n3 ‖ gemini-n3`, paired across publishers for separate quota pools. **~6 h.**

**n=5 stays trimmed** (decided): two points cannot distinguish √n from any other decreasing curve, so the write-up reports that rather than fitting a trend. Manifests are retained if a third point is later wanted.

Then, in order:

1. **Check for `analysis_error.txt`** in each run's `reports/` prefix. If Task 1's guard fired, this is the diagnostic three submissions failed to produce — fix the cause before trusting the reports.
2. **Confirm `health.passed: true`** in every arm's deploy artifact. With `required: true` a failing arm should not exist, so a *present* arm with `passed: false` is itself a bug.
3. **Run the Task 2–4 functions** over the four arms. Fill in `docs/doe/06-pipeline-noise-floor.md`'s Result section against its own pre-registered table — per-metric, paired and unpaired, replicates reported before pooling, coverage attached to every number.
4. **Correct `CLAUDE.md:232-236`.** Both figures there predate the `average_per_case` fix and were measured through dropout that no longer exists: "~0.059 at `num_runs: 1` and ~0.034 at 3", and "pairing … helps only by ~15%". Replace with measured values, dated, saying what changed and why the old ones were wrong. If pairing really is worth 44–64%, state it as guidance — it is a cheaper lever than raising `num_runs`, the opposite of what the current text implies.
5. **Tear down.** `wrangler engines prune` protects engines with recent traffic, so campaign engines usually need deleting by id with pacing, as Campaign 09 did.

---

## Verification

1. `uv run pytest tests/ -q --no-cov` green throughout; `ruff check`, `ruff format --check`, `ty check wrangler/` clean.
2. **Task 1's guard is seen to fire.** Temporarily make `generate_report` raise, confirm `summary.json` is still written and `analysis_error.txt` appears, revert. A guard never observed firing is not known to work.
3. **Task 2 reproduces the hand computation** to within 5e-4 on the committed fixture — that is the whole point of pinning it to real data.
4. **Coverage near 100% on both sides of every arm**; any arm whose sides differ by more than ~10 points is reported with that caveat, not pooled.
5. **Both evals of each control arm used a byte-identical prompt** — diff the recorded prompts rather than trusting `skip_optimize`. The measurement rests entirely on it.
6. Task 4's conclusion is written down **before** campaign 07 is scheduled, whichever way it goes.

## Risks

- **A fifth defect in the eval-only path.** Four submissions, four distinct failures. Task 1's traceback capture is the mitigation: the next failure costs one run to understand rather than one run to guess.
- **The drift is real and interacts with optimization.** Control arms cannot see that — they have no optimize stage. If Step 4 confirms an order effect, campaign 07 needs a design change, not a constant correction.
- **Two points cannot test √n.** Required in the write-up; drawing a line through two points is exactly what the pre-registration warns against.
- **The fixture pins Task 2 to one arm's quirks.** Mitigate by also testing synthetic edge cases — missing metrics, one-sided cases, empty `per_case` — not just the real file.

## Out of scope

- Campaigns 07 and 08 — 07 gated on Task 4, 08 on 07.
- Restoring Campaign 06's n=5 batch (decided against; manifests retained).
- The four deferred PR #29 follow-ups: promoting `_filter_pairs`, `blended_cost`'s identical unguarded subscript, the cross-process tier-ownership gap, and the `Experiment.pair_ids` rename.
- Filing the empty-stream escalation.
