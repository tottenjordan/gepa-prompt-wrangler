---
name: inspecting-pipeline-runs
description: Use when asked to report on, diagnose, or interpret a Vertex pipeline run or DOE campaign arm in this repo — a run that is in flight, stuck, failed with an unhelpful error, or finished and needs its results read.
---

# Inspecting pipeline runs

## Overview

A pipeline run has four evidence layers, and each one answers a question the
others cannot. Reading fewer than all four produces a report that is confidently
wrong.

| layer | answers | where |
| --- | --- | --- |
| task states | what stage is it on | `PipelineJob.gca_resource.job_detail.task_details` |
| stage artifacts | are the numbers usable | `gs://$BUCKET/pipeline-runs/<run>/stages/` |
| worker logs | is it actually working, and on what | `resource.type="ml_job"` |
| orchestration | is this arm alone or part of a batch | `scripts/run_campaign.py` |

**A SUCCEEDED stage is not a correct stage, and an empty-looking log is almost
never empty.** Most of the cost in this repo's history has come from believing
one of those two things.

## The sweep

**1. Task states.** Get them from the Python SDK, not REST or `gcloud ai` —
`gcloud ai pipeline-jobs` does not exist in this gcloud, and hand-rolled REST
wastes a round of auth and URL-encoding for nothing.

```python
from google.cloud import aiplatform
from scripts.run_campaign import state_name          # NOT str(state)
j = aiplatform.PipelineJob.get(resource_name=f"projects/{P}/locations/{R}/pipelineJobs/{JOB}")
```

**2. Stage artifacts — read them even when the run failed.** They are written by
different components than the one that failed, so they usually survive it. The
2026-09-02 noise floor was reconstructed entirely from artifacts of a run whose
analysis stage died.

Job name → prefix: `gepa-run-8a5905dee0-20260908-021742` → `pipeline-runs/run-8a5905dee0/`
(strip `gepa-`, strip the timestamp).

Always pull `deploy/*.json` (`health.passed`, `health.rate`, `engine_id`,
`model`) and `eval_*/*.json` (`cases_scored`/`cases_total`, `scores`).

**3. Worker logs.** The `job_id` is a *custom job* id, not the pipeline job id
and not in `task_details` directly. Get it from the running task, or find it by
searching for the pipeline name:

```python
t.execution.metadata          # look for the customJob resource name
```
```bash
gcloud logging read 'resource.type="ml_job" AND jsonPayload.message:"<pipeline-run-id>"' \
  --format="value(resource.labels.job_id)" --limit=1
```

One component can emit ~14k log lines. Filter server-side with
`jsonPayload.message:"<pattern>"` and post-process in Python; do not page
through raw output.

**4. Orchestration.** Before calling anything missing, check how the campaign is
*supposed* to run. See "Do not raise a false alarm" below.

## Gotchas that have each cost real time

| symptom | cause | fix |
| --- | --- | --- |
| "the logs are empty" | content is in `jsonPayload.message`, not `textPayload` — measured 184/200 vs 4/200 non-empty | filter and format on `jsonPayload.message` |
| terminal job polls forever | `str(state)` is the bare ordinal (`'3'`), not `PIPELINE_STATE_RUNNING` | `state.name`, or `state_name()` |
| ERROR rows that are not errors | tqdm progress bars go to stderr | read message content; do not trust `severity>=WARNING` |
| stage SKIPPED unexpectedly | KFP cache hit — component body and inputs unchanged | expected; but check *what* it reused |
| `gcloud ai pipeline-jobs` not found | does not exist in this gcloud | use the Python SDK |

## Verify the stage did what it claims

A stage reports SUCCEEDED for completing, not for being correct. For each one,
find independent evidence of the thing that could silently be wrong:

- **deploy** — does the engine serve the manifest's model? `deploy/*.json`
  `model`, and the engine's own `[GEAP startup] model ...` log line.
- **optimize** — is GEPA calling that model? Look for
  `publishers/anthropic/models/<id>:rawPredict` in the worker log. Two arms
  sharing an agent module will silently optimize the module's pinned default
  unless the manifest's model is threaded through.
- **optimize, tool health** — grep `will run without the tools`. A tool-using
  agent evaluated with an empty toolset scores near zero on tool use and
  corrupts the objective GEPA is optimizing. This is the single most-missed
  check; it was missed on 2026-09-08 while five occurrences were in the log.
  Do not rely on the run's own degradation summary for this:
  `_ToolsetFailureCounter` matches `"Failed to get tools from toolset"`, and on
  2026-09-08 the container emitted `"will run without the tools"` instead --
  0 counted against 5 real. Grep both strings.
- **eval** — `cases_scored/cases_total` on *both* sides. A delta across a
  coverage gap measures dropout, not the prompt.
- **the container's own dependencies.** `Dockerfile.pipeline` installs
  `google-adk>=2.2.0` **unpinned**, while `deploy.py` pins `==2.7.1` for the
  agent. Any change to `pyproject.toml`/`uv.lock`/`Dockerfile.pipeline` moves
  the image tag and triggers a rebuild, which can silently pull a newer ADK
  into the container where all five monkey-patches run. CLAUDE.md's rule --
  "do NOT add or remove patches without re-running the per-patch probe" --
  is equally violated by the *dependency* moving underneath them.

  Check it. A traceback in the log gives line numbers inside
  `site-packages/google/adk/...`; compare them to the local install:

  ```python
  import google.adk, pathlib
  pathlib.Path(google.adk.__file__).parent / "tools/mcp_tool/mcp_toolset.py"
  ```

  Different line numbers for the same function means the container is on a
  different version than the patches were verified against. Found this way on
  2026-09-08: container `get_tools` at line 501 vs local 2.7.1 at 536.

## Interpret, do not just relay

- **Coverage before scores.** Every measurement problem in this repo traces
  back to it. Report it beside every number.
- **Quote the gate verdict, not its existence.** `health.passed: false` with the
  eval running anyway is a finding, not a footnote.
- **Base an ETA on measured rates**, from this run or a comparable historical
  one. Note GEPA does **not** log a running metric-call tally against
  `max_metric_calls`, so that count is not observable — fall back to generation
  cadence (`Generation N: scored in Xs`), completed `Evaluation run completed`
  events, or a comparable past run's optimize duration. Say which basis you
  used, and give a range rather than a point.
- **Say what cannot be concluded yet.** A delta with no control arm is
  uncalibrated; say so rather than reporting it as a result.

## Do not raise a false alarm

Before reporting that an arm, control, or pairing is "missing", read
`scripts/run_campaign.py` and `scripts/validate_then_run.py`.

A campaign submits **one validation arm first** and releases the remaining
batches only when it reaches SUCCEEDED. A baseline reviewer on 2026-09-08 saw
one job, found no control and no paired arm, and reported the campaign design
violated — when both were scheduled and gated behind the arm it was looking at.

"Not yet submitted" and "missing" are different claims. Check which one applies.

## Common mistakes

- Reporting task states as the whole status.
- Concluding "no logs" from an empty `textPayload`.
- Trusting the manifest for what a stage is *doing* rather than checking.
- Skipping artifacts because the run failed.
- Giving an ETA with no measured basis.
- Calling scheduled-but-not-yet-run work missing.
