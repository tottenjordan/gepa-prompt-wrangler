# Log query cookbook

Copy-paste queries for a pipeline run in this repo. Substitute `$GCP_PROJECT_ID` from `.env`
— never paste a real project id into a committed file.

## The two resource types, and picking the wrong one wastes an hour

| What you want | Filter |
| --- | --- |
| **Worker container logs** — what the code printed | `resource.type="ml_job"` |
| **Orchestration events** — task start/stop, state changes | `resource.type="aiplatform.googleapis.com/PipelineJob"` |
| Deployed agent logs (GEAP) | `resource.type="aiplatform.googleapis.com/ReasoningEngine"` |

**Worker logs live in `jsonPayload.message`, not `textPayload`.** Querying `textPayload`
with a pipeline-run display name returns **zero rows on a healthy run**, which reads exactly
like a clean result. That has produced a wrong "all clear" here before. The numeric
`job_id` — not the pipeline name — is the key that works.

## Two flags that are not optional

**`--freshness`.** `gcloud logging read` defaults to **1 day** and truncates silently. That
default once turned 29 real toolset losses into a reported zero. Pass `--freshness=90d` on
anything historical.

**`resource.labels.job_id` in `--format`.** Each `ParallelFor` arm runs in its own container
with its own `job_id`. Without it you cannot attribute a line to an arm.

## Find the numeric job id

It is not the pipeline run name. Take it from the task error message, or:

```bash
gcloud logging read \
  'resource.type="aiplatform.googleapis.com/PipelineJob" AND "<PIPELINE_RUN_ID>"' \
  --project="$GCP_PROJECT_ID" --limit=50 --freshness=90d --format=json --order=asc
```

Parse `jsonPayload.payload.taskName`, `state`, `startTime`, `endTime`.

## Milestones for one arm

```bash
gcloud logging read \
  'resource.type="ml_job" AND resource.labels.job_id="<JOB_ID>"' \
  --project="$GCP_PROJECT_ID" --limit=1000 --freshness=90d --order=asc \
  --format="value(timestamp,resource.labels.job_id,jsonPayload.message)"
```

If messages come back empty — common for lightweight components — drop `--format` and read
the raw JSON to see which field is populated:

```bash
gcloud logging read 'resource.type="ml_job" AND resource.labels.job_id="<JOB_ID>"' \
  --project="$GCP_PROJECT_ID" --limit=10 --freshness=90d --format=json --order=desc
```

## Greps that matter in this repo

| Looking for | Pattern |
| --- | --- |
| **Toolset loss** (silent failure #12) | `will run without the tools` **and** `Failed to get tools from toolset` — ADK reworded at 2.8.0; matching one spelling read zero through a live campaign |
| Toolset teardown | `Closing toolset` — **does not imply a real close since patch 8**; the deferral still logs it |
| Patch 8 engaged | `Deferred N toolset close(s)` — **N=0 means it never ran** |
| GEPA progress | `Generation N: evaluating candidate` / `Generation N: scored in` |
| Judge rate limiting | `429` / `RESOURCE_EXHAUSTED` — `gemini-3.5-flash` is 5 RPM |
| Merge attempts | `No merge candidates found` — expected; merge is structurally inert here |
| Health gate | `reach` / `gate` in the deploy stage output |
| Empty-stream defect | a 200 with no inference — see silent-failures |

**Beware greps contaminated by your own arm name.** Counting `merge` on an arm called
`m01-rationale-merge` returned 229 hits against a true count of 19. Anchor on the full
phrase.

## Stage artifacts are authoritative, the console is not

```bash
gsutil ls "gs://$GCP_STAGING_BUCKET/pipeline-runs/<RUN_ID>/stages/"
gsutil cat "gs://$GCP_STAGING_BUCKET/pipeline-runs/<RUN_ID>/stages/eval_after.json" | head -40
```

Also uploaded per run, and worth knowing about:

- `stages/optimize/mcp_logs/<pair>-<server>.log` — the local MCP servers' own output.
  Added because campaign 07's toolset losses were undiagnosable while these went to
  `DEVNULL`.
- `stages/optimize/gepa_run/<arm>/` — GEPA's `run_dir`, including `gepa_state.bin` with
  per-candidate scores. Only on runs after 2026-09-16.

## Analysis scripts, rather than eyeballing

```bash
# Toolset losses with the close-burst band analysis. --freshness is required.
uv run python scripts/analyze_toolset_loss.py --job-id <JOB_ID> --freshness 90d

# Per-metric deltas classified against this run's control arm
uv run python .claude/skills/campaign-result-report/scripts/summarize_arm_metrics.py \
    experiments/active/<name>
```
